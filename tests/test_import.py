# File: test_import.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Importing gigs from Ticketmaster: parsing, storing, cleaning up, the CLI, and showing them in 'near me'."""
import json
import urllib.error
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest

from helpers import make_app, register, scalar, sql
from rockconnect import importer

LONDON = (51.5072, -0.1276)
KEY = "secret-api-key-123"


def in_days(days, hour=20):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d"), "%02d:00:00" % hour


def tm_event(n, days=3, lat="51.5033", lon="-0.1195", venue="O2 Academy Brixton", city="London", **start_over):
    """An event shaped like the Discovery API's answer."""
    day, clock = in_days(days)
    start = {"localDate": day, "localTime": clock, "dateTime": day + "T" + clock + "Z"}
    start.update(start_over)
    return {
        "id": "G5v0Z%03d" % n, "name": "Band number %d" % n, "url": "https://www.ticketmaster.co.uk/event/%d" % n,
        "dates": {"start": start},
        "classifications": [{"genre": {"name": "Rock"}, "subGenre": {"name": "Indie"}}],
        "_embedded": {"venues": [{"name": venue, "city": {"name": city}, "country": {"countryCode": "GB"},
                                  "location": {"latitude": lat, "longitude": lon}}]},
    }


def page(events, total_pages=1, number=0):
    return {"_embedded": {"events": events}, "page": {"size": 200, "totalPages": total_pages, "number": number}}


class FakeTicketmaster:
    """Stands in for _http_get_json: answers with queued pages and remembers every URL asked."""

    def __init__(self, *pages):
        self.pages, self.urls = list(pages), []

    def __call__(self, url):
        self.urls.append(url)
        answer = self.pages.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def params(self, n=0):
        return {k: v[0] for k, v in parse_qs(urlparse(self.urls[n]).query).items()}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(importer.time, "sleep", lambda s: None)
    return make_app(tmp_path, TICKETMASTER_API_KEY=KEY, IMPORT_AREAS="London=51.5072,-0.1276,40")


def run(app, fake, monkeypatch, **kw):
    monkeypatch.setattr(importer, "_http_get_json", fake)
    with app.app_context():
        return importer.run(**kw)


def count(app):
    return scalar(app, "SELECT count(*) FROM external_events")


# ------------------------------------------------------------------ configuration
def test_areas_are_parsed_and_checked():
    areas = importer.parse_areas("London=51.5072,-0.1276,40; Dublin = 53.35,-6.26, 30 ;;")
    assert [(a.name, a.lat, a.lon, a.radius_km) for a in areas] == [("London", 51.5072, -0.1276, 40.0),
                                                                    ("Dublin", 53.35, -6.26, 30.0)]
    assert importer.parse_areas("") == [] and importer.parse_areas(None) == []
    for bad in ("London", "London=1,2", "London=a,b,c", "London=95,0,10", "London=51,0,0", "London=51,0,9999",
                "=51,0,10", "A=1,1,5;a=2,2,5"):
        with pytest.raises(importer.ImportFailed):
            importer.parse_areas(bad)


def test_nothing_runs_without_a_key_or_areas(tmp_path):
    for config, words in (({"IMPORT_AREAS": "London=51.5,-0.12,40"}, "TICKETMASTER_API_KEY"),
                          ({"TICKETMASTER_API_KEY": KEY}, "IMPORT_AREAS")):
        app = make_app(tmp_path, **config)
        with app.app_context(), pytest.raises(importer.ImportFailed, match=words):
            importer.run()


# ------------------------------------------------------------------ reading Ticketmaster's answer
def test_a_normal_event_is_understood():
    item = importer.normalise_ticketmaster(tm_event(1))
    assert item["source"] == "ticketmaster" and item["external_id"] == "G5v0Z001"
    assert item["title"] == "Band number 1" and item["venue"] == "O2 Academy Brixton" and item["city"] == "London"
    assert item["event_at"].endswith(" 20:00") and item["time_known"] == 1
    assert (item["latitude"], item["longitude"]) == (51.5033, -0.1195)
    assert item["genre"] == "Rock" and item["ticket_url"] == "https://www.ticketmaster.co.uk/event/1"


@pytest.mark.parametrize("breakage", [
    lambda e: e["dates"]["start"].update(dateTBA=True),
    lambda e: e["dates"]["start"].pop("localDate"),
    lambda e: e["dates"]["start"].update(localDate="not-a-date"),
    lambda e: e["_embedded"].pop("venues"),
    lambda e: e["_embedded"].update(venues=[]),
    lambda e: e["_embedded"]["venues"][0].pop("location"),
    lambda e: e["_embedded"]["venues"][0]["location"].update(latitude="999"),
    lambda e: e["_embedded"]["venues"][0]["location"].update(latitude="nan"),
    lambda e: e.update(name=""),
    lambda e: e.pop("id"),
])
def test_unusable_events_are_skipped_not_crashed_on(breakage):
    event = tm_event(1)
    breakage(event)
    assert importer.normalise_ticketmaster(event) is None


def test_odd_but_usable_events():
    no_time = importer.normalise_ticketmaster(tm_event(1, localTime=None))
    assert no_time["time_known"] == 0 and no_time["event_at"].endswith(" 00:00")
    tbd = tm_event(2); tbd["dates"]["start"]["timeTBA"] = True
    assert importer.normalise_ticketmaster(tbd)["time_known"] == 0
    unsafe = tm_event(3); unsafe["url"] = "javascript:alert(1)"
    assert importer.normalise_ticketmaster(unsafe)["ticket_url"] is None          # never becomes a clickable link
    insecure = tm_event(4); insecure["url"] = "http://example.com/x"
    assert importer.normalise_ticketmaster(insecure)["ticket_url"] is None
    undefined = tm_event(5); undefined["classifications"] = [{"genre": {"name": "Undefined"}}]
    assert importer.normalise_ticketmaster(undefined)["genre"] is None
    long_title = tm_event(6); long_title["name"] = "x" * 999
    assert len(importer.normalise_ticketmaster(long_title)["title"]) == 255


# ------------------------------------------------------------------ the request we send
def test_the_request_asks_for_music_near_the_area(app, monkeypatch):
    fake = FakeTicketmaster(page([tm_event(1)]))
    run(app, fake, monkeypatch)
    p = fake.params()
    assert p["apikey"] == KEY and p["classificationName"] == "music" and p["unit"] == "km"
    assert p["latlong"] == "51.5072,-0.1276" and p["radius"] == "40" and p["sort"] == "date,asc"
    assert p["size"] == "200" and p["page"] == "0"
    start, end = (datetime.strptime(p[k], "%Y-%m-%dT%H:%M:%SZ") for k in ("startDateTime", "endDateTime"))
    assert 119 <= (end - start).days <= 120                                      # IMPORT_DAYS_AHEAD


def test_several_pages_are_read_with_a_pause_between(app, monkeypatch):
    pauses = []
    monkeypatch.setattr(importer.time, "sleep", pauses.append)
    fake = FakeTicketmaster(page([tm_event(1)], total_pages=3, number=0), page([tm_event(2)], 3, 1),
                            page([tm_event(3)], 3, 2))
    results = run(app, fake, monkeypatch)
    assert results[0]["new"] == 3 and [fake.params(i)["page"] for i in range(3)] == ["0", "1", "2"]
    assert len(pauses) == 2 and all(p >= 0.2 for p in pauses)                    # 5 requests/second is the limit


def at(n, hour, days=3):
    """An event that starts at a known UTC moment (so the 'resume from here' logic has something to read)."""
    event = tm_event(n, days=days)
    day, _ = in_days(days)
    event["dates"]["start"]["dateTime"] = "%sT%02d:00:00Z" % (day, hour)
    return event


def test_one_search_never_asks_for_a_page_the_api_refuses(app, monkeypatch):
    # every page of the first search ends at 10:00; a resume would start at 10:00 too, i.e. no progress: it stops
    fake = FakeTicketmaster(*[page([at(i, 10)], total_pages=50, number=i) for i in range(5)],
                            page([at(50, 10)], total_pages=1, number=0))
    run(app, fake, monkeypatch)
    pages_asked = [int(params_of(u)["page"]) for u in fake.urls]
    assert pages_asked[:importer.MAX_PAGES] == list(range(importer.MAX_PAGES))
    assert max(pages_asked) < importer.MAX_PAGES                  # page 5 and above are refused by the API


def test_a_cut_off_search_resumes_where_it_ended_and_nothing_is_stored_twice(app, monkeypatch):
    first = [page([at(i, 10 + i)], total_pages=9, number=i) for i in range(importer.MAX_PAGES)]     # cut off, last at 14:00
    second = [page([at(4, 14), at(100, 15)], total_pages=1, number=0)]                                  # 4 repeats at the seam
    fake = FakeTicketmaster(*first, *second)
    results = run(app, fake, monkeypatch)
    assert len(fake.urls) == importer.MAX_PAGES + 1
    resume = params_of(fake.urls[-1])
    day, _ = in_days(3)
    assert resume["startDateTime"] == day + "T14:00:00Z" and resume["page"] == "0"        # restarts from the last event
    assert results[0]["new"] == importer.MAX_PAGES + 1 and count(app) == importer.MAX_PAGES + 1      # event 4 only once
    assert fake.params(0)["endDateTime"] == resume["endDateTime"]                          # same overall period


def test_a_busy_city_is_read_completely_in_several_searches(app, monkeypatch):
    searches = []
    for window in range(3):
        base = window * 10
        pages_ = [page([at(base + i, 8 + window * 2)], total_pages=7 if window < 2 else 1, number=i)
                  for i in range(importer.MAX_PAGES if window < 2 else 1)]
        searches.extend(pages_)
        # each search ends later than the previous one started
        for index, pg in enumerate(pages_):
            for ev in pg["_embedded"]["events"]:
                ev["dates"]["start"]["dateTime"] = "%sT%02d:%02d:00Z" % (in_days(3)[0], 8 + window * 3, index)
    fake = FakeTicketmaster(*searches)
    results = run(app, fake, monkeypatch)
    assert results[0]["new"] == importer.MAX_PAGES * 2 + 1 and len(fake.urls) == len(searches)


def test_it_stops_when_a_search_makes_no_progress(app, monkeypatch):
    """If a whole search ends at the very moment it started, asking again would loop for ever."""
    same = lambda i: at(i, 10)  # noqa: E731
    fake = FakeTicketmaster(*[page([same(i)], total_pages=99, number=i % 5) for i in range(5)],
                            *[page([same(10 + i)], total_pages=99, number=i % 5) for i in range(5)])
    run(app, fake, monkeypatch)
    assert len(fake.urls) <= 2 * importer.MAX_PAGES                                         # one resume, then stops


def test_there_is_a_hard_limit_on_searches(app, monkeypatch):
    calls = []

    def endless(url):
        calls.append(url)
        n = len(calls)
        event = at(n, 0)
        event["dates"]["start"]["dateTime"] = "%sT%02d:%02d:00Z" % (in_days(3)[0], n // 60 % 24, n % 60)  # always later
        return page([event], total_pages=99, number=0)

    monkeypatch.setattr(importer, "_http_get_json", endless)
    with app.app_context():
        importer.run()
    assert len(calls) <= importer.MAX_SEARCHES * importer.MAX_PAGES


def params_of(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


# ------------------------------------------------------------------ storing, updating, cleaning up
def test_events_are_stored_then_updated_not_duplicated(app, monkeypatch):
    results = run(app, FakeTicketmaster(page([tm_event(1), tm_event(2)])), monkeypatch)
    assert results[0]["new"] == 2 and results[0]["updated"] == 0 and count(app) == 2
    changed = tm_event(1, venue="Roundhouse"); changed["name"] = "Band number 1 (new date)"
    results = run(app, FakeTicketmaster(page([changed, tm_event(2)])), monkeypatch)
    assert results[0]["new"] == 0 and results[0]["updated"] == 2 and count(app) == 2
    assert scalar(app, "SELECT venue FROM external_events WHERE external_id = 'G5v0Z001'") == "Roundhouse"
    assert scalar(app, "SELECT title FROM external_events WHERE external_id = 'G5v0Z001'").endswith("(new date)")


def test_events_that_disappear_from_the_listing_are_removed(app, monkeypatch):
    run(app, FakeTicketmaster(page([tm_event(1), tm_event(2), tm_event(3)])), monkeypatch)
    sql(app, "UPDATE external_events SET seen_at = '2000-01-01 00:00:00'")       # all were seen "long ago"
    results = run(app, FakeTicketmaster(page([tm_event(1)])), monkeypatch)        # now only #1 is still listed
    assert results[0]["removed"] == 2 and count(app) == 1
    assert scalar(app, "SELECT external_id FROM external_events") == "G5v0Z001"


def test_old_stale_and_finished_events_are_pruned(app, monkeypatch):
    run(app, FakeTicketmaster(page([tm_event(1), tm_event(2), tm_event(3)])), monkeypatch)
    sql(app, "UPDATE external_events SET event_at = '2020-01-01 20:00' WHERE external_id = 'G5v0Z001'")   # over
    sql(app, "UPDATE external_events SET seen_at = '2000-01-01 00:00:00' WHERE external_id = 'G5v0Z002'")  # not refreshed
    with app.app_context():
        assert importer.prune() == 2
    assert count(app) == 1 and scalar(app, "SELECT external_id FROM external_events") == "G5v0Z003"


def test_a_failing_area_does_not_stop_the_others_or_lose_data(tmp_path, monkeypatch):
    monkeypatch.setattr(importer.time, "sleep", lambda s: None)
    app = make_app(tmp_path, TICKETMASTER_API_KEY=KEY, IMPORT_AREAS="London=51.5,-0.12,40;Dublin=53.35,-6.26,30")
    run(app, FakeTicketmaster(page([tm_event(1)])), monkeypatch, only="London")
    fake = FakeTicketmaster(importer.ImportFailed("Ticketmaster answered HTTP 500."),
                            page([tm_event(9, lat="53.34", lon="-6.26", city="Dublin")]))
    results = run(app, fake, monkeypatch)
    assert results[0]["error"] and "HTTP 500" in results[0]["error"] and results[1]["error"] is None
    assert count(app) == 2                                                       # London's old data survived
    assert scalar(app, "SELECT count(*) FROM external_events WHERE area = 'London'") == 1


def test_a_half_read_area_is_not_stored(app, monkeypatch):
    fake = FakeTicketmaster(page([tm_event(1)], total_pages=2), importer.ImportFailed("boom"))
    results = run(app, fake, monkeypatch)
    assert results[0]["error"] == "boom" and count(app) == 0


def test_dry_run_changes_nothing(app, monkeypatch):
    results = run(app, FakeTicketmaster(page([tm_event(1), tm_event(2)])), monkeypatch, dry_run=True)
    assert results[0]["fetched"] == 2 and count(app) == 0


def test_unknown_area_name(app, monkeypatch):
    with app.app_context(), pytest.raises(importer.ImportFailed, match="No area called"):
        importer.run(only="Paris")


# ------------------------------------------------------------------ HTTP errors, and the key staying secret
def http_error(code):
    return urllib.error.HTTPError("https://app.ticketmaster.com/x?apikey=" + KEY, code, "x", {}, None)


@pytest.mark.parametrize("raised,words", [
    (http_error(401), "refused the API key"), (http_error(403), "refused the API key"),
    (http_error(429), "too often"), (http_error(500), "HTTP 500"),
    (urllib.error.URLError("no route"), "Could not reach"), (TimeoutError(), "Could not reach"),
    (ValueError("bad json"), "Could not reach"),
])
def test_http_problems_become_clear_messages_without_the_key(app, monkeypatch, raised, words):
    def failing_urlopen(request, timeout):
        raise raised
    monkeypatch.setattr(importer.urllib.request, "urlopen", failing_urlopen)
    with app.app_context(), pytest.raises(importer.ImportFailed, match=words) as info:
        importer._http_get_json("https://app.ticketmaster.com/x?apikey=" + KEY)
    assert KEY not in str(info.value)


def test_the_key_never_appears_in_logs_or_output(app, monkeypatch, capsys):
    import rockconnect.baclog as baclog
    baclog.ENABLED = True
    try:
        run(app, FakeTicketmaster(page([tm_event(1)])), monkeypatch)
        run(app, FakeTicketmaster(importer.ImportFailed("Ticketmaster refused the API key (HTTP 401).")), monkeypatch)
    finally:
        baclog.ENABLED = False
    out = capsys.readouterr()
    assert KEY not in out.out and KEY not in out.err and "import" in out.out


# ------------------------------------------------------------------ the command line
def test_cli_import_and_dry_run(app, monkeypatch):
    monkeypatch.setattr(importer, "_http_get_json", FakeTicketmaster(page([tm_event(1)]), page([tm_event(1)])))
    runner = app.test_cli_runner()
    dry = runner.invoke(args=["import-events", "--dry-run"])
    assert dry.exit_code == 0 and "1 found (dry run)" in dry.output and count(app) == 0
    real = runner.invoke(args=["import-events"])
    assert real.exit_code == 0 and "London" in real.output and "1 new" in real.output and count(app) == 1


def test_cli_explains_a_missing_key_and_exits_with_an_error(tmp_path):
    app = make_app(tmp_path, IMPORT_AREAS="London=51.5,-0.12,40")
    result = app.test_cli_runner().invoke(args=["import-events"])
    assert result.exit_code != 0 and "TICKETMASTER_API_KEY" in result.output and "developer.ticketmaster.com" in result.output


def test_cli_exit_code_is_nonzero_when_an_area_fails(app, monkeypatch):
    monkeypatch.setattr(importer, "_http_get_json", FakeTicketmaster(importer.ImportFailed("Ticketmaster answered HTTP 500.")))
    result = app.test_cli_runner().invoke(args=["import-events"])
    assert result.exit_code == 1 and "FAILED" in result.output          # so cron / monitoring notices


def test_cli_prune(app, monkeypatch):
    run(app, FakeTicketmaster(page([tm_event(1)])), monkeypatch)
    sql(app, "UPDATE external_events SET event_at = '2020-01-01 20:00'")
    assert "1 imported event(s) removed" in app.test_cli_runner().invoke(args=["prune-events"]).output


# ------------------------------------------------------------------ showing them in "near me"
def nearby(client, **params):
    return client.get("/api/v1/gigs/nearby", query_string=params)


@pytest.fixture
def imported(app, monkeypatch):
    run(app, FakeTicketmaster(page([tm_event(1, days=3), tm_event(2, days=1, lat="51.4545", lon="-0.9781", city="Reading"),
                                    tm_event(3, days=2, lat="53.4808", lon="-2.2426", city="Manchester")])), monkeypatch)
    return app, register(app, "rita")


def test_imported_events_are_found_near_london_and_not_far_away(imported):
    app, fan = imported
    body = nearby(fan, lat=51.5072, lon=-0.1276, radius_km=60).get_json()
    assert [g["title"] for g in body["gigs"]] == ["Band number 1", "Band number 2"]    # Manchester is 260 km away
    first = body["gigs"][0]
    assert first["source"] == "ticketmaster" and first["attribution"] == "Ticketmaster"
    assert first["ticket_url"] == "https://www.ticketmaster.co.uk/event/1" and first["author"] is None
    assert first["place"] == "O2 Academy Brixton, London" and first["genre"] == "Rock" and first["distance_km"] < 5
    assert first["url"].startswith("/events/") and first["event_label"].endswith("20:00")
    assert nearby(fan, lat=51.5072, lon=-0.1276, radius_km=400).get_json()["total"] == 3


def test_source_filter_and_mixing_with_member_gigs(imported):
    app, fan = imported
    band = register(app, "kings", kind="band")
    from test_nearby import announce
    announce(band, "Camden Underworld", 51.5391, -0.1426, days=5, body="our own gig")
    everything = nearby(fan, lat=51.5072, lon=-0.1276, radius_km=60).get_json()
    assert {g["source"] for g in everything["gigs"]} == {"community", "ticketmaster"} and everything["total"] == 3
    only_members = nearby(fan, lat=51.5072, lon=-0.1276, radius_km=60, source="community").get_json()
    assert [g["body"] for g in only_members["gigs"]] == ["our own gig"]
    only_imported = nearby(fan, lat=51.5072, lon=-0.1276, radius_km=60, source="external").get_json()
    assert {g["source"] for g in only_imported["gigs"]} == {"ticketmaster"} and only_imported["total"] == 2
    bad = nearby(fan, lat=51.5, lon=-0.1, source="everything")
    assert bad.status_code == 400 and bad.get_json()["code"] == "bad_source"
    # sorted by distance across both kinds
    distances = [g["distance_km"] for g in everything["gigs"]]
    assert distances == sorted(distances)


def test_past_imported_events_are_not_returned(imported):
    app, fan = imported
    sql(app, "UPDATE external_events SET event_at = '2020-01-01 20:00'")
    assert nearby(fan, lat=51.5072, lon=-0.1276, radius_km=400).get_json()["total"] == 0


def test_event_page(imported):
    app, fan = imported
    eid = scalar(app, "SELECT id FROM external_events WHERE external_id = 'G5v0Z001'")
    page_ = fan.get("/events/%d" % eid)
    html = page_.get_data(as_text=True)
    assert page_.status_code == 200 and "Band number 1" in html and "O2 Academy Brixton" in html
    assert 'href="https://www.ticketmaster.co.uk/event/1"' in html and "Get tickets on Ticketmaster" in html
    assert 'rel="noopener nofollow sponsored"' in html and "not the seller" in html
    assert "openstreetmap.org/?mlat=51.5033" in html
    assert fan.get("/events/99999").status_code == 404
    anon = app.test_client()
    assert anon.get("/events/%d" % eid).status_code == 302


def test_event_without_a_clock_time_shows_only_the_date(app, monkeypatch):
    run(app, FakeTicketmaster(page([tm_event(1, localTime=None)])), monkeypatch)
    fan = register(app, "rita")
    gig = nearby(fan, lat=51.5072, lon=-0.1276).get_json()["gigs"][0]
    assert ":" not in gig["event_label"]
    eid = scalar(app, "SELECT id FROM external_events")
    assert ":" not in fan.get("/events/%d" % eid).get_data(as_text=True).split("gig-when")[1].split("</span>")[0]


def test_panel_marks_imported_results_in_the_page_script(imported):
    app, fan = imported
    script = fan.get("/static/js/gigs.js").get_data(as_text=True)
    assert "gig.ticket_url" in script and "gig.attribution" in script and 'rel = "noopener nofollow sponsored"' in script
    # a slow answer for an earlier search must never replace the result of a newer one
    assert "var mine = ++latest" in script and "if (mine !== latest)" in script


# ------------------------------------------------------------------ for the Android app
def test_meta_endpoint_is_public_and_describes_the_server(app):
    body = app.test_client().get("/api/v1/meta").get_json()
    assert body["api_version"] == 1 and body["server_version"]
    assert set(body["site"]) == {"name", "tagline", "accent_color", "contact"}
    assert body["features"] == {"nearby_gigs": True, "imported_events": True, "event_sources": ["Ticketmaster"],
                                "place_search": False, "going": True, "people_search": True, "message_requests": True,
                                "follows": True, "private_accounts": True, "notifications": True, "skill_levels": True, "gig_comments": True,
                                "calendar": True, "age_check": True}
    assert KEY not in json.dumps(body)                                           # only a yes/no, never the key


def test_meta_reflects_branding_and_missing_import(tmp_path):
    app = make_app(tmp_path, SITE_NAME="GigHub", ACCENT_COLOR="#2ec4b6")
    body = app.test_client().get("/api/v1/meta").get_json()
    assert body["site"]["name"] == "GigHub" and body["site"]["accent_color"] == "#2ec4b6"
    assert body["features"]["imported_events"] is False


def test_api_lives_under_v1_and_old_path_is_gone(app):
    fan = register(app, "rita")
    assert nearby(fan, lat=1, lon=1).status_code == 200
    assert fan.get("/api/gigs/nearby?lat=1&lon=1").status_code == 404


def test_every_api_item_has_the_same_fields_whatever_its_source(imported):
    """An app parses one shape: community and imported gigs must carry exactly the same keys."""
    app, fan = imported
    band = register(app, "kings", kind="band")
    from test_nearby import announce
    announce(band, "Camden", 51.54, -0.14, days=5)
    gigs = nearby(fan, lat=51.5072, lon=-0.1276, radius_km=60).get_json()["gigs"]
    assert len({frozenset(g) for g in gigs}) == 1


def test_migration_adds_the_table(tmp_path):
    from sqlalchemy import create_engine, inspect
    from rockconnect.migrate import upgrade
    engine = create_engine("sqlite:///" + str(tmp_path / "m.sqlite"))
    upgrade(engine, "0003")
    assert "external_events" not in inspect(engine).get_table_names()
    upgrade(engine)
    assert "external_events" in inspect(engine).get_table_names()
