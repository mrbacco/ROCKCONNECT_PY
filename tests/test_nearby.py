# File: test_nearby.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""'Gigs near me': map maths, place lookups, the JSON API, gig positions and the gig board panel."""
import json
from datetime import datetime, timedelta

import pytest

from helpers import limits, make_app, post_form, register, scalar, sql, token_of, user_id
from rockconnect import geo

GALWAY = (53.2707, -9.0568)
DUBLIN = (53.3498, -6.2603)
CORK = (51.8985, -8.4756)


def when(days, hour=20):
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0).strftime("%Y-%m-%dT%H:%M")


def announce(client, place="Somewhere", lat=None, lon=None, days=3, body="gig", at=None):
    data = {"_csrf": token_of(client, "/feed"), "body": body, "event_at": at or when(days), "event_place": place}
    if lat is not None:
        data.update(event_lat=str(lat), event_lon=str(lon))
    return client.post("/posts", data=data, follow_redirects=True)


def nearby(client, **params):
    return client.get("/api/v1/gigs/nearby", query_string=params)


@pytest.fixture
def scene(tmp_path):
    """Three gigs: Galway (3 days), Dublin (5 days), Cork (4 days); a fan standing in Galway."""
    app = make_app(tmp_path)
    band, venue, fan = register(app, "kings", kind="band"), register(app, "cork", kind="venue"), register(app, "rita")
    announce(band, "Galway Docks", *GALWAY, days=3, body="galway gig")
    announce(band, "Dublin Castle", *DUBLIN, days=5, body="dublin gig")
    announce(venue, "Cork Harbour", *CORK, days=4, body="cork gig")
    return app, band, venue, fan


# ------------------------------------------------------------------ the maths
def test_distances_are_right():
    assert geo.distance_km(*DUBLIN, *DUBLIN) == 0
    assert 183 < geo.distance_km(*DUBLIN, *GALWAY) < 189          # about 186 km by air
    assert 215 < geo.distance_km(*DUBLIN, *CORK) < 222            # about 219 km
    assert 19000 < geo.distance_km(0, 0, 0, 180) < 20100          # half the way round the world
    assert geo.distance_km(10, 20, 30, 40) == pytest.approx(geo.distance_km(30, 40, 10, 20))


def test_bounding_box_contains_the_whole_circle():
    lat, lon, r = 53.27, -9.05, 50
    min_lat, max_lat, min_lon, max_lon = geo.bounding_box(lat, lon, r)
    for bearing_lat, bearing_lon in ((r / 111.2, 0), (-r / 111.2, 0), (0, r / 66.6), (0, -r / 66.6)):
        assert min_lat <= lat + bearing_lat <= max_lat and min_lon <= lon + bearing_lon <= max_lon
    assert geo.bounding_box(0, 179.9, 50)[2:] == (None, None)       # date line: no longitude filter
    assert geo.bounding_box(89.9, 0, 50)[2:] == (None, None)        # pole


@pytest.mark.parametrize("lat,lon,ok", [(53, -9, True), (90, 180, True), (-90, -180, True), (91, 0, False),
                                       (0, 181, False), (float("nan"), 0, False), (float("inf"), 0, False),
                                       (None, 0, False)])
def test_valid_coords(lat, lon, ok):
    assert geo.valid_coords(lat, lon) is ok


def test_parse_coord():
    assert geo.parse_coord("53.5", 90) == 53.5 and geo.parse_coord("-9", 180) == -9
    for bad in ("abc", "nan", "inf", "91", "", None, "1e999"):
        assert geo.parse_coord(bad, 90) is None


# ------------------------------------------------------------------ the API
def test_nearby_returns_gigs_in_range_nearest_first(scene):
    app, band, venue, fan = scene
    r = nearby(fan, lat=53.27, lon=-9.05, radius_km=300)
    body = r.get_json()
    assert r.status_code == 200 and body["total"] == 3 and body["count"] == 3
    assert [g["body"] for g in body["gigs"]] == ["galway gig", "cork gig", "dublin gig"]       # by distance
    first = body["gigs"][0]
    assert first["distance_km"] < 2 and first["place"] == "Galway Docks"
    assert first["author"] == {"id": user_id(app, "kings"), "username": "kings", "name": "Kings Test", "kind": "band"}
    assert first["url"].startswith("/posts/") and first["event_label"] and first["has_photo"] is False
    assert body["center"] == {"latitude": 53.27, "longitude": -9.05}
    assert first["source"] == "community" and first["title"] == "Kings Test" and first["ticket_url"] is None
    assert r.headers["Cache-Control"] == "no-store"


def test_radius_limits_the_results(scene):
    app, band, venue, fan = scene
    only_galway = nearby(fan, lat=53.27, lon=-9.05, radius_km=50).get_json()
    assert [g["body"] for g in only_galway["gigs"]] == ["galway gig"]
    assert nearby(fan, lat=53.27, lon=-9.05, radius_km=175).get_json()["total"] == 2          # + Cork (~157 km)
    assert nearby(fan, lat=53.27, lon=-9.05, radius_km=200).get_json()["total"] == 3          # + Dublin (~186 km)
    assert nearby(fan, lat=0, lon=0).get_json()["gigs"] == []                                  # nothing near the equator


def test_sort_by_date_limit_and_days(scene):
    app, band, venue, fan = scene
    by_date = nearby(fan, lat=53.27, lon=-9.05, radius_km=300, sort="date").get_json()
    assert [g["body"] for g in by_date["gigs"]] == ["galway gig", "cork gig", "dublin gig"]   # 3, 4, 5 days
    sql(app, "UPDATE posts SET event_at = :d WHERE body = 'galway gig'", d=when(6).replace("T", " "))
    by_date = nearby(fan, lat=53.27, lon=-9.05, radius_km=300, sort="date").get_json()
    assert [g["body"] for g in by_date["gigs"]] == ["cork gig", "dublin gig", "galway gig"]
    limited = nearby(fan, lat=53.27, lon=-9.05, radius_km=300, limit=2).get_json()
    assert limited["total"] == 3 and limited["count"] == 2 and len(limited["gigs"]) == 2
    # how far ahead to look (dates far apart so the time of day the test runs cannot matter)
    for body, days_ahead in (("galway gig", 2), ("dublin gig", 50), ("cork gig", 200)):
        sql(app, "UPDATE posts SET event_at = :d WHERE body = :b", b=body,
            d=(datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d %H:%M"))
    ahead = lambda days: [g["body"] for g in nearby(fan, lat=53.27, lon=-9.05, radius_km=300, days=days).get_json()["gigs"]]  # noqa: E731
    assert ahead(30) == ["galway gig"]
    assert sorted(ahead(100)) == ["dublin gig", "galway gig"]
    assert len(ahead(365)) == 3
    assert [g["body"] for g in nearby(fan, lat=53.27, lon=-9.05, radius_km=300).get_json()["gigs"]] == ["galway gig", "dublin gig"]   # default 90 days
    assert nearby(fan, lat=53.27, lon=-9.05, radius_km=9999, limit=9999).get_json()["radius_km"] == 500   # clamped


def test_past_gigs_posts_without_position_and_plain_posts_are_not_returned(scene):
    app, band, venue, fan = scene
    announce(band, "Old Gig", *GALWAY, at=when(-4), body="old gig")
    announce(band, "No Position Town", body="positionless gig")            # geocoder is off in tests
    band.post("/posts", data={"_csrf": token_of(band, "/feed"), "body": "just a post"})
    bodies = [g["body"] for g in nearby(fan, lat=53.27, lon=-9.05, radius_km=300).get_json()["gigs"]]
    assert "old gig" not in bodies and "positionless gig" not in bodies and "just a post" not in bodies
    assert scalar(app, "SELECT latitude FROM posts WHERE body = 'positionless gig'") is None


def test_blocked_and_suspended_authors_are_hidden(scene):
    app, band, venue, fan = scene
    post_form(fan, "/block/%d" % user_id(app, "cork"))
    assert "cork gig" not in [g["body"] for g in nearby(fan, lat=53.27, lon=-9.05, radius_km=300).get_json()["gigs"]]
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'kings'")
    assert nearby(fan, lat=53.27, lon=-9.05, radius_km=300).get_json()["gigs"] == []


def test_api_errors_are_json_with_codes(scene):
    app, band, venue, fan = scene
    cases = {
        (): "location_required",
        (("lat", "53"),): "bad_coordinates",
        (("lat", "abc"), ("lon", "1")): "bad_coordinates",
        (("lat", "95"), ("lon", "1")): "bad_coordinates",
        (("lat", "nan"), ("lon", "1")): "bad_coordinates",
        (("lat", "53"), ("lon", "-9"), ("radius_km", "far")): "bad_radius_km",
        (("lat", "53"), ("lon", "-9"), ("radius_km", "nan")): "bad_radius_km",
        (("lat", "53"), ("lon", "-9"), ("days", "1.5")): "bad_days",
        (("lat", "53"), ("lon", "-9"), ("limit", "x")): "bad_limit",
        (("lat", "53"), ("lon", "-9"), ("sort", "random")): "bad_sort",
        (("profile", "1"),): "no_profile_location",
    }
    for params, code in cases.items():
        r = fan.get("/api/v1/gigs/nearby", query_string=list(params))
        assert r.status_code == 400 and r.get_json()["code"] == code, (params, r.get_json())
        assert r.get_json()["error"]


def test_api_needs_sign_in_and_answers_json_not_a_redirect(scene):
    app, *_ = scene
    r = app.test_client().get("/api/v1/gigs/nearby?lat=53&lon=-9")
    assert r.status_code == 401 and r.get_json()["code"] == "session_expired" and r.get_json()["login_url"]


def test_api_is_rate_limited(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(nearby_user=(2, 600)))
    fan = register(app)
    assert [nearby(fan, lat=1, lon=1).status_code for _ in range(2)] == [200, 200]
    r = nearby(fan, lat=1, lon=1)
    assert r.status_code == 429 and r.get_json()["error"]


def test_coordinates_never_reach_the_logs(scene, capsys):
    app, band, venue, fan = scene
    capsys.readouterr()
    import rockconnect.baclog as baclog
    baclog.ENABLED = True
    try:
        nearby(fan, lat=53.27, lon=-9.05)
    finally:
        baclog.ENABLED = False
    out = capsys.readouterr().out
    assert "53.27" not in out and "-9.05" not in out and "/api/v1/gigs/nearby" not in out
    from rockconnect import _is_quiet
    assert _is_quiet("/api/v1/gigs/nearby")


# ------------------------------------------------------------------ place names
class FakeNominatim:
    def __init__(self):
        self.calls = []
        self.answers = {"galway": (53.2707, -9.0568)}

    def __call__(self, place):
        self.calls.append(place)
        if place == "unreachable":
            raise OSError("no network")
        return self.answers.get(place)


@pytest.fixture
def geocoded(tmp_path, monkeypatch):
    fake = FakeNominatim()
    monkeypatch.setattr(geo, "_fetch", fake)
    app = make_app(tmp_path, GEOCODER="nominatim")
    return app, fake


def test_search_by_place_name_uses_the_lookup_and_caches_it(geocoded):
    app, fake = geocoded
    band, fan = register(app, "kings", kind="band"), register(app, "rita")
    announce(band, "Galway Docks", *GALWAY, body="galway gig")
    for name in ("Galway", "  GALWAY ", "galway"):
        body = nearby(fan, q=name, radius_km=20).get_json()
        assert [g["body"] for g in body["gigs"]] == ["galway gig"]
        assert body["center"]["latitude"] == 53.2707
    assert fake.calls == ["galway"]                                # looked up once, then remembered
    assert scalar(app, "SELECT count(*) FROM geocache") == 1


def test_unknown_place_is_a_404_and_not_looked_up_again_for_a_day(geocoded):
    app, fake = geocoded
    fan = register(app)
    for _ in range(2):
        r = nearby(fan, q="Atlantis")
        assert r.status_code == 404 and r.get_json()["code"] == "place_not_found"
    assert fake.calls == ["atlantis"]
    sql(app, "UPDATE geocache SET created_at = '2000-01-01 00:00:00'")      # a day later: try again
    nearby(fan, q="Atlantis")
    assert fake.calls == ["atlantis", "atlantis"]


def test_an_unreachable_service_is_reported_as_unavailable_not_as_an_unknown_place(geocoded):
    app, fake = geocoded
    fan = register(app)
    r = nearby(fan, q="unreachable")
    assert r.status_code == 503 and r.get_json()["code"] == "place_lookup_unavailable"      # not "place not found"
    assert scalar(app, "SELECT count(*) FROM geocache") == 0                                 # and not remembered


def test_profile_location_is_used_with_profile_1(geocoded):
    app, fake = geocoded
    fan = register(app, "rita")
    sql(app, "UPDATE users SET location = 'Galway' WHERE username = 'rita'")
    assert nearby(fan, profile=1).get_json()["center"]["latitude"] == 53.2707


def test_lookups_off_means_no_place_search(tmp_path):
    app = make_app(tmp_path)                    # tests default to GEOCODER=none
    fan = register(app)
    assert nearby(fan, q="Galway").get_json()["code"] == "place_not_found"
    assert app.config["GEOCODER"] == "none"


def test_place_lookups_are_limited_separately(tmp_path, monkeypatch):
    monkeypatch.setattr(geo, "_fetch", FakeNominatim())
    app = make_app(tmp_path, GEOCODER="nominatim", RATE_LIMITS=limits(geocode_user=(1, 3600)))
    fan = register(app)
    assert nearby(fan, q="galway").status_code == 200
    assert nearby(fan, q="galway").status_code == 429            # even a cached one counts as a lookup request
    assert nearby(fan, lat=1, lon=1).status_code == 200          # but searching by position is unaffected


def test_nominatim_client_talks_to_the_service_correctly(tmp_path, monkeypatch):
    seen = {}

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(self.payload).encode()

    def fake_urlopen(request, timeout):
        seen["url"], seen["agent"], seen["timeout"] = request.full_url, request.get_header("User-agent"), timeout
        return Response([{"lat": "53.2707", "lon": "-9.0568", "display_name": "Galway"}])

    monkeypatch.setattr("rockconnect.geo.urllib.request.urlopen", fake_urlopen)
    app = make_app(tmp_path, GEOCODER="nominatim", CONTACT_EMAIL="hi@gighub.example",
                   GEOCODER_URL="https://geo.example/search")
    with app.test_request_context():
        assert geo._fetch("galway") == (53.2707, -9.0568)
    assert seen["url"].startswith("https://geo.example/search?") and "q=galway" in seen["url"]
    assert "hi@gighub.example" in seen["agent"] and seen["timeout"] <= 5      # identifies us, never hangs the page

    monkeypatch.setattr("rockconnect.geo.urllib.request.urlopen", lambda r, timeout: Response([]))
    with app.test_request_context():
        assert geo._fetch("nowhere") is None
    monkeypatch.setattr("rockconnect.geo.urllib.request.urlopen",
                        lambda r, timeout: Response([{"lat": "999", "lon": "1"}]))
    with app.test_request_context():
        assert geo._fetch("garbage") is None                                    # out-of-range answers are rejected


# ------------------------------------------------------------------ giving a gig a position
def test_gig_position_from_the_location_button(tmp_path):
    app = make_app(tmp_path)
    band = register(app, "kings", kind="band")
    page = announce(band, "My Venue", 53.27441, -9.04912)
    assert b"Gig announced" in page.data and b"could not place it" not in page.data
    assert scalar(app, "SELECT latitude FROM posts") == 53.27441 and scalar(app, "SELECT longitude FROM posts") == -9.04912


def test_gig_position_from_the_place_name(geocoded):
    app, fake = geocoded
    band = register(app, "kings", kind="band")
    announce(band, "Galway")
    assert scalar(app, "SELECT latitude FROM posts") == 53.2707 and fake.calls == ["galway"]


def test_button_coordinates_win_over_the_lookup(geocoded):
    app, fake = geocoded
    band = register(app, "kings", kind="band")
    announce(band, "Galway", 10.5, 20.5)
    assert scalar(app, "SELECT latitude FROM posts") == 10.5 and fake.calls == []


def test_unplaceable_gig_is_still_posted_and_the_poster_is_told(geocoded):
    app, fake = geocoded
    band = register(app, "kings", kind="band")
    page = announce(band, "Atlantis Arena")
    assert b"could not place it on the map" in page.data
    assert scalar(app, "SELECT count(*) FROM posts") == 1 and scalar(app, "SELECT latitude FROM posts") is None


@pytest.mark.parametrize("lat,lon", [("999", "1"), ("abc", "5"), ("nan", "nan"), ("5", ""), ("", "")])
def test_bad_coordinates_are_ignored(tmp_path, lat, lon):
    app = make_app(tmp_path)
    band = register(app, "kings", kind="band")
    band.post("/posts", data={"_csrf": token_of(band, "/feed"), "body": "x", "event_at": when(2),
                              "event_place": "P", "event_lat": lat, "event_lon": lon})
    assert scalar(app, "SELECT count(*) FROM posts") == 1 and scalar(app, "SELECT latitude FROM posts") is None


def test_coordinates_without_a_gig_date_are_not_stored(tmp_path):
    app = make_app(tmp_path)
    band = register(app, "kings", kind="band")
    band.post("/posts", data={"_csrf": token_of(band, "/feed"), "body": "plain", "event_lat": "5", "event_lon": "5"})
    assert scalar(app, "SELECT latitude FROM posts") is None


# ------------------------------------------------------------------ pages
def test_post_permalink_respects_visibility(scene):
    app, band, venue, fan = scene
    pid = scalar(app, "SELECT id FROM posts WHERE body = 'galway gig'")
    page = fan.get("/posts/%d" % pid)
    assert page.status_code == 200 and b"galway gig" in page.data
    assert b"openstreetmap.org/?mlat=53.2707" in page.data           # the map link of a positioned gig
    assert fan.get("/posts/9999").status_code == 404
    assert app.test_client().get("/posts/%d" % pid).status_code == 302
    post_form(fan, "/block/%d" % user_id(app, "kings"))
    assert fan.get("/posts/%d" % pid).status_code == 404


def test_gig_board_has_the_nearby_panel_and_the_composer_the_location_button(scene):
    app, band, venue, fan = scene
    board = fan.get("/gigs").get_data(as_text=True)
    assert 'id="nearby"' in board and "/api/v1/gigs/nearby" in board and "gigs.js" in board
    assert "Use my current location" in board and "nearby-profile" not in board      # no profile location yet
    sql(app, "UPDATE users SET location = 'Galway' WHERE username = 'rita'")
    assert "nearby-profile" in fan.get("/gigs").get_data(as_text=True)
    assert "data-use-location" in band.get("/feed").get_data(as_text=True)
    assert "data-use-location" not in fan.get("/feed").get_data(as_text=True)


def test_browser_may_use_location_for_this_site_only(scene):
    app, band, venue, fan = scene
    policy = fan.get("/gigs").headers["Permissions-Policy"]
    assert "geolocation=(self)" in policy and "camera=()" in policy


def test_migration_keeps_old_gigs_without_a_position(tmp_path):
    from sqlalchemy import create_engine, text
    from rockconnect.migrate import upgrade
    engine = create_engine("sqlite:///" + str(tmp_path / "old.sqlite"))
    upgrade(engine, "0002")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (username, name, email, password, about) VALUES ('a','A','a@a.aa','h','x')"))
        conn.execute(text("INSERT INTO posts (user_id, body, created_at, event_at, event_place)"
                          " VALUES (1, 'old gig', '2026-01-01 00:00:00', '2027-01-01 20:00', 'Somewhere')"))
    upgrade(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT body, latitude, longitude FROM posts")).fetchone() == ("old gig", None, None)
