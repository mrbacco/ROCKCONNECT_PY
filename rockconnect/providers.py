# File: providers.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Event providers besides Ticketmaster: Skiddle, Songkick, PredictHQ and Bandsintown.

Each provider turns the answer of its API into the same small dictionary (see importer.normalise_ticketmaster):
source, external_id, title, venue, city, event_at, time_known, latitude, longitude, ticket_url, genre.

What each one can do (details and terms in docs/EVENT-IMPORT.md):
  Skiddle      search by position and radius, UK and Ireland. Free key, apply at skiddle.com/api/join.php.
  Songkick     search by position, worldwide. Keys are given out by application only.
  PredictHQ    search by position and radius, worldwide. Paid service with a free trial; the key is a bearer token.
               It describes events (venue, time, expected attendance) but has no ticket links.
  Bandsintown  NO search by place. It lists the events of one artist, and its terms say it is for artists (or people
               acting for them) with their own app id. So a band connects its own page; see fetch_bandsintown_artist.

`get` is the HTTP function handed in by importer.py (it hides the API key in error messages and is replaced in
tests), called as get(url, provider="Skiddle", timeout=...).
"""
import math
import re
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import geo

SKIDDLE_URL = "https://www.skiddle.com/api/v1/events/search/"
SONGKICK_URL = "https://api.songkick.com/api/3.0/events.json"
PREDICTHQ_URL = "https://api.predicthq.com/v1/events/"
BANDSINTOWN_URL = "https://rest.bandsintown.com/artists/%s/events"

SKIDDLE_PAGE = 100       # the API's maximum
SONGKICK_PAGE = 50       # the API's maximum
PREDICTHQ_PAGE = 100
PAUSE_SECONDS = 0.3


def _text(value, limit):
    return str(value or "").strip()[:limit]


def _https(url):
    """Only https links are ever offered as a ticket link (never javascript: or plain http:)."""
    return url if isinstance(url, str) and url.startswith("https://") else None


def _clock(text):
    """'20:30', '20:30:00' or '2026-10-12 20:30:00' -> '20:30', else None."""
    match = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)", str(text or ""))
    return "%02d:%s" % (int(match.group(1)), match.group(2)) if match else None


def _position(lat, lon):
    """A usable (lat, lon) or None. (0, 0) is the 'unknown' value many feeds use, so it is refused."""
    lat, lon = geo.parse_coord(lat, 90), geo.parse_coord(lon, 180)
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    return round(lat, 5), round(lon, 5)


def _first_name(items):
    if not isinstance(items, (list, tuple)):
        return None   # a plain string would otherwise be read letter by letter
    for item in items:
        name = item.get("name") if isinstance(item, dict) else item
        if name:
            return str(name)[:60]
    return None


# ------------------------------------------------------------------ Skiddle (UK and Ireland)
def normalise_skiddle(event):
    """One Skiddle event -> our row, or None. Written defensively: the field names are read leniently."""
    try:
        venue = event.get("venue") or {}
        title = _text(event.get("eventname") or event.get("name"), 255)
        raw_date = str(event.get("startdate") or event.get("date") or "")
        day = datetime.strptime(raw_date[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        external_id = str(event["id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    where = _position(venue.get("latitude"), venue.get("longitude"))
    if not title or where is None:
        return None
    opening = event.get("openingtimes") if isinstance(event.get("openingtimes"), dict) else {}
    clock = _clock(raw_date[10:]) or _clock(opening.get("doorsopen"))
    return {
        "source": "skiddle", "external_id": external_id[:64], "title": title,
        "venue": _text(venue.get("name"), 160), "city": _text(venue.get("town") or venue.get("city"), 120),
        "event_at": "%s %s" % (day, clock or "00:00"), "time_known": int(clock is not None),
        "latitude": where[0], "longitude": where[1], "ticket_url": _https(event.get("link"))
        and event["link"][:600], "genre": _first_name(event.get("genres")),
    }


def fetch_skiddle(area, api_key, days, get, max_pages=10, stats=None, timeout=None):
    """Yield normalised live-music events around an area (Skiddle measures the radius in miles)."""
    from .importer import ImportFailed
    stats = stats if stats is not None else {}
    now = datetime.now(timezone.utc)
    for page in range(max_pages):
        query = urllib.parse.urlencode({
            "api_key": api_key, "latitude": "%.4f" % area.lat, "longitude": "%.4f" % area.lon,
            "radius": max(1, math.ceil(area.radius_km / 1.609344)), "eventcode": "LIVE", "order": "distance",
            "minDate": now.strftime("%Y-%m-%d"), "maxDate": (now + timedelta(days=days)).strftime("%Y-%m-%d"),
            "limit": SKIDDLE_PAGE, "offset": page * SKIDDLE_PAGE, "description": 0})
        data = get(SKIDDLE_URL + "?" + query, provider="Skiddle", **({"timeout": timeout} if timeout else {}))
        if str(data.get("error")) not in ("0", "False", "None", ""):
            raise ImportFailed("Skiddle answered with an error: %s" % _text(data.get("errormessage") or data.get("error"), 120))
        results = data.get("results") or []
        for event in results:
            item = normalise_skiddle(event)
            if item:
                yield item
        total = int(data.get("totalcount") or 0)
        if len(results) < SKIDDLE_PAGE or (page + 1) * SKIDDLE_PAGE >= total:
            return
        time.sleep(PAUSE_SECONDS)
    stats["truncated"] = True


# ------------------------------------------------------------------ Songkick (worldwide, key on application)
def normalise_songkick(event):
    try:
        start, venue = event.get("start") or {}, event.get("venue") or {}
        day = datetime.strptime(str(start.get("date") or "")[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        external_id, title = str(event["id"]), _text(event.get("displayName"), 255)
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    # the venue's own position only: the 'location' of an event is the centre of its city, which would put it
    # in the wrong street
    where = _position(venue.get("lat"), venue.get("lng"))
    if not title or where is None:
        return None
    clock = _clock(start.get("time"))
    return {
        "source": "songkick", "external_id": external_id[:64], "title": title,
        "venue": _text(venue.get("displayName"), 160), "city": _text((event.get("location") or {}).get("city"), 120),
        "event_at": "%s %s" % (day, clock or "00:00"), "time_known": int(clock is not None),
        "latitude": where[0], "longitude": where[1], "ticket_url": _https(event.get("uri")) and event["uri"][:600],
        "genre": None,
    }


def fetch_songkick(area, api_key, days, get, max_pages=10, stats=None, timeout=None):
    """Yield normalised events around an area. Songkick has no radius: it answers for the metro area of the point."""
    from .importer import ImportFailed
    stats = stats if stats is not None else {}
    now = datetime.now(timezone.utc)
    for page in range(1, max_pages + 1):
        query = urllib.parse.urlencode({
            "apikey": api_key, "location": "geo:%.4f,%.4f" % (area.lat, area.lon),
            "min_date": now.strftime("%Y-%m-%d"), "max_date": (now + timedelta(days=days)).strftime("%Y-%m-%d"),
            "per_page": SONGKICK_PAGE, "page": page})
        data = get(SONGKICK_URL + "?" + query, provider="Songkick", **({"timeout": timeout} if timeout else {}))
        results_page = data.get("resultsPage") or {}
        if results_page.get("status") not in (None, "ok"):
            raise ImportFailed("Songkick answered with an error: %s" % _text(
                (results_page.get("error") or {}).get("message"), 120))
        for event in (results_page.get("results") or {}).get("event") or []:
            item = normalise_songkick(event)
            if item:
                yield item
        if page * int(results_page.get("perPage") or SONGKICK_PAGE) >= int(results_page.get("totalEntries") or 0):
            return
        time.sleep(PAUSE_SECONDS)
    stats["truncated"] = True


# ------------------------------------------------------------------ PredictHQ (worldwide, paid with a free trial)
GENERIC_LABELS = {"concert", "concerts", "music", "live", "live music", "festival"}


def _phq_local(stamp, tz_name, lon):
    """PredictHQ gives UTC. Returns (local day, local 'HH:MM' or None). Without the time-zone database (a plain Windows
    Python) the day is estimated from the longitude and no time of day is claimed."""
    when = datetime.strptime(str(stamp)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    try:
        local = when.astimezone(ZoneInfo(str(tz_name)))
    except (ZoneInfoNotFoundError, ValueError, TypeError, KeyError):
        local = when + timedelta(hours=round(lon / 15.0))
        return local.strftime("%Y-%m-%d"), None
    clock = local.strftime("%H:%M")
    return local.strftime("%Y-%m-%d"), None if clock == "00:00" else clock     # midnight means 'time not known'


def normalise_predicthq(event):
    """One PredictHQ concert -> our row, or None. Needs a named venue: without one the position is a city centre."""
    try:
        external_id, title = str(event["id"]), _text(event.get("title"), 255)
        if event.get("private") or str(event.get("state") or "active") not in ("active", "predicted"):
            return None
        lon, lat = event["location"][0], event["location"][1]
        where = _position(lat, lon)
        venue = next((e for e in event.get("entities") or [] if isinstance(e, dict) and e.get("type") == "venue"), None)
        if not title or where is None or venue is None or not venue.get("name"):
            return None
        day, clock = _phq_local(event["start"], event.get("timezone"), where[1])
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        return None
    address = (event.get("geo") or {}).get("address") or {}
    labels = [t for t in event.get("labels") or [] if isinstance(t, str) and t.lower() not in GENERIC_LABELS]
    return {
        "source": "predicthq", "external_id": external_id[:64], "title": title,
        "venue": _text(venue.get("name"), 160), "city": _text(address.get("locality"), 120),
        "event_at": "%s %s" % (day, clock or "00:00"), "time_known": int(clock is not None),
        "latitude": where[0], "longitude": where[1], "ticket_url": None,
        "genre": labels[0].replace("-", " ")[:60] if labels else None,
    }


def fetch_predicthq(area, api_key, days, get, max_pages=10, stats=None, timeout=None):
    """Yield normalised concerts around an area. Pages are followed through the answer's own `next` address, but only
    if it points at PredictHQ (the key is sent with every request)."""
    from .importer import ImportFailed
    stats = stats if stats is not None else {}
    now = datetime.now(timezone.utc)
    url = PREDICTHQ_URL + "?" + urllib.parse.urlencode({
        "category": "concerts", "within": "%dkm@%.4f,%.4f" % (max(1, math.ceil(area.radius_km)), area.lat, area.lon),
        "active.gte": now.strftime("%Y-%m-%d"), "active.lte": (now + timedelta(days=days)).strftime("%Y-%m-%d"),
        "sort": "start", "limit": PREDICTHQ_PAGE})
    headers = {"Authorization": "Bearer " + api_key}
    for _ in range(max_pages):
        data = get(url, provider="PredictHQ", headers=headers, **({"timeout": timeout} if timeout else {}))
        if not isinstance(data, dict) or data.get("error"):
            raise ImportFailed("PredictHQ answered with an error: %s" % _text(
                data.get("error") if isinstance(data, dict) else "unreadable answer", 120))
        for event in data.get("results") or []:
            item = normalise_predicthq(event)
            if item:
                yield item
        url = data.get("next")
        if not url:
            return
        if not str(url).startswith(PREDICTHQ_URL):
            raise ImportFailed("PredictHQ sent a next-page address that is not theirs; stopped.")
        time.sleep(PAUSE_SECONDS)
    stats["truncated"] = True


# ------------------------------------------------------------------ Bandsintown (one artist, with the artist's own app id)
def normalise_bandsintown(event, artist):
    try:
        venue = event.get("venue") or {}
        stamp = str(event["datetime"])
        day = datetime.strptime(stamp[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        external_id = str(event["id"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    where = _position(venue.get("latitude"), venue.get("longitude"))
    if where is None:
        return None
    clock = _clock(stamp[10:])
    ticket = None
    for offer in event.get("offers") or []:
        if isinstance(offer, dict) and str(offer.get("type", "")).lower() == "tickets" and _https(offer.get("url")):
            ticket = offer["url"][:600]
            break
    city = ", ".join(p for p in (venue.get("city"), venue.get("region")) if p)
    return {
        "source": "bandsintown", "external_id": external_id[:64], "title": _text(event.get("title") or artist, 255),
        "venue": _text(venue.get("name"), 160), "city": _text(city, 120),
        "event_at": "%s %s" % (day, clock or "00:00"), "time_known": int(clock is not None),
        "latitude": where[0], "longitude": where[1],
        "ticket_url": ticket or (_https(event.get("url")) and event["url"][:600]), "genre": None,
    }


def bandsintown_url(artist, app_id):
    """Artist names are part of the address; a '/' inside one would break it, so such names are not accepted."""
    return BANDSINTOWN_URL % urllib.parse.quote(artist, safe="") + "?" + urllib.parse.urlencode(
        {"app_id": app_id, "date": "upcoming"})


def fetch_bandsintown_artist(artist, app_id, get, timeout=None):
    """Yield the upcoming events of one artist, using that artist's own app id."""
    data = get(bandsintown_url(artist, app_id), provider="Bandsintown", **({"timeout": timeout} if timeout else {}))
    if not isinstance(data, list):
        return   # {"errors": ...} or "Not found": the artist has nothing listed
    for event in data:
        item = normalise_bandsintown(event, artist)
        if item:
            yield item
