# File: geo.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Distances on the map and place-name -> coordinates lookups.

* Distances (haversine) and the search box are plain maths done here on the server: the position of the
  person searching is never sent to any third party.
* Only the TEXT of a place that a band typed ("The Basement Bar, Galway") is looked up, by default with
  OpenStreetMap's Nominatim service. Set GEOCODER=none to switch that off (gigs then need the "use my
  location" button), or GEOCODER_URL to use your own Nominatim-compatible server. Answers are cached.
"""
import json
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

from flask import current_app
from sqlalchemy.exc import IntegrityError

from .baclog import bac_log
from .db import commit, execute, rollback
from .util import TIME_FORMAT, now_str

EARTH_RADIUS_KM = 6371.0088
NOT_FOUND_RETRY = timedelta(days=1)   # a place that was not found is looked up again after a day


def valid_coords(lat, lon):
    """True for real numbers inside the globe (rejects NaN, infinity and out-of-range values)."""
    return (lat is not None and lon is not None and math.isfinite(lat) and math.isfinite(lon)
            and -90 <= lat <= 90 and -180 <= lon <= 180)


def parse_coord(text, limit):
    """A float from user input within +-limit, or None."""
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and -limit <= value <= limit else None


def distance_km(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points in kilometres (haversine)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(a)))


def bounding_box(lat, lon, radius_km):
    """(min_lat, max_lat, min_lon, max_lon) that surely contains the circle, to let the database narrow
    the search before the exact distance is computed. min_lon/max_lon are None near the poles or the
    date line, where a simple longitude range does not work."""
    dlat = math.degrees(radius_km / EARTH_RADIUS_KM)
    min_lat, max_lat = max(-90.0, lat - dlat), min(90.0, lat + dlat)
    if max_lat >= 90 or min_lat <= -90:
        return min_lat, max_lat, None, None
    dlon = math.degrees(radius_km / (EARTH_RADIUS_KM * math.cos(math.radians(lat))))
    min_lon, max_lon = lon - dlon, lon + dlon
    if min_lon < -180 or max_lon > 180:
        return min_lat, max_lat, None, None
    return min_lat, max_lat, min_lon, max_lon


# ------------------------------------------------------------------ place name -> coordinates
def normalize(place):
    return " ".join((place or "").lower().split())[:120]


class GeocoderUnavailable(Exception):
    """The place-name service could not answer (refused us, down, misconfigured). NOT the same as 'no such place':
    callers must not tell the member their place does not exist, and nothing is cached."""


# addresses nobody reads: OpenStreetMap (rightly) refuses clients that identify themselves with one of these
_PLACEHOLDER_DOMAINS = ("example.com", "example.org", "example.net", "localhost", "invalid")


def contact_is_placeholder(email):
    domain = (email or "").rpartition("@")[2].lower()
    return "@" not in (email or "") or domain in _PLACEHOLDER_DOMAINS


def _fetch(place):
    """Ask the geocoder. Returns (lat, lon), None when the place does not exist, and raises GeocoderUnavailable
    when the service cannot answer (that is not cached: it may work next time)."""
    cfg = current_app.config
    if contact_is_placeholder(cfg["CONTACT_EMAIL"]) and "nominatim.openstreetmap.org" in cfg["GEOCODER_URL"]:
        raise GeocoderUnavailable("OpenStreetMap refuses requests that do not identify a real contact: set "
                                  "CONTACT_EMAIL to your real e-mail address (it is now %r)." % cfg["CONTACT_EMAIL"])
    query = urllib.parse.urlencode({"q": place, "format": "json", "limit": 1})
    request = urllib.request.Request(
        cfg["GEOCODER_URL"] + "?" + query,
        headers={"User-Agent": "rockconnect/1.0 (%s)" % cfg["CONTACT_EMAIL"], "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=4) as response:
            found = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            raise GeocoderUnavailable("The place service refused us (HTTP 403). OpenStreetMap does this when "
                                      "CONTACT_EMAIL is not a real address or when it is used too heavily.")
        raise GeocoderUnavailable("The place service answered HTTP %d." % exc.code)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise GeocoderUnavailable("The place service could not be reached (%s)." % type(exc).__name__)
    if not found:
        return None
    lat, lon = parse_coord(found[0].get("lat"), 90), parse_coord(found[0].get("lon"), 180)
    return (lat, lon) if lat is not None and lon is not None else None


def geocode(place):
    """(latitude, longitude) for a place name, or None if the place is unknown or lookups are switched off.

    Raises GeocoderUnavailable when the service itself fails: that is a different thing from an unknown place."""
    key = normalize(place)
    if not key:
        return None
    row = execute("SELECT latitude, longitude, created_at FROM geocache WHERE place = :p", p=key).fetchone()
    if row is not None:
        if row[0] is not None:
            return row[0], row[1]
        age = datetime.now(timezone.utc) - datetime.strptime(row[2], TIME_FORMAT).replace(tzinfo=timezone.utc)
        if age < NOT_FOUND_RETRY:
            return None  # looked for it recently and found nothing
    if current_app.config["GEOCODER"] != "nominatim":
        return None
    try:
        result = _fetch(key)
    except GeocoderUnavailable as exc:
        bac_log("geo", "lookup FAILED: %s" % exc)
        raise
    except Exception as exc:  # anything unexpected is treated the same way: not 'no such place'
        bac_log("geo", "lookup FAILED (%s)" % type(exc).__name__)
        raise GeocoderUnavailable("The place service failed (%s)." % type(exc).__name__) from exc
    try:
        execute("DELETE FROM geocache WHERE place = :p", p=key)
        execute("INSERT INTO geocache (place, latitude, longitude, created_at) VALUES (:p, :a, :o, :n)",
                p=key, a=result[0] if result else None, o=result[1] if result else None, n=now_str())
        commit()
    except IntegrityError:
        rollback()  # another request cached the same place a moment ago
    bac_log("geo", "looked up a place name -> %s" % ("found" if result else "not found"))
    return result
