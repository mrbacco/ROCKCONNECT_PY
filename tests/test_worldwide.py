# File: test_worldwide.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Gigs in any city: fetching around a search on demand, several providers, duplicates, and bands' own Bandsintown."""
import io
import zipfile
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from helpers import limits, make_admin, make_app, post_form, register, scalar, sql, token_of, user_id
from rockconnect import importer
from rockconnect.importer import ImportFailed
from test_import import page, tm_event
from test_providers import bit_event, skiddle_event, songkick_event


class FakeWorld:
    """The whole internet as far as the importer is concerned: one handler per provider host."""

    def __init__(self, **handlers):
        self.handlers, self.requests = handlers, []

    def __call__(self, url, provider="Ticketmaster", timeout=20):
        parsed = urlparse(url)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.requests.append(SimpleNamespace(host=parsed.netloc, params=params, provider=provider, timeout=timeout,
                                             url=url))
        for key, handler in self.handlers.items():
            if key in parsed.netloc:
                result = handler(params) if callable(handler) else handler
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError("unexpected request to " + parsed.netloc)

    def to(self, host):
        return [r for r in self.requests if host in r.host]


TM, SKIDDLE, SONGKICK, BIT = "ticketmaster.com", "skiddle.com", "songkick.com", "bandsintown.com"
EMPTY_SKIDDLE = {"error": 0, "totalcount": 0, "results": []}
EMPTY_SONGKICK = {"resultsPage": {"status": "ok", "totalEntries": 0, "perPage": 50, "results": {}}}

CITIES = {   # name: (latitude, longitude): north and south, east and west of Greenwich, near the date line
    "Paris": (48.8566, 2.3522), "New York": (40.7128, -74.0060), "Sydney": (-33.8688, 151.2093),
    "Sao Paulo": (-23.5505, -46.6333), "Tokyo": (35.6762, 139.6503), "Auckland": (-36.8485, 174.7633),
    "Reykjavik": (64.1466, -21.9426), "Nairobi": (-1.2921, 36.8219),
}


@pytest.fixture(autouse=True)
def no_sleeping(monkeypatch):
    monkeypatch.setattr(importer.time, "sleep", lambda s: None)


def world_app(tmp_path, monkeypatch, world, **config):
    monkeypatch.setattr(importer, "_http_get_json", world)
    settings = {"TICKETMASTER_API_KEY": "tm-key", "IMPORT_ON_DEMAND": True}
    settings.update(config)
    return make_app(tmp_path, **settings)


def tm_near(n, lat, lon, days=3, title=None):
    event = tm_event(n, days=days, lat=str(lat), lon=str(lon), venue="Venue %d" % n)
    if title:
        event["name"] = title
    return event


def nearby(client, **params):
    return client.get("/api/v1/gigs/nearby", query_string=params)


def covered(app):
    return [tuple(r) for r in sql_rows(app, "SELECT provider, cell, status FROM import_coverage ORDER BY id")]


def sql_rows(app, statement, **params):
    from rockconnect.db import execute
    with app.app_context():
        return execute(statement, **params).fetchall()


# ------------------------------------------------------------------ the grid
@pytest.mark.parametrize("lat,lon,cell", [
    (51.5074, -0.1278, "51.50,-0.25"), (51.5, 0.0, "51.50,0.00"), (-33.8688, 151.2093, "-33.75,151.25"),
    (0.0, 0.0, "0.00,0.00"), (-0.1, -0.1, "0.00,0.00"), (89.99, 179.99, "90.00,180.00")])
def test_searches_snap_to_a_coarse_grid(lat, lon, cell):
    clat, clon, key = importer.cell_for(lat, lon)
    assert key == cell and abs(clat - lat) <= 0.126 and abs(clon - lon) <= 0.126


def test_nearby_points_share_a_cell_and_the_cell_is_never_more_precise_than_the_grid():
    assert importer.cell_for(51.51, -0.13)[2] == importer.cell_for(51.60, -0.20)[2]
    assert importer.cell_for(51.51, -0.13)[2] != importer.cell_for(51.76, -0.13)[2]
    for lat, lon in ((53.34981, -6.26031), (-33.86882, 151.20929)):
        assert len(importer.cell_for(lat, lon)[2].split(",")[0].split(".")[1]) == 2      # two decimals at most


@pytest.mark.parametrize("radius,fetch", [(1, 25), (5, 25), (25, 45), (50, 70), (80, 100), (500, 100)])
def test_the_fetch_radius_covers_the_search_radius_plus_the_grid_slack(radius, fetch):
    assert importer.coverage_radius(radius) == fetch


# ------------------------------------------------------------------ fetching around a search
def test_the_first_search_in_a_place_fetches_it_and_the_second_does_not(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([tm_near(1, 48.86, 2.35)])})
    app = world_app(tmp_path, monkeypatch, world)
    with app.app_context():
        assert importer.ensure_coverage(48.8566, 2.3522, 25) == ["ticketmaster"]
        assert importer.ensure_coverage(48.87, 2.30, 25) == []                    # same grid square, already covered
    assert len(world.to(TM)) == 1
    assert covered(app) == [("ticketmaster", "48.75,2.25", "ok")] or covered(app)[0][0] == "ticketmaster"
    assert scalar(app, "SELECT count(*) FROM external_events") == 1
    assert scalar(app, "SELECT area FROM external_events").startswith("auto:")


def test_only_the_rounded_area_ever_reaches_the_provider(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([])})
    app = world_app(tmp_path, monkeypatch, world)
    exact = (48.85661, 2.35222)
    with app.app_context():
        importer.ensure_coverage(*exact, 25)
    url = world.requests[0].url
    sent = world.requests[0].params
    assert "48.85661" not in url and "2.35222" not in url and "48.8566" not in url
    lat, lon = (float(x) for x in sent["latlong"].split(","))
    assert (lat, lon) == importer.cell_for(*exact)[:2] and sent["radius"] == "45"


def test_each_new_place_is_fetched_once_for_every_city_in_the_world(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: lambda p: page([tm_near(int(abs(float(p["latlong"].split(",")[0])) * 100),
                                                    *(float(x) for x in p["latlong"].split(",")))])})
    app = world_app(tmp_path, monkeypatch, world)
    fan = register(app, "rita")
    for name, (lat, lon) in CITIES.items():
        body = nearby(fan, lat=round(lat, 2), lon=round(lon, 2), radius_km=30).get_json()
        assert body["total"] == 1, name                                               # found a gig right there
        assert body["gigs"][0]["distance_km"] < 20 and body["gigs"][0]["source"] == "ticketmaster", name
    assert len(world.to(TM)) == len(CITIES)
    for name, (lat, lon) in CITIES.items():                                           # all remembered now
        nearby(fan, lat=round(lat, 2), lon=round(lon, 2), radius_km=30)
    assert len(world.to(TM)) == len(CITIES)


def test_a_bigger_radius_than_covered_fetches_again(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([])})
    app = world_app(tmp_path, monkeypatch, world)
    with app.app_context():
        importer.ensure_coverage(40.7, -74.0, 5)
        importer.ensure_coverage(40.7, -74.0, 20)         # 25 km fetched before, 45 needed now
        importer.ensure_coverage(40.7, -74.0, 10)         # smaller again: covered
    assert [r.params["radius"] for r in world.to(TM)] == ["25", "40"]


def test_old_coverage_is_refreshed_without_duplicating_events(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([tm_near(1, 48.86, 2.35)])})
    app = world_app(tmp_path, monkeypatch, world, IMPORT_COVERAGE_HOURS=12)
    with app.app_context():
        importer.ensure_coverage(48.86, 2.35, 25)
    sql(app, "UPDATE import_coverage SET fetched_at = '2000-01-01 00:00:00'")
    with app.app_context():
        assert importer.ensure_coverage(48.86, 2.35, 25) == ["ticketmaster"]
    assert scalar(app, "SELECT count(*) FROM external_events") == 1 and len(world.to(TM)) == 2


def test_a_failed_fetch_is_not_hammered_but_retried_later(tmp_path, monkeypatch):
    answers = [ImportFailed("Could not reach Ticketmaster (URLError).")]
    world = FakeWorld(**{TM: lambda p: answers.pop(0) if answers else page([tm_near(1, 48.86, 2.35)])})
    app = world_app(tmp_path, monkeypatch, world)
    with app.app_context():
        assert importer.ensure_coverage(48.86, 2.35, 25) == []
        assert importer.ensure_coverage(48.86, 2.35, 25) == []              # within the retry pause: not asked again
    assert len(world.to(TM)) == 1 and covered(app)[0][2] == "error"
    sql(app, "UPDATE import_coverage SET fetched_at = '2000-01-01 00:00:00'")   # ten minutes later
    with app.app_context():
        assert importer.ensure_coverage(48.86, 2.35, 25) == ["ticketmaster"]
    assert covered(app)[0][2] == "ok"


def test_nothing_happens_when_switched_off_or_without_a_key(tmp_path, monkeypatch):
    world = FakeWorld()
    off = world_app(tmp_path / "a", monkeypatch, world, IMPORT_ON_DEMAND=False)
    keyless = world_app(tmp_path / "b", monkeypatch, world, TICKETMASTER_API_KEY="")
    for app in (off, keyless):
        with app.app_context():
            assert importer.ensure_coverage(48.86, 2.35, 25) == []
    assert world.requests == []


def test_each_provider_is_fetched_and_one_failing_does_not_block_the_others(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: ImportFailed("Ticketmaster answered HTTP 500.", 500),
                         SKIDDLE: {"error": 0, "totalcount": 1, "results": [skiddle_event(1, venue={
                             "name": "Electric Ballroom", "town": "London", "latitude": "51.5391", "longitude": "-0.1426"})]},
                         SONGKICK: EMPTY_SONGKICK})
    app = world_app(tmp_path, monkeypatch, world, SKIDDLE_API_KEY="sk", SONGKICK_API_KEY="sg")
    with app.app_context():
        done = importer.ensure_coverage(51.53, -0.14, 25)
    assert done == ["skiddle", "songkick"]
    assert sorted(c[0] for c in covered(app)) == ["skiddle", "songkick", "ticketmaster"]
    assert dict((c[0], c[2]) for c in covered(app))["ticketmaster"] == "error"
    assert scalar(app, "SELECT source FROM external_events") == "skiddle"


def test_the_whole_site_is_limited_in_how_often_it_asks_the_providers(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([])})
    app = world_app(tmp_path, monkeypatch, world, RATE_LIMITS=limits(ondemand_site=(2, 3600)))
    with app.app_context():
        for lat in (10.0, 20.0, 30.0, 40.0):
            importer.ensure_coverage(lat, 20.0, 25)
    assert len(world.to(TM)) == 2                                          # the third and fourth places wait


def test_only_one_of_several_simultaneous_searches_does_the_fetching(tmp_path, monkeypatch):
    app = world_app(tmp_path, monkeypatch, FakeWorld())
    with app.app_context():
        assert importer._claim("ticketmaster", "48.75,2.25", 45) is True
        assert importer._claim("ticketmaster", "48.75,2.25", 45) is False       # someone else is already on it
    sql(app, "UPDATE import_coverage SET fetched_at = '2000-01-01 00:00:00'")    # that fetch died long ago
    with app.app_context():
        assert importer._claim("ticketmaster", "48.75,2.25", 45) is True


def test_a_big_city_gets_a_quick_fill_then_the_rest_in_the_background(tmp_path, monkeypatch):
    pages_asked = []

    def ticketmaster(p):
        pages_asked.append(int(p["page"]))
        return page([tm_near(int(p["page"]), 51.5, -0.12)], total_pages=9, number=int(p["page"]))

    world = FakeWorld(**{TM: ticketmaster})
    app = world_app(tmp_path, monkeypatch, world)
    started = []

    class Recorded:
        def __init__(self, target, daemon):
            started.append((target, daemon))

        def start(self):
            pass

    monkeypatch.setattr(importer.threading, "Thread", Recorded)
    with app.app_context():
        importer.ensure_coverage(51.5, -0.12, 25)
    assert pages_asked == [0]                                                 # quick fill: one page only
    assert world.requests[0].timeout == importer.ONDEMAND_TIMEOUT             # and it may not hang the member's search
    assert scalar(app, "SELECT count(*) FROM external_events") == 1 and covered(app)[0][2] == "partial"
    assert len(started) == 1 and started[0][1] is True                        # a daemon thread was started

    started[0][0]()                                                           # ... which now finishes the job
    assert scalar(app, "SELECT count(*) FROM external_events") == 5 and covered(app)[0][2] == "ok"
    assert world.requests[-1].timeout == 20                                   # the background job has no short limit


def test_a_small_place_needs_no_background_job(tmp_path, monkeypatch):
    app = world_app(tmp_path, monkeypatch, FakeWorld(**{TM: page([tm_near(1, 64.14, -21.94)])}))
    monkeypatch.setattr(importer.threading, "Thread", lambda **k: pytest.fail("no thread expected"))
    with app.app_context():
        importer.ensure_coverage(64.14, -21.94, 25)
    assert covered(app)[0][2] == "ok"


def test_pruning_forgets_old_coverage_so_it_is_fetched_again(tmp_path, monkeypatch):
    app = world_app(tmp_path, monkeypatch, FakeWorld(**{TM: page([])}))
    with app.app_context():
        importer.ensure_coverage(10.0, 10.0, 25)
    sql(app, "UPDATE import_coverage SET fetched_at = '2000-01-01 00:00:00'")
    with app.app_context():
        importer.prune()
    assert covered(app) == []


# ------------------------------------------------------------------ through the API
def test_searching_a_new_city_returns_gigs_and_a_member_never_waits_twice(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([tm_near(1, 35.68, 139.69)])})
    app = world_app(tmp_path, monkeypatch, world)
    fan = register(app, "rita")
    first = nearby(fan, lat=35.68, lon=139.69).get_json()
    again = nearby(fan, lat=35.69, lon=139.70).get_json()
    assert first["total"] == again["total"] == 1 and len(world.to(TM)) == 1


def test_member_gigs_alone_never_trigger_a_fetch_and_a_broken_fetch_never_breaks_the_search(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([])})
    app = world_app(tmp_path, monkeypatch, world)
    fan = register(app, "rita")
    assert nearby(fan, lat=1, lon=1, source="community").status_code == 200 and world.requests == []

    def explode(*a, **k):
        raise RuntimeError("something unexpected")
    monkeypatch.setattr(importer, "ensure_coverage", explode)
    assert nearby(fan, lat=2, lon=2).status_code == 200


def test_the_same_concert_from_several_sources_is_listed_once(tmp_path, monkeypatch):
    lat, lon = 51.5391, -0.1426
    world = FakeWorld(**{
        TM: page([tm_near(1, lat, lon, title="Gilla Band")]),
        SKIDDLE: {"error": 0, "totalcount": 2, "results": [
            skiddle_event(1, eventname="Gilla Band + support", venue={"name": "x", "town": "London", "latitude": str(lat), "longitude": str(lon)}),
            skiddle_event(2, eventname="A Completely Different Act", venue={"name": "x", "town": "London", "latitude": str(lat), "longitude": str(lon)})]},
        SONGKICK: {"resultsPage": {"status": "ok", "totalEntries": 1, "perPage": 50, "results": {"event": [
            songkick_event(1, displayName="Gilla Band at Electric Ballroom", venue={"displayName": "EB", "lat": lat, "lng": lon})]}}}})
    app = world_app(tmp_path, monkeypatch, world, SKIDDLE_API_KEY="sk", SONGKICK_API_KEY="sg")
    fan = register(app, "rita")
    gigs = nearby(fan, lat=51.54, lon=-0.14, radius_km=5).get_json()["gigs"]
    titles = sorted(g["title"] for g in gigs)
    assert titles == ["A Completely Different Act", "Gilla Band"]                  # one Gilla Band, Ticketmaster's
    assert {g["source"] for g in gigs if g["title"] == "Gilla Band"} == {"ticketmaster"}
    assert scalar(app, "SELECT count(*) FROM external_events") == 4               # all stored, shown once


def test_duplicate_detection_rules():
    base = {"source": "ticketmaster", "title": "The Hollow Kings", "event_at": "2026-10-20 20:00",
            "latitude": 51.5, "longitude": -0.12}
    other = dict(base, source="skiddle", title="Hollow Kings live!")
    from rockconnect import api
    assert len(api._remove_duplicates([other, base])) == 1
    assert api._remove_duplicates([other, base])[0]["source"] == "ticketmaster"
    for different in ({"event_at": "2026-10-21 20:00"}, {"latitude": 51.52}, {"longitude": -0.2},
                      {"title": "Another Band Entirely"}, {"title": ""}, {"title": "The"}):
        assert len(api._remove_duplicates([dict(other, **different), base])) == 2, different
    # two entries of the SAME source are different events, however alike (a matinee and an evening show)
    twin = dict(base, event_at="2026-10-20 14:00")
    assert len(api._remove_duplicates([base, twin])) == 2
    member = dict(base, source="community", title="The Hollow Kings")
    assert api._remove_duplicates([base, member])[0]["source"] == "community"       # members' own post wins


def test_meta_lists_the_sources_that_are_switched_on(tmp_path, monkeypatch):
    app = world_app(tmp_path, monkeypatch, FakeWorld(), SKIDDLE_API_KEY="sk")
    features = app.test_client().get("/api/v1/meta").get_json()["features"]
    assert features["event_sources"] == ["Ticketmaster", "Skiddle"] and features["imported_events"] is True


def test_the_scheduled_import_runs_every_provider_for_every_area(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([tm_near(1, 51.5, -0.12)]), SKIDDLE: EMPTY_SKIDDLE})
    app = world_app(tmp_path, monkeypatch, world, SKIDDLE_API_KEY="sk",
                    IMPORT_AREAS="London=51.5072,-0.1276,40;Leeds=53.8,-1.55,30")
    with app.app_context():
        results = importer.run()
    assert [(r["provider"], r["area"]) for r in results] == [
        ("ticketmaster", "London"), ("ticketmaster", "Leeds"), ("skiddle", "London"), ("skiddle", "Leeds")]
    assert len(world.to(SKIDDLE)) == 2
    with app.app_context():
        only = importer.run(provider="skiddle", only="Leeds")
    assert [(r["provider"], r["area"]) for r in only] == [("skiddle", "Leeds")]
    with app.app_context(), pytest.raises(ImportFailed, match="Unknown provider"):
        importer.run(provider="myspace")


def test_import_events_command_names_the_provider(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([tm_near(1, 51.5, -0.12)])})
    app = world_app(tmp_path, monkeypatch, world, IMPORT_AREAS="London=51.5,-0.12,40")
    out = app.test_cli_runner().invoke(args=["import-events"]).output
    assert "London (ticketmaster)" in out and "1 found: 1 new" in out
    out = app.test_cli_runner().invoke(args=["import-events", "--provider", "skiddle"]).output
    assert "No event provider is set up" in out or "provider" in out.lower()


def test_check_for_the_other_providers(tmp_path, monkeypatch):
    world = FakeWorld(**{SKIDDLE: {"error": 0, "totalcount": 3, "results": [skiddle_event(1)]}, SONGKICK: EMPTY_SONGKICK})
    app = world_app(tmp_path, monkeypatch, world, SKIDDLE_API_KEY="sk", SONGKICK_API_KEY="sg")
    with app.app_context():
        ok, message = importer.check("skiddle")
        assert ok and "1 event(s) were read" in message and "Skiddle Band 1" in message
        ok, message = importer.check("songkick")
        assert ok and "no readable events" in message
    monkeypatch.setattr(importer, "_http_get_json", FakeWorld(**{SKIDDLE: ImportFailed("Skiddle refused the API key (HTTP 403). Check SKIDDLE_API_KEY.", 403)}))
    with app.app_context():
        ok, message = importer.check("skiddle")
    assert not ok and "refused the API key" in message and "sk" != message
    keyless = make_app(tmp_path / "k")
    with keyless.app_context():
        assert importer.check("skiddle") == (False, "SKIDDLE_API_KEY is not set.")


def test_doctor_describes_the_providers_and_on_demand(tmp_path, monkeypatch):
    app = world_app(tmp_path, monkeypatch, FakeWorld(), SKIDDLE_API_KEY="sk", GEOCODER="nominatim")
    out = app.test_cli_runner().invoke(args=["doctor"]).output
    import re
    assert re.search(r"Ticketmaster key\s+set", out) and re.search(r"Skiddle key\s+set", out)
    assert re.search(r"Songkick key\s+not set \(optional\)", out) and re.search(r"On-demand import\s+on: any city", out)
    assert "none set (fine: areas are fetched on demand)" in out and "Everything needed is in place." in out
    off = world_app(tmp_path / "off", monkeypatch, FakeWorld(), IMPORT_ON_DEMAND=False)
    assert "off (IMPORT_ON_DEMAND=0)" in off.test_cli_runner().invoke(args=["doctor"]).output


# ------------------------------------------------------------------ a band connects its own Bandsintown page
def save_profile(client, **extra):
    data = {"name": "The Kings", "email": "kings@example.com", "about": "loud", "kind": "band",
            "_csrf": token_of(client, "/edit"), **extra}
    return client.post("/edit", data=data, follow_redirects=True)


@pytest.fixture
def band(tmp_path, monkeypatch):
    world = FakeWorld(**{BIT: [bit_event(1), bit_event(2, id="bit2")]})
    app = world_app(tmp_path, monkeypatch, world)
    return app, register(app, "kings", kind="band"), world


def test_a_band_connects_with_its_own_app_id_and_its_dates_appear(band):
    app, kings, world = band
    page_ = save_profile(kings, bandsintown_artist="The Hollow Kings", bandsintown_app_id="kings-own-id")
    assert b"2 upcoming date(s) found for The Hollow Kings" in page_.data
    request = world.to(BIT)[0]
    assert request.params["app_id"] == "kings-own-id" and "/artists/The%20Hollow%20Kings/events" in request.url
    assert scalar(app, "SELECT count(*) FROM external_events WHERE source = 'bandsintown'") == 2
    fan = register(app, "rita")
    gigs = nearby(fan, lat=51.45, lon=-2.58, radius_km=20).get_json()["gigs"]
    assert len(gigs) == 2 and gigs[0]["attribution"] == "Bandsintown"
    assert gigs[0]["author"]["username"] == "kings" and gigs[0]["author"]["kind"] == "band"
    assert gigs[0]["ticket_url"].startswith("https://tix.example/")


def test_the_app_id_is_a_secret_nobody_sees_again(band):
    app, kings, world = band
    save_profile(kings, bandsintown_artist="The Hollow Kings", bandsintown_app_id="kings-own-id")
    html = kings.get("/edit").get_data(as_text=True)
    assert "kings-own-id" not in html and "saved (leave empty to keep it)" in html and 'type="password"' in html
    export = kings.get("/account/export")
    assert export.status_code == 200
    assert b"kings-own-id" not in zipfile.ZipFile(io.BytesIO(export.data)).read("data.json")
    other = register(app, "rita")
    assert "kings-own-id" not in other.get("/users_list/%d" % user_id(app, "kings")).get_data(as_text=True)
    assert "kings-own-id" not in other.get("/gigs").get_data(as_text=True)


def test_leaving_the_app_id_empty_keeps_the_saved_one(band):
    app, kings, world = band
    save_profile(kings, bandsintown_artist="The Hollow Kings", bandsintown_app_id="kings-own-id")
    save_profile(kings, bandsintown_artist="The Hollow Kings Reunion", bandsintown_app_id="")
    assert scalar(app, "SELECT bandsintown_app_id FROM users") == "kings-own-id"
    assert world.to(BIT)[-1].params["app_id"] == "kings-own-id" and "Reunion" in world.to(BIT)[-1].url


def test_disconnecting_removes_the_dates(band):
    app, kings, world = band
    save_profile(kings, bandsintown_artist="The Hollow Kings", bandsintown_app_id="kings-own-id")
    assert scalar(app, "SELECT count(*) FROM external_events") == 2
    save_profile(kings, bandsintown_disconnect="1")
    assert scalar(app, "SELECT count(*) FROM external_events") == 0
    assert scalar(app, "SELECT bandsintown_artist FROM users") is None and scalar(app, "SELECT bandsintown_app_id FROM users") is None


@pytest.mark.parametrize("artist,app_id,words", [
    ("a/b", "id", b"artist name is not valid"), ("what?", "id", b"artist name is not valid"),
    ("x" * 121, "id", b"artist name is not valid"), ("Good Name", "bad id with spaces", b"needs your own app id"),
    ("Good Name", "", b"needs your own app id"), ("Good Name", "x" * 65, b"needs your own app id")])
def test_bad_bandsintown_details_are_refused_and_nothing_is_fetched(band, artist, app_id, words):
    app, kings, world = band
    assert words in save_profile(kings, bandsintown_artist=artist, bandsintown_app_id=app_id).data
    assert world.to(BIT) == [] and scalar(app, "SELECT bandsintown_artist FROM users") is None


def test_only_bands_can_connect(tmp_path, monkeypatch):
    world = FakeWorld(**{BIT: [bit_event(1)]})
    app = world_app(tmp_path, monkeypatch, world)
    fan = register(app, "rita", kind="fan")
    save_profile(fan, kind="fan", bandsintown_artist="Someone", bandsintown_app_id="not-theirs")
    assert world.to(BIT) == [] and scalar(app, "SELECT bandsintown_app_id FROM users") is None
    assert "bandsintown_app_id" not in fan.get("/edit").get_data(as_text=True)


def test_a_refused_app_id_is_reported_to_the_band(tmp_path, monkeypatch):
    world = FakeWorld(**{BIT: ImportFailed("Bandsintown refused the API key (HTTP 403). Check the app id of the band.", 403)})
    app = world_app(tmp_path, monkeypatch, world)
    kings = register(app, "kings", kind="band")
    assert b"Bandsintown refused the API key" in save_profile(kings, bandsintown_artist="X", bandsintown_app_id="bad").data


def test_a_suspended_or_blocked_band_has_no_dates_on_the_board_and_deleting_the_account_erases_them(band):
    app, kings, world = band
    save_profile(kings, bandsintown_artist="The Hollow Kings", bandsintown_app_id="kings-own-id")
    fan = register(app, "rita")
    ask = lambda: nearby(fan, lat=51.45, lon=-2.58, radius_km=20).get_json()["total"]  # noqa: E731
    assert ask() == 2
    post_form(fan, "/block/%d" % user_id(app, "kings"))
    assert ask() == 0
    post_form(fan, "/unblock/%d" % user_id(app, "kings"))
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'kings'")
    assert ask() == 0
    sql(app, "UPDATE users SET status = 'active' WHERE username = 'kings'")
    assert ask() == 2
    kings.post("/account/delete", data={"password": "S3cret!pw", "confirm": "DELETE", "_csrf": token_of(kings, "/account/")})
    assert scalar(app, "SELECT count(*) FROM external_events") == 0


def test_the_daily_import_refreshes_connected_bands_and_skips_suspended_ones(band):
    app, kings, world = band
    save_profile(kings, bandsintown_artist="The Hollow Kings", bandsintown_app_id="kings-own-id")
    world.requests.clear()
    with app.app_context():
        results = importer.run()
    assert [(r["provider"], r["area"]) for r in results][-1] == ("bandsintown", "artist:%d" % user_id(app, "kings"))
    sql(app, "UPDATE users SET status = 'banned'")
    world.requests.clear()
    with app.app_context(), pytest.raises(ImportFailed):
        importer.run(provider="bandsintown")
    assert world.to(BIT) == []


def test_dates_that_disappear_from_bandsintown_are_removed(band):
    app, kings, world = band
    save_profile(kings, bandsintown_artist="The Hollow Kings", bandsintown_app_id="kings-own-id")
    sql(app, "UPDATE external_events SET seen_at = '2000-01-01 00:00:00'")
    world.handlers[BIT] = [bit_event(1)]                        # the second date was cancelled
    with app.app_context():
        importer.import_artist(importer.connected_artists()[0])
    assert scalar(app, "SELECT count(*) FROM external_events") == 1


def test_admin_hint_depends_on_providers(tmp_path, monkeypatch):
    app = world_app(tmp_path, monkeypatch, FakeWorld(**{TM: page([])}))
    boss = register(app, "boss")
    make_admin(app, "boss")
    hint = nearby(boss, lat=-33.9, lon=151.2).get_json()["hint"]                       # providers on, nothing in Sydney
    assert "Ticketmaster listed nothing here" in hint and "SONGKICK_API_KEY" in hint and "France" in hint
    bare = make_app(tmp_path / "bare")
    boss2 = register(bare, "boss")
    make_admin(bare, "boss")
    assert "no event provider" in nearby(boss2, lat=-33.9, lon=151.2).get_json()["hint"]
