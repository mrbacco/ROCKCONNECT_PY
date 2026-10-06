# File: importer.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Import upcoming music events from outside services, so "gigs near you" works in any city.

Providers (docs/EVENT-IMPORT.md has the terms of each): Ticketmaster (worldwide where they sell tickets),
Skiddle (UK and Ireland), Songkick (worldwide, keys on application), PredictHQ (worldwide, paid with a trial) and Bandsintown (a band's own page only).

Two ways events arrive:
  * ON DEMAND (default): when someone searches "near me" in a place nobody searched before, the rounded ~28 km
    area around it is fetched once and remembered for IMPORT_COVERAGE_HOURS (ensure_coverage). The first request
    gets a quick fill, a background thread finishes big cities. Only the ROUNDED AREA is sent to the providers,
    never the exact position and never who searched.
  * SCHEDULED: `flask import-events` (cron) keeps the areas in IMPORT_AREAS fresh, and the Bandsintown pages
    of connected bands.

Providers only allow event data to be stored "for reasonable periods", so events that are over, that the provider
no longer lists, or that were not refreshed for IMPORT_MAX_AGE_HOURS are deleted.
"""
import json
import math
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from . import geo, providers, ratelimit
from .baclog import bac_log
from .db import commit, execute, external_events, insert, rollback
from .util import TIME_FORMAT, now_str

TICKETMASTER_URL = "https://app.ticketmaster.com/discovery/v2/events.json"
PAGE_SIZE = 200
MAX_PAGES = 5            # the API refuses deep paging: page size x page number must stay under 1000
MAX_SEARCHES = 10        # at most this many consecutive searches per area (a safety stop, 10 x 1000 events)
PAUSE_SECONDS = 0.3      # the free key allows 5 requests per second

Area = namedtuple("Area", "name lat lon radius_km")


class ImportFailed(Exception):
    """Something the operator should be told about (missing key, provider refused, bad configuration)."""

    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status   # the HTTP status when the provider answered with one


# ------------------------------------------------------------------ configuration
def parse_areas(text):
    """'London=51.5072,-0.1276,40; Dublin=53.35,-6.26,30' -> [Area, ...] (name=lat,lon,radius_km)."""
    areas, seen = [], set()
    for chunk in (text or "").split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            name, rest = chunk.split("=", 1)
            lat, lon, radius = (float(part) for part in rest.split(","))
        except ValueError:
            raise ImportFailed("Bad IMPORT_AREAS entry %r: write it as Name=latitude,longitude,radius_km" % chunk)
        name = name.strip()[:60]
        if not name or name.lower() in seen:
            raise ImportFailed("IMPORT_AREAS needs a different non-empty name for each area (%r)" % chunk)
        if not geo.valid_coords(lat, lon) or not 1 <= radius <= 500:
            raise ImportFailed("IMPORT_AREAS entry %r: coordinates must be real and the radius 1-500 km" % chunk)
        seen.add(name.lower())
        areas.append(Area(name, lat, lon, radius))
    return areas


# ------------------------------------------------------------------ talking to Ticketmaster
KEY_SETTINGS = {"Ticketmaster": "TICKETMASTER_API_KEY", "Skiddle": "SKIDDLE_API_KEY", "Songkick": "SONGKICK_API_KEY",
                "PredictHQ": "PREDICTHQ_API_KEY", "Bandsintown": "the app id of the band"}


def _http_get_json(url, provider="Ticketmaster", timeout=20, headers=None):
    """GET a URL and parse the JSON. Errors never contain the URL or headers: they hold the API key."""
    request = urllib.request.Request(url, headers={
        "User-Agent": "rockconnect/1.0 (%s)" % current_app.config["CONTACT_EMAIL"], "Accept": "application/json",
        **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise ImportFailed("%s refused the API key (HTTP %d). Check %s." % (
                provider, exc.code, KEY_SETTINGS.get(provider, "the key")), exc.code)
        if exc.code == 429:
            raise ImportFailed("%s says we asked too often (HTTP 429). Try again later." % provider, 429)
        raise ImportFailed("%s answered HTTP %d." % (provider, exc.code), exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ImportFailed("Could not reach %s (%s)." % (provider, type(exc).__name__))


def normalise_ticketmaster(event):
    """One Discovery API event -> our row, or None when it cannot be used (no date, no venue position...)."""
    try:
        start = event["dates"]["start"]
        if start.get("dateTBA") or start.get("dateTBD"):
            return None
        day = datetime.strptime(start["localDate"], "%Y-%m-%d").strftime("%Y-%m-%d")
        venue = event["_embedded"]["venues"][0]
        lat = geo.parse_coord(venue["location"]["latitude"], 90)
        lon = geo.parse_coord(venue["location"]["longitude"], 180)
        external_id, title = str(event["id"]), str(event["name"]).strip()
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        return None
    if lat is None or lon is None or not title or not external_id:
        return None
    local_time = start.get("localTime") or ""
    time_known = bool(local_time) and not start.get("timeTBA") and not start.get("noSpecificTime")
    genre = None
    for classification in event.get("classifications") or []:
        name = ((classification or {}).get("genre") or {}).get("name")
        if name and name.lower() != "undefined":
            genre = str(name)[:60]
            break
    url = event.get("url") or ""
    return {
        "source": "ticketmaster", "external_id": external_id[:64], "title": title[:255],
        "venue": str(venue.get("name") or "")[:160], "city": str((venue.get("city") or {}).get("name") or "")[:120],
        "event_at": "%s %s" % (day, local_time[:5] if time_known else "00:00"), "time_known": int(time_known),
        "latitude": round(lat, 5), "longitude": round(lon, 5),
        "ticket_url": url[:600] if url.startswith("https://") else None,   # never a javascript: or http: link
        "genre": genre,
    }


_GEOHASH_ALPHABET = "0123456789bcdefghjkmnpqrstuvwxyz"


def geohash(lat, lon, precision=9):
    """The standard geohash of a position (what Ticketmaster's newer `geoPoint` parameter expects)."""
    lat_range, lon_range = [-90.0, 90.0], [-180.0, 180.0]
    result, bits, bit_count, use_lon = "", 0, 0, True
    while len(result) < precision:
        rng, value = (lon_range, lon) if use_lon else (lat_range, lat)
        middle = (rng[0] + rng[1]) / 2
        bits <<= 1
        if value >= middle:
            bits |= 1
            rng[0] = middle
        else:
            rng[1] = middle
        use_lon = not use_lon
        bit_count += 1
        if bit_count == 5:
            result += _GEOHASH_ALPHABET[bits]
            bits, bit_count = 0, 0
    return result


def _ticketmaster_url(area, api_key, start, end, page, size, mode):
    """The request URL for events starting between `start` and `end`. `mode` picks how the area is described:
    'latlong' (documented, but marked deprecated) or 'geoPoint' (a geohash, the replacement)."""
    params = {
        "apikey": api_key, "classificationName": "music", "radius": int(area.radius_km), "unit": "km",
        "size": size, "page": page, "sort": "date,asc",
        "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if mode == "geoPoint":
        params["geoPoint"] = geohash(area.lat, area.lon)
    else:
        params["latlong"] = "%.4f,%.4f" % (area.lat, area.lon)
    return TICKETMASTER_URL + "?" + urllib.parse.urlencode(params)


def _get_page(area, api_key, start, end, page, size, mode, timeout=None):
    """One page of results. If Ticketmaster rejects the 'latlong' form (HTTP 400) it is asked again the
    'geoPoint' way. Returns (data, the mode that worked)."""
    options = {"timeout": timeout} if timeout else {}
    try:
        return _http_get_json(_ticketmaster_url(area, api_key, start, end, page, size, mode), **options), mode
    except ImportFailed as exc:
        if exc.status == 400 and mode == "latlong":
            bac_log("import", "Ticketmaster rejected 'latlong', trying 'geoPoint'")
            return _http_get_json(_ticketmaster_url(area, api_key, start, end, page, size, "geoPoint"), **options), "geoPoint"
        raise


def _start_instant(event):
    """When the event starts (UTC), from the raw API event, or None."""
    try:
        return datetime.strptime(event["dates"]["start"]["dateTime"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def fetch_ticketmaster(area, api_key, days, max_pages=MAX_PAGES, stats=None, timeout=None, max_searches=MAX_SEARCHES):
    """Yield normalised events around an area, soonest first.

    One search returns at most MAX_PAGES x PAGE_SIZE events (a limit of the API). A busy city has more than
    that, so when a search is cut off the next one starts where the last event of the previous one began,
    until the whole period is covered. Events seen twice at the seam are yielded once.
    """
    stats = stats if stats is not None else {}
    max_pages = min(max_pages, MAX_PAGES)
    mode, seen = "latlong", set()
    start = datetime.now(timezone.utc)
    end = start + timedelta(days=days)
    for search in range(max_searches):
        cut_off, last_start = False, None
        for page in range(max_pages):
            data, mode = _get_page(area, api_key, start, end, page, PAGE_SIZE, mode, timeout)
            for event in (data.get("_embedded") or {}).get("events") or []:
                instant = _start_instant(event)
                if instant and (last_start is None or instant > last_start):
                    last_start = instant
                item = normalise_ticketmaster(event)
                if item and item["external_id"] not in seen:
                    seen.add(item["external_id"])
                    yield item
            if page + 1 >= int((data.get("page") or {}).get("totalPages") or 1):
                break
            cut_off = page + 1 == max_pages      # more results exist than this search was allowed to read
            time.sleep(PAUSE_SECONDS)
        if not cut_off or last_start is None or last_start <= start:
            if cut_off:
                stats["truncated"] = True        # asked to stop early (quick fill): more exists
            return                               # everything was read, or no progress is possible
        start = last_start
    stats["truncated"] = True                    # the safety limit on consecutive searches was reached




# ------------------------------------------------------------------ the providers
Provider = namedtuple("Provider", "name label key_setting fetch")


def _fetch_ticketmaster(area, key, days, max_pages, stats, timeout):
    # the quick fill reads one search only; a full import may continue with more searches (see fetch_ticketmaster)
    return fetch_ticketmaster(area, key, days, max_pages=max_pages, stats=stats, timeout=timeout,
                              max_searches=1 if max_pages < MAX_PAGES else MAX_SEARCHES)


def _fetch_skiddle(area, key, days, max_pages, stats, timeout):
    return providers.fetch_skiddle(area, key, days, lambda *a, **k: _http_get_json(*a, **k),
                                   max_pages=max_pages * 2, stats=stats, timeout=timeout)


def _fetch_songkick(area, key, days, max_pages, stats, timeout):
    return providers.fetch_songkick(area, key, days, lambda *a, **k: _http_get_json(*a, **k),
                                    max_pages=max_pages * 2, stats=stats, timeout=timeout)


def _fetch_predicthq(area, key, days, max_pages, stats, timeout):
    return providers.fetch_predicthq(area, key, days, lambda *a, **k: _http_get_json(*a, **k),
                                     max_pages=max_pages * 2, stats=stats, timeout=timeout)


# searched in this order; when two list the same gig the first one wins (see api._remove_duplicates)
PROVIDERS = {
    "ticketmaster": Provider("ticketmaster", "Ticketmaster", "TICKETMASTER_API_KEY", _fetch_ticketmaster),
    "skiddle": Provider("skiddle", "Skiddle", "SKIDDLE_API_KEY", _fetch_skiddle),
    "songkick": Provider("songkick", "Songkick", "SONGKICK_API_KEY", _fetch_songkick),
    "predicthq": Provider("predicthq", "PredictHQ", "PREDICTHQ_API_KEY", _fetch_predicthq),
}
LABELS = {name: p.label for name, p in PROVIDERS.items()}
LABELS["bandsintown"] = "Bandsintown"


def enabled_providers(cfg=None):
    """The location-searching providers that have a key."""
    cfg = cfg or current_app.config
    return [p for p in PROVIDERS.values() if cfg.get(p.key_setting)]


def check(name="ticketmaster"):
    """Ask a provider one tiny question to see whether the key works. Returns (ok, message). Uses one API call."""
    cfg = current_app.config
    provider = PROVIDERS[name]
    key = cfg.get(provider.key_setting)
    if not key:
        return False, "%s is not set." % provider.key_setting
    try:
        area = (parse_areas(cfg["IMPORT_AREAS"]) or [Area("London", 51.5072, -0.1276, 40)])[0]
        now = datetime.now(timezone.utc)
        if name == "ticketmaster":
            data, _ = _get_page(area, key, now, now + timedelta(days=cfg["IMPORT_DAYS_AHEAD"]), 0, 1, "latlong")
            total = int((data.get("page") or {}).get("totalElements") or 0)
            return True, "Ticketmaster accepted the key: %d upcoming concerts within %d km of %s." % (
                total, area.radius_km, area.name)
        # the others: fetch one small page and make sure the answer can be read (their field names are not
        # all documented, so say so loudly if they differ from what the parser expects)
        stats = {}
        items = list(provider.fetch(area, key, 30, 1, stats, 15))[:5]
        if not items:
            return True, ("%s accepted the key but returned no readable events for %s in the next 30 days "
                          "(normal outside its coverage; if you expected some, the answer format may differ)." % (
                              provider.label, area.name))
        return True, "%s accepted the key and %d event(s) were read, e.g. %r." % (
            provider.label, len(items), items[0]["title"])
    except ImportFailed as exc:
        return False, str(exc)


# ------------------------------------------------------------------ storing
def _store(item, area, run_start, user_id=None):
    """Insert a new event or refresh an existing one. Returns 'new' or 'updated'."""
    row = execute("SELECT id FROM external_events WHERE source = :s AND external_id = :e",
                  s=item["source"], e=item["external_id"]).fetchone()
    values = dict(item, area=area.name, seen_at=run_start, user_id=user_id)
    if row is None:
        insert(external_events, imported_at=run_start, **values)
        return "new"
    execute("UPDATE external_events SET title = :title, venue = :venue, city = :city, event_at = :event_at,"
            " time_known = :time_known, latitude = :latitude, longitude = :longitude, ticket_url = :ticket_url,"
            " genre = :genre, area = :area, seen_at = :seen_at, user_id = :user_id WHERE id = :id", id=row[0], **values)
    return "updated"


def _forget_unlisted(source, area_name, run_start):
    """Listed before in this area but not any more (cancelled, moved): they go too. Returns how many."""
    return execute("DELETE FROM external_events WHERE source = :s AND area = :a AND seen_at < :t",
                   s=source, a=area_name, t=run_start).rowcount or 0


def prune():
    """Forget events that are over or have not been refreshed for IMPORT_MAX_AGE_HOURS. Returns how many."""
    now = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
    stale = (now - timedelta(hours=current_app.config["IMPORT_MAX_AGE_HOURS"])).strftime(TIME_FORMAT)
    removed = execute("DELETE FROM external_events WHERE event_at < :y OR seen_at < :s", y=yesterday, s=stale).rowcount
    execute("DELETE FROM import_coverage WHERE fetched_at < :s", s=stale)      # forgotten data must be fetched again
    commit()
    from . import social    # imported here: social.py imports modules that import this one
    social.prune_attendances()     # the small copies of imported gigs kept with "I am going"
    return removed or 0


def _hidden_ids(source):
    """External ids of this provider that an admin hid."""
    return {r[0] for r in execute("SELECT external_id FROM hidden_events WHERE source = :s", s=source)}


def _import_area(provider, area, dry_run=False, max_pages=None, timeout=None):
    """Fetch one area from one provider and store it. Returns a result dict; never raises ImportFailed."""
    cfg = current_app.config
    counts = {"area": area.name, "provider": provider.name, "new": 0, "updated": 0, "removed": 0, "error": None,
              "fetched": 0, "truncated": False, "hidden": 0}
    run_start = now_str()
    stats = {}
    try:
        # all pages first: a half-read area is never stored
        fetched = list(provider.fetch(area, cfg[provider.key_setting], cfg["IMPORT_DAYS_AHEAD"],
                                      max_pages or MAX_PAGES, stats, timeout))
        counts["fetched"] = len(fetched)
        counts["truncated"] = bool(stats.get("truncated"))
        hidden = _hidden_ids(provider.name)
        if hidden:
            kept = [item for item in fetched if item["external_id"] not in hidden]
            counts["hidden"], fetched = len(fetched) - len(kept), kept
        if not dry_run:
            for number, item in enumerate(fetched, 1):
                counts[_store(item, area, run_start)] += 1
                if number % COMMIT_EVERY == 0:
                    commit()          # short transactions: other requests are not kept waiting for the database
            if not counts["truncated"]:
                counts["removed"] = _forget_unlisted(provider.name, area.name, run_start)
            commit()
    except ImportFailed as exc:
        counts["error"] = str(exc)
    bac_log("import", "%s / %s: %s" % (provider.name, area.name, counts))   # no API key, no URLs in the log
    return counts


# ------------------------------------------------------------------ bands that connect their own Bandsintown page
def connected_artists(user_id=None):
    """Active bands that gave their Bandsintown artist name and their own app id."""
    sql = ("SELECT id, username, bandsintown_artist, bandsintown_app_id FROM users WHERE kind = 'band'"
           " AND status = 'active' AND COALESCE(bandsintown_artist, '') <> ''"
           " AND COALESCE(bandsintown_app_id, '') <> ''")
    if user_id is not None:
        return execute(sql + " AND id = :u", u=user_id).mappings().fetchall()
    return execute(sql).mappings().fetchall()


def import_artist(artist, dry_run=False, timeout=None):
    """Fetch the upcoming dates of one connected band. `artist` is a row from connected_artists()."""
    area = Area("artist:%d" % artist["id"], 0, 0, 0)
    counts = {"area": area.name, "provider": "bandsintown", "new": 0, "updated": 0, "removed": 0, "error": None,
              "fetched": 0, "truncated": False}
    run_start = now_str()
    try:
        fetched = list(providers.fetch_bandsintown_artist(
            artist["bandsintown_artist"], artist["bandsintown_app_id"],
            lambda *a, **k: _http_get_json(*a, **k), timeout=timeout))
        counts["fetched"] = len(fetched)
        if not dry_run:
            for item in fetched:
                counts[_store(item, area, run_start, user_id=artist["id"])] += 1
            counts["removed"] = _forget_unlisted("bandsintown", area.name, run_start)
            commit()
    except ImportFailed as exc:
        counts["error"] = str(exc)
    bac_log("import", "bandsintown / %s: %s" % (area.name, counts))
    return counts


# ------------------------------------------------------------------ the scheduled import
def run(only=None, dry_run=False, provider=None):
    """Import the areas of IMPORT_AREAS from every provider that has a key, and the connected Bandsintown pages.

    Returns a list of result dicts (one per provider and area). An area that fails is reported and skipped; the
    others still run. Raises ImportFailed for problems that stop everything (nothing configured at all).
    """
    cfg = current_app.config
    areas = parse_areas(cfg["IMPORT_AREAS"])
    if only:
        areas = [a for a in areas if a.name.lower() == only.lower()]
        if not areas:
            raise ImportFailed("No area called %r in IMPORT_AREAS." % only)
    chosen = enabled_providers(cfg)
    if provider:
        if provider != "bandsintown" and provider not in PROVIDERS:
            raise ImportFailed("Unknown provider %r. Choose from: %s, bandsintown." % (provider, ", ".join(PROVIDERS)))
        chosen = [p for p in chosen if p.name == provider]
    artists = [] if (only or (provider and provider != "bandsintown")) else connected_artists()
    if not chosen and not artists:
        raise ImportFailed("No event provider is set up. Put a free key in the .env file: TICKETMASTER_API_KEY "
                           "(https://developer.ticketmaster.com, worldwide) and/or SKIDDLE_API_KEY (UK and Ireland).")
    if chosen and not areas and not artists:
        raise ImportFailed("IMPORT_AREAS is empty. Example: IMPORT_AREAS=\"London=51.5072,-0.1276,40\" "
                           "(not needed for on-demand search, see docs/EVENT-IMPORT.md).")

    results = []
    for p in chosen:
        for area in areas:
            results.append(_import_area(p, area, dry_run))
    for artist in artists:
        results.append(import_artist(artist, dry_run))
    if not dry_run:
        prune()
    return results


# ------------------------------------------------------------------ on demand: any city in the world
CELL_DEGREES = 0.25           # ~28 km: searches are snapped to this grid before anything is sent to a provider
QUICK_PAGES = 1               # the quick fill reads one page (the 200 soonest events) so the first search answers fast;
                              # the rest of a big city is read in the background
ONDEMAND_TIMEOUT = 8          # seconds per request during a member's search; the background job is not limited
COMMIT_EVERY = 100
ERROR_RETRY = timedelta(minutes=10)
CLAIM_EXPIRES = timedelta(minutes=3)


def cell_for(lat, lon):
    """(centre latitude, centre longitude, cell id) of the grid square containing a position."""
    clat = max(-90.0, min(90.0, round(round(lat / CELL_DEGREES) * CELL_DEGREES, 4))) + 0.0   # + 0.0: no "-0.00"
    clon = max(-180.0, min(180.0, round(round(lon / CELL_DEGREES) * CELL_DEGREES, 4))) + 0.0
    return clat, clon, "%.2f,%.2f" % (clat, clon)


def coverage_radius(radius_km):
    """How far around the cell centre to fetch so that a search of `radius_km` from anywhere in the cell is covered."""
    return int(min(100, max(25, math.ceil(radius_km) + 20)))


def _age(stamp):
    return datetime.now(timezone.utc) - datetime.strptime(stamp, TIME_FORMAT).replace(tzinfo=timezone.utc)


def _claim(provider, cell, radius):
    """Reserve the right to fetch this (provider, cell) if it is due. Returns True for exactly one caller, so
    several members searching the same new city at once cause a single fetch."""
    hours = timedelta(hours=current_app.config["IMPORT_COVERAGE_HOURS"])
    row = execute("SELECT radius_km, status, fetched_at FROM import_coverage WHERE provider = :p AND cell = :c",
                  p=provider, c=cell).fetchone()
    if row is not None:
        age = _age(row[2])
        if row[1] == "error":
            due = age >= ERROR_RETRY
        elif row[1] == "running":
            due = age >= CLAIM_EXPIRES
        else:
            due = row[0] < radius or age >= hours
        if not due:
            return False
        won = execute("UPDATE import_coverage SET status = 'running', radius_km = :r, fetched_at = :now"
                      " WHERE provider = :p AND cell = :c AND fetched_at = :old",
                      r=max(radius, row[0]), now=now_str(), p=provider, c=cell, old=row[2]).rowcount
        commit()
        return bool(won)
    try:
        execute("INSERT INTO import_coverage (provider, cell, radius_km, status, fetched_at)"
                " VALUES (:p, :c, :r, 'running', :now)", p=provider, c=cell, r=radius, now=now_str())
        commit()
        return True
    except IntegrityError:
        rollback()
        return False


def _finish(provider, cell, status):
    execute("UPDATE import_coverage SET status = :s, fetched_at = :now WHERE provider = :p AND cell = :c",
            s=status, now=now_str(), p=provider, c=cell)
    commit()


def _complete_in_background(app, provider, area):
    """A big city did not fit in the quick fill: read the rest without keeping anyone waiting."""
    def work():
        with app.app_context():
            try:
                result = _import_area(provider, area)
                _finish(provider.name, area.name[len("auto:"):], "error" if result["error"] else "ok")
            except Exception as exc:  # a background job must never die noisily
                bac_log("import", "background import failed (%s)" % type(exc).__name__)

    threading.Thread(target=work, daemon=True).start()


def ensure_coverage(lat, lon, radius_km):
    """Make sure the area around a search has been fetched from the providers. Returns the providers fetched now.

    Called by the nearby search, which must never fail because of it (the caller swallows every problem).
    Only the centre of the ~28 km grid square goes further from here: the exact position is not used again.
    """
    cfg = current_app.config
    if not cfg["IMPORT_ON_DEMAND"]:
        return []
    if secrets.randbelow(20) == 0:
        prune()      # sites that only fetch on demand never run the scheduled import: clean up now and then
    clat, clon, cell = cell_for(lat, lon)
    radius = coverage_radius(radius_km)
    done = []
    for provider in enabled_providers(cfg):
        if ratelimit.blocked("ondemand_site", "all"):
            bac_log("import", "on-demand fetches are at their hourly limit for this site; using what is stored")
            break
        if not _claim(provider.name, cell, radius):
            continue
        ratelimit.hit("ondemand_site", "all")
        area = Area("auto:" + cell, clat, clon, radius)
        result = _import_area(provider, area, max_pages=QUICK_PAGES, timeout=ONDEMAND_TIMEOUT)
        if result["error"]:
            _finish(provider.name, cell, "error")
            continue
        if result["truncated"]:
            _finish(provider.name, cell, "partial")
            _complete_in_background(current_app._get_current_object(), provider, area)  # type: ignore[attr-defined]
        else:
            _finish(provider.name, cell, "ok")
        done.append(provider.name)
    return done
