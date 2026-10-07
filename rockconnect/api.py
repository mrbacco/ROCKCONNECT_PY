# File: api.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""JSON API, version 1 (signed-in members, same cookie as the website). See docs/API.md.

GET /api/v1/meta           public: API version, branding and which features this site has (for apps)
GET /api/v1/gigs/nearby    upcoming gigs close to a position, nearest first: gigs announced by members
                           plus gigs imported from the event providers
The social endpoints (going, who is going, people search, my gigs, lists) are in social.py, same prefix.

The version is in the URL on purpose: an installed mobile app cannot be force-updated, so what /api/v1
answers must not change in a way that breaks it. Add fields freely; to change or remove one, make /api/v2.
"""
import functools
import math
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, g, jsonify, request, url_for

from . import __version__, geo, gigmatch, importer, ratelimit, taxonomy
from .baclog import bac_log
from .db import execute
from .feed import POST_SELECT, VISIBLE_AUTHOR
from .util import event_label, event_time

API_VERSION = 1
bp = Blueprint("api", __name__, url_prefix="/api/v1")

DEFAULT_RADIUS_KM, MAX_RADIUS_KM = 25.0, 500.0
DEFAULT_DAYS, MAX_DAYS = 90, 365
DEFAULT_LIMIT, MAX_LIMIT = 50, 100
SOURCES = ("all", "community", "external")


def api_login_required(view):
    """Like login_required, but always answers in JSON (an API client does not want a redirect page)."""
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            return jsonify(error="session_expired", code="session_expired",
                           login_url=url_for("auth.signin")), 401
        return view(**kwargs)

    return wrapped


class ApiError(Exception):
    """A request the API refuses; turned into {"error": ..., "code": ...} by the handler below."""

    def __init__(self, message: str, code: str, status: int = 400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


@bp.app_errorhandler(ApiError)
def api_error(exc: ApiError):
    return jsonify(error=exc.message, code=exc.code), exc.status


def _float_param(name: str, default: float, low: float, high: float) -> float:
    """A numeric query parameter clamped to [low, high]."""
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ApiError("%s must be a number." % name, "bad_" + name)
    if math.isnan(value):
        raise ApiError("%s must be a number." % name, "bad_" + name)
    return max(low, min(high, value))


def _int_param(name: str, default: int, low: int, high: int) -> int:
    """A whole-number query parameter clamped to [low, high]."""
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError("%s must be a whole number." % name, "bad_" + name)
    return max(low, min(high, value))


def _center() -> tuple[float, float]:
    """Where to search around: ?lat=&lon=, else ?q=<place name>, else ?profile=1 (the member's profile location)."""
    lat = geo.parse_coord(request.args.get("lat"), 90)
    lon = geo.parse_coord(request.args.get("lon"), 180)
    if request.args.get("lat") or request.args.get("lon"):
        if lat is None or lon is None:
            raise ApiError("lat must be -90..90 and lon -180..180.", "bad_coordinates")
        return lat, lon
    place = request.args.get("q", "").strip()
    if not place and request.args.get("profile") == "1":
        place = (g.user["location"] or "").strip()
        if not place:
            raise ApiError("Add a location to your profile first, or send lat and lon.", "no_profile_location")
    if not place:
        raise ApiError("Send lat and lon, or q=<place name>, or profile=1.", "location_required")
    ratelimit.allow("geocode_user", g.user["id"])   # this one calls the geocoding service
    try:
        found = geo.geocode(place)
    except geo.GeocoderUnavailable:
        raise ApiError("Searching by town name is not available right now. Use your current location instead.",
                       "place_lookup_unavailable", 503)
    if found is None:
        raise ApiError("We could not find that place on the map.", "place_not_found", 404)
    return found


def _community_gigs(lat, lon, radius, box, since, until):
    """Gigs announced by members (posts with a date and a position)."""
    min_lat, max_lat, min_lon, max_lon = box
    sql = (POST_SELECT + " AND p.mod_state = 'ok' AND p.event_at IS NOT NULL AND p.event_at >= :since AND p.event_at <= :until"
           " AND p.latitude BETWEEN :min_lat AND :max_lat")
    params = {"me": g.user["id"], "since": since, "until": until, "min_lat": min_lat, "max_lat": max_lat}
    if min_lon is not None:
        sql += " AND p.longitude BETWEEN :min_lon AND :max_lon"
        params.update(min_lon=min_lon, max_lon=max_lon)
    for row in execute(sql, **params).mappings():
        distance = geo.distance_km(lat, lon, row["latitude"], row["longitude"])
        if distance <= radius:
            yield {
                "source": "community", "id": row["id"], "ref": str(row["id"]), "genre_key": row["genre"],
                "title": row["author_name"],
                "url": url_for("feed.single_post", post_id=row["id"]), "ticket_url": None,
                "body": row["body"][:300], "event_at": row["event_at"], "event_label": event_time(row["event_at"]),
                "place": row["event_place"], "latitude": row["latitude"], "longitude": row["longitude"],
                "distance_km": round(distance, 1), "has_photo": bool(row["image_filename"]), "genre": None,
                "attribution": None,
                "author": {"id": row["author_id"], "username": row["author"], "name": row["author_name"],
                           "kind": row["author_kind"]},
            }


def _external_events(lat, lon, radius, box, since, until):
    """Gigs imported from the providers. Those of a band's own Bandsintown page belong to that band, so they are
    hidden when the band is suspended or blocked, like its posts."""
    min_lat, max_lat, min_lon, max_lon = box
    sql = ("SELECT e.*, u.username AS author_username, u.name AS author_name, u.kind AS author_kind"
           " FROM external_events e LEFT JOIN users u ON u.id = e.user_id"
           " WHERE e.event_at >= :since AND e.event_at <= :until AND e.latitude BETWEEN :min_lat AND :max_lat"
           " AND (e.user_id IS NULL OR (" + VISIBLE_AUTHOR + "))")
    params = {"me": g.user["id"], "since": since, "until": until, "min_lat": min_lat, "max_lat": max_lat}
    if min_lon is not None:
        sql += " AND e.longitude BETWEEN :min_lon AND :max_lon"
        params.update(min_lon=min_lon, max_lon=max_lon)
    for row in execute(sql, **params).mappings():
        distance = geo.distance_km(lat, lon, row["latitude"], row["longitude"])
        if distance <= radius:
            band = ({"id": row["user_id"], "username": row["author_username"], "name": row["author_name"],
                     "kind": row["author_kind"]} if row["user_id"] else None)
            yield {
                "source": row["source"], "id": row["id"], "ref": row["external_id"],
                "genre_key": taxonomy.genre_key(row["genre"]), "title": row["title"],
                "url": url_for("events.event_page", event_id=row["id"]), "ticket_url": row["ticket_url"],
                "body": row["genre"] or "", "event_at": row["event_at"],
                "event_label": event_label(row["event_at"], row["time_known"]),
                "place": ", ".join(p for p in (row["venue"], row["city"]) if p) or None,
                "latitude": row["latitude"], "longitude": row["longitude"], "distance_km": round(distance, 1),
                "has_photo": False, "genre": row["genre"], "author": band,
                "attribution": importer.LABELS.get(row["source"], row["source"]),
            }


# the same concert listed twice is recognised in gigmatch.py (also used for 'who is going')
_same_gig = gigmatch.same_gig
_remove_duplicates = gigmatch.remove_duplicates


def _admin_hint():
    """Only for admins, when a search finds nothing: say why the site has no gigs to show (None for everyone else)."""
    if g.user["role"] != "admin":
        return None
    cfg = current_app.config
    if not importer.enabled_providers(cfg):
        return ("Admin hint: no event provider is set up, so only gigs announced by members can be found. Put a free "
                "TICKETMASTER_API_KEY (and optionally SKIDDLE_API_KEY) in the .env file, then restart "
                "(see docs/EVENT-IMPORT.md).")
    if not cfg["IMPORT_ON_DEMAND"] and (execute("SELECT count(*) FROM external_events").scalar() or 0) == 0:
        return ("Admin hint: on-demand import is switched off (IMPORT_ON_DEMAND=0) and nothing was imported yet. "
                "Run: flask --app wsgi import-events")
    names = ", ".join(p.label for p in importer.enabled_providers(cfg))
    return ("Admin hint: %s listed nothing here. Each provider covers only some countries (Ticketmaster: not France, "
            "Japan or India; Skiddle: UK and Ireland only). Add SONGKICK_API_KEY or PREDICTHQ_API_KEY for worldwide coverage "
            "(docs/EVENT-IMPORT.md); members can also announce gigs themselves." % names)


@bp.route("/meta")
def meta():
    """Public, no sign-in: lets an app show the right name and colour and know what this server can do."""
    cfg = current_app.config
    sources = [p.label for p in importer.enabled_providers(cfg)]
    return jsonify(
        api_version=API_VERSION, server_version=__version__,
        site={"name": cfg["SITE_NAME"], "tagline": cfg["SITE_TAGLINE"], "accent_color": cfg["ACCENT_COLOR"],
              "contact": cfg["CONTACT_EMAIL"]},
        features={"nearby_gigs": True, "imported_events": bool(sources), "event_sources": sources,
                  "place_search": cfg["GEOCODER"] != "none", "going": True, "people_search": True,
                  "message_requests": True, "follows": True, "private_accounts": True, "notifications": True, "skill_levels": True,
                  "gig_comments": True, "calendar": True, "age_check": True})


@bp.route("/gigs/nearby")
@api_login_required
def nearby_gigs():
    ratelimit.allow("nearby_user", g.user["id"])
    lat, lon = _center()
    radius = _float_param("radius_km", DEFAULT_RADIUS_KM, 1, MAX_RADIUS_KM)
    days = _int_param("days", DEFAULT_DAYS, 1, MAX_DAYS)
    limit = _int_param("limit", DEFAULT_LIMIT, 1, MAX_LIMIT)
    sort = request.args.get("sort", "distance")
    if sort not in ("distance", "date"):
        raise ApiError("sort must be distance or date.", "bad_sort")
    source = request.args.get("source", "all")
    if source not in SOURCES:
        raise ApiError("source must be all, community or external.", "bad_source")
    genre = request.args.get("genre", "")
    if genre and genre not in taxonomy.GENRES:
        raise ApiError("genre must be one of the keys of /api/v1/lists.", "bad_genre")

    if source in ("all", "external"):
        try:   # first search around a new place: fetch that area from the providers (see importer.ensure_coverage)
            importer.ensure_coverage(lat, lon, radius)
        except Exception as exc:  # whatever goes wrong there, the search itself must still answer
            bac_log("api", "on-demand import skipped (%s)" % type(exc).__name__)

    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M")   # tonight's gig is still "upcoming"
    until = (now + timedelta(days=days)).strftime("%Y-%m-%d %H:%M")
    box = geo.bounding_box(lat, lon, radius)

    gigs = []
    if source in ("all", "community"):
        gigs.extend(_community_gigs(lat, lon, radius, box, since, until))
    if source in ("all", "external"):
        gigs.extend(_external_events(lat, lon, radius, box, since, until))
    gigs = _remove_duplicates(gigs)
    if genre:
        gigs = [x for x in gigs if x["genre_key"] == genre]
    gigs.sort(key=(lambda x: (x["distance_km"], x["event_at"])) if sort == "distance"
              else (lambda x: (x["event_at"], x["distance_km"])))
    total = len(gigs)
    shown = gigs[:limit]
    from . import social    # imported here: social.py itself imports this module
    plans = social.counts_for(shown, g.user["id"])
    for item in shown:      # who is going, for the Going button and the "12 going" label
        entry = plans[(item["source"], item["ref"])]
        item["going_count"], item["interested_count"], item["my_status"] = entry["going"], entry["interested"], entry["mine"]
        item["friends_going"] = entry["friends_going"]
    response = jsonify(center={"latitude": round(lat, 4), "longitude": round(lon, 4)}, radius_km=radius,
                       sort=sort, source=source, total=total, count=min(total, limit), gigs=shown,
                       hint=_admin_hint() if total == 0 else None)
    response.headers["Cache-Control"] = "no-store"   # depends on where the member is: never cache it
    bac_log("api", "nearby gigs: %d found within %.0f km" % (total, radius))   # no coordinates in the log
    return response
