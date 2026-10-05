# File: test_hidden_events.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""An admin hides an imported event whose data is wrong; it is removed and the importer never brings it back."""
import pytest

from helpers import make_admin, register, scalar, token_of
from rockconnect import importer
from test_import import page, tm_event
from test_worldwide import TM, FakeWorld, nearby, world_app


def wrong_venue_event():
    """A Manchester club that Ticketmaster has pinned in central London."""
    event = tm_event(7, lat="51.51018", lon="-0.13104", venue="The White Hotel", city="Manchester")
    event["name"] = "Gilla Band"
    return event


@pytest.fixture
def site(tmp_path, monkeypatch):
    world = FakeWorld(**{TM: page([wrong_venue_event(), tm_event(8, lat="51.50", lon="-0.12")])})
    app = world_app(tmp_path, monkeypatch, world, IMPORT_ON_DEMAND=False, IMPORT_AREAS="London=51.5072,-0.1276,40")
    with app.app_context():
        importer.run()
    boss, fan = register(app, "boss"), register(app, "rita")
    make_admin(app, "boss")
    return app, boss, fan, world


def event_id(app, external_id="G5v0Z007"):
    return scalar(app, "SELECT id FROM external_events WHERE external_id = :e", e=external_id)


def hide(client, app, reason="venue is in Manchester", external_id="G5v0Z007"):
    eid = event_id(app, external_id)
    return client.post("/events/%d/hide" % eid, data={"reason": reason, "_csrf": token_of(client, "/events/%d" % eid)},
                       follow_redirects=True)


def test_only_admins_see_the_hide_button(site):
    app, boss, fan, world = site
    eid = event_id(app)
    assert "Hide this event" in boss.get("/events/%d" % eid).get_data(as_text=True)
    assert "Hide this event" not in fan.get("/events/%d" % eid).get_data(as_text=True)


def test_hiding_removes_the_event_and_logs_it(site):
    app, boss, fan, world = site
    assert nearby(fan, lat=51.51, lon=-0.13, radius_km=5).get_json()["total"] == 2
    page_ = hide(boss, app)
    assert b"Hidden: it will not come back" in page_.data
    assert scalar(app, "SELECT count(*) FROM external_events WHERE external_id = 'G5v0Z007'") == 0
    assert [g["title"] for g in nearby(fan, lat=51.51, lon=-0.13, radius_km=5).get_json()["gigs"]] == ["Band number 8"]
    row = scalar(app, "SELECT reason FROM hidden_events")
    assert row == "venue is in Manchester" and scalar(app, "SELECT hidden_by FROM hidden_events") == "boss"
    assert scalar(app, "SELECT action FROM mod_log WHERE action = 'hide_event'") == "hide_event"


def test_a_hidden_event_does_not_come_back_at_the_next_refresh(site, monkeypatch):
    app, boss, fan, world = site
    hide(boss, app)
    with app.app_context():
        results = importer.run()
    assert results[0]["hidden"] == 1 and results[0]["new"] == 0
    assert scalar(app, "SELECT count(*) FROM external_events WHERE external_id = 'G5v0Z007'") == 0
    # nor through a member's search on demand
    app.config["IMPORT_ON_DEMAND"] = True
    with app.app_context():
        importer.ensure_coverage(51.51, -0.13, 10)
    assert scalar(app, "SELECT count(*) FROM external_events WHERE external_id = 'G5v0Z007'") == 0
    assert scalar(app, "SELECT count(*) FROM external_events WHERE external_id = 'G5v0Z008'") == 1


def test_the_same_id_from_another_provider_is_not_affected(site):
    app, boss, fan, world = site
    hide(boss, app)
    with app.app_context():
        assert importer._hidden_ids("ticketmaster") == {"G5v0Z007"} and importer._hidden_ids("skiddle") == set()


def test_bringing_it_back(site):
    app, boss, fan, world = site
    hide(boss, app)
    listing = boss.get("/admin/hidden").get_data(as_text=True)
    assert "Gilla Band" in listing and "venue is in Manchester" in listing and "Hidden events" in listing
    hidden_id = scalar(app, "SELECT id FROM hidden_events")
    r = boss.post("/admin/hidden/%d/unhide" % hidden_id, data={"_csrf": token_of(boss, "/admin/hidden")},
                  follow_redirects=True)
    assert b"can be imported again" in r.data and scalar(app, "SELECT count(*) FROM hidden_events") == 0
    with app.app_context():
        importer.run()
    assert scalar(app, "SELECT count(*) FROM external_events WHERE external_id = 'G5v0Z007'") == 1
    assert scalar(app, "SELECT action FROM mod_log WHERE action = 'unhide_event'") == "unhide_event"


def test_members_and_visitors_cannot_hide_or_see_the_list(site):
    app, boss, fan, world = site
    eid = event_id(app)
    assert fan.post("/events/%d/hide" % eid, data={"_csrf": token_of(fan, "/gigs")}).status_code == 404
    assert app.test_client().post("/events/%d/hide" % eid, data={}).status_code in (302, 400)
    assert fan.get("/admin/hidden").status_code == 404
    assert fan.post("/admin/hidden/1/unhide", data={"_csrf": token_of(fan, "/gigs")}).status_code == 404
    assert scalar(app, "SELECT count(*) FROM external_events WHERE external_id = 'G5v0Z007'") == 1


def test_hiding_needs_a_valid_csrf_token_and_an_existing_event(site):
    app, boss, fan, world = site
    eid = event_id(app)
    assert boss.post("/events/%d/hide" % eid, data={"reason": "x"}).status_code == 400
    assert boss.post("/events/99999/hide", data={"_csrf": token_of(boss, "/gigs")}).status_code == 404
    assert scalar(app, "SELECT count(*) FROM hidden_events") == 0


def test_hiding_twice_is_harmless_and_a_default_reason_is_used(site):
    app, boss, fan, world = site
    eid = event_id(app)
    boss.post("/events/%d/hide" % eid, data={"_csrf": token_of(boss, "/gigs")})
    assert scalar(app, "SELECT reason FROM hidden_events") == "wrong data at the provider"
    from rockconnect.db import commit, execute        # a refresh that re-created the row
    with app.app_context():
        execute("INSERT INTO external_events (source, external_id, title, event_at, latitude, longitude, imported_at, seen_at)"
                " VALUES ('ticketmaster', 'G5v0Z007', 'again', '2099-01-01 20:00', 51.5, -0.1, '2026-01-01 00:00:00', '2026-01-01 00:00:00')")
        commit()
    boss.post("/events/%d/hide" % event_id(app), data={"_csrf": token_of(boss, "/gigs")})
    assert scalar(app, "SELECT count(*) FROM hidden_events") == 1
    assert scalar(app, "SELECT count(*) FROM external_events WHERE external_id = 'G5v0Z007'") == 0


def test_a_bands_own_bandsintown_dates_cannot_be_hidden_by_this_button(tmp_path, monkeypatch):
    from test_providers import bit_event
    from test_worldwide import BIT, save_profile
    world = FakeWorld(**{BIT: [bit_event(1)]})
    app = world_app(tmp_path, monkeypatch, world)
    kings = register(app, "kings", kind="band")
    save_profile(kings, bandsintown_artist="The Kings", bandsintown_app_id="own-id")
    boss = register(app, "boss")
    make_admin(app, "boss")
    eid = scalar(app, "SELECT id FROM external_events")
    r = boss.post("/events/%d/hide" % eid, data={"_csrf": token_of(boss, "/gigs")}, follow_redirects=True)
    assert b"the band manages it" in r.data and scalar(app, "SELECT count(*) FROM external_events") == 1
    assert "Hide this event" not in boss.get("/events/%d" % eid).get_data(as_text=True)


def test_dry_run_still_reports_hidden_ones(site):
    app, boss, fan, world = site
    hide(boss, app)
    with app.app_context():
        results = importer.run(dry_run=True)
    assert results[0]["hidden"] == 1 and results[0]["fetched"] == 2


def test_migration_creates_the_table(tmp_path):
    from sqlalchemy import create_engine, inspect
    from rockconnect.migrate import upgrade
    engine = create_engine("sqlite:///" + str(tmp_path / "m.sqlite"))
    upgrade(engine, "0005")
    assert "hidden_events" not in inspect(engine).get_table_names()
    upgrade(engine)
    assert "hidden_events" in inspect(engine).get_table_names()
