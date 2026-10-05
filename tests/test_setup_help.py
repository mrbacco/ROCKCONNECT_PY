# File: test_setup_help.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Making setup problems obvious: the .env file, `flask doctor`, the admin hint, the area-parameter fallback."""
import os
from urllib.parse import parse_qs, urlparse

import pytest

from helpers import make_admin, make_app, register
from rockconnect import envfile, importer
from test_import import FakeTicketmaster, KEY, nearby, page, tm_event


# ------------------------------------------------------------------ the .env file
def test_parse_handles_the_usual_shapes():
    text = '''
# a comment
SITE_NAME=GigHub
export ACCENT_COLOR="#2ec4b6"
SECRET='abc def'
IMPORT_AREAS=London=51.5072,-0.1276,40;Dublin=53.35,-6.26,30
WITH_COMMENT=value   # trailing comment
EMPTY=
no equals sign here
 SPACED = padded
BAD KEY=ignored
'''
    assert dict(envfile.parse(text)) == {
        "SITE_NAME": "GigHub", "ACCENT_COLOR": "#2ec4b6", "SECRET": "abc def",
        "IMPORT_AREAS": "London=51.5072,-0.1276,40;Dublin=53.35,-6.26,30", "WITH_COMMENT": "value",
        "EMPTY": "", "SPACED": "padded"}


def test_load_sets_missing_variables_but_never_overrides_real_ones(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("A_ONE=from-file\nA_TWO=from-file\nA_BLANK=\n", encoding="utf-8")
    monkeypatch.setenv("A_TWO", "from-environment")
    monkeypatch.delenv("A_ONE", raising=False)
    monkeypatch.delenv("A_BLANK", raising=False)
    loaded = envfile.load(str(path))
    assert loaded == ["A_ONE"] and os.environ["A_ONE"] == "from-file"
    assert os.environ["A_TWO"] == "from-environment"          # the real environment wins
    assert "A_BLANK" not in os.environ                         # an empty value (like in .env.example) sets nothing
    monkeypatch.delenv("A_ONE")


def test_windows_bom_and_missing_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_bytes("﻿BOM_KEY=works\r\nOTHER=1\r\n".encode("utf-8"))
    monkeypatch.delenv("BOM_KEY", raising=False)
    monkeypatch.delenv("OTHER", raising=False)
    assert envfile.load(str(path)) == ["BOM_KEY", "OTHER"] and os.environ["BOM_KEY"] == "works"
    monkeypatch.delenv("BOM_KEY"), monkeypatch.delenv("OTHER")
    assert envfile.load(str(tmp_path / "nothing-here")) == []


def test_values_are_not_logged(tmp_path, monkeypatch, capsys):
    import rockconnect.baclog as baclog
    path = tmp_path / ".env"
    path.write_text("TOP_SECRET_VALUE_KEY=hunter2-very-secret\n")
    monkeypatch.delenv("TOP_SECRET_VALUE_KEY", raising=False)
    baclog.ENABLED = True
    try:
        envfile.load(str(path))
    finally:
        baclog.ENABLED = False
        os.environ.pop("TOP_SECRET_VALUE_KEY", None)
    assert "hunter2" not in capsys.readouterr().out


def test_both_entry_points_read_the_file_but_the_app_itself_does_not():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("run.py", "wsgi.py"):
        assert "envfile.load()" in open(os.path.join(root, name), encoding="utf-8").read()
    init = open(os.path.join(root, "rockconnect", "__init__.py"), encoding="utf-8").read()
    assert "envfile" not in init               # tests must never depend on a developer's own .env


def test_a_plain_copy_of_env_example_starts_a_working_local_site(tmp_path, monkeypatch):
    """The first thing a new user does is copy .env.example to .env. It must run as it is."""
    from rockconnect import settings
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    copy = tmp_path / ".env"
    copy.write_text(open(os.path.join(root, ".env.example"), encoding="utf-8").read(), encoding="utf-8")
    for name in list(os.environ):                       # a clean environment, as on a new machine
        if name in dict(envfile.parse(copy.read_text())) or name in ("APP_ENV", "SECRET_KEY", "TRUST_PROXY"):
            monkeypatch.delenv(name)
    loaded = envfile.load(str(copy))
    try:
        config = settings.build()
        settings.validate(config)                       # raises if the example puts us in unsafe production mode
        assert config["APP_ENV"] == "development" and "SECRET_KEY" not in loaded and "APP_ENV" not in loaded
        assert config["TICKETMASTER_API_KEY"] == "" and config["TRUST_PROXY"] is False
    finally:
        for name in loaded:
            os.environ.pop(name, None)


def test_env_example_holds_no_secrets():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    text = open(os.path.join(root, ".env.example"), encoding="utf-8").read()
    for line in text.splitlines():
        name, _, value = line.lstrip("# ").partition("=")
        if name.strip() in ("TICKETMASTER_API_KEY", "SECRET_KEY", "POSTGRES_PASSWORD", "SMTP_PASSWORD",
                            "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID"):
            assert value.strip() == "", "%s must be empty in .env.example (it is committed to git)" % name


def test_the_unsafe_production_error_says_how_to_fix_it(tmp_path):
    with pytest.raises(RuntimeError) as info:
        make_app(tmp_path, APP_ENV="production")
    text = str(info.value)
    assert "APP_ENV=development" in text and "secrets.token_hex(32)" in text and "SECRET_KEY" in text


# ------------------------------------------------------------------ geohash + the fallback
def test_geohash_matches_the_published_reference():
    assert importer.geohash(57.64911, 10.40744, 11) == "u4pruydqqvj"          # the standard example
    assert importer.geohash(51.5074, -0.1278, 5) == "gcpvj"                    # London
    assert importer.geohash(-33.8688, 151.2093, 4) == "r3gx"                   # Sydney
    assert len(importer.geohash(0, 0)) == 9


def params(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(importer.time, "sleep", lambda s: None)
    return make_app(tmp_path, TICKETMASTER_API_KEY=KEY, IMPORT_AREAS="London=51.5072,-0.1276,40")


def test_if_latlong_is_rejected_the_geohash_form_is_tried_and_kept(app, monkeypatch):
    fake = FakeTicketmaster(importer.ImportFailed("Ticketmaster answered HTTP 400.", 400),
                            page([tm_event(1)], total_pages=2), page([tm_event(2)], 2, 1))
    monkeypatch.setattr(importer, "_http_get_json", fake)
    with app.app_context():
        result = importer.run()
    assert result[0]["new"] == 2 and result[0]["error"] is None
    first, second, third = (params(u) for u in fake.urls)
    assert "latlong" in first and "geoPoint" not in first
    assert second["geoPoint"].startswith("gcpv") and "latlong" not in second
    assert "geoPoint" in third and third["page"] == "1"                        # keeps using what works


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_other_errors_do_not_trigger_the_fallback(app, monkeypatch, status):
    fake = FakeTicketmaster(importer.ImportFailed("nope", status))
    monkeypatch.setattr(importer, "_http_get_json", fake)
    with app.app_context():
        result = importer.run()
    assert result[0]["error"] == "nope" and len(fake.urls) == 1


def test_both_forms_rejected_is_reported_clearly(app, monkeypatch):
    fake = FakeTicketmaster(importer.ImportFailed("Ticketmaster answered HTTP 400.", 400),
                            importer.ImportFailed("Ticketmaster answered HTTP 400.", 400))
    monkeypatch.setattr(importer, "_http_get_json", fake)
    with app.app_context():
        assert "HTTP 400" in importer.run()[0]["error"]


# ------------------------------------------------------------------ the key check
def test_check_reports_a_working_key(app, monkeypatch):
    monkeypatch.setattr(importer, "_http_get_json", FakeTicketmaster(
        {"page": {"size": 1, "totalElements": 321, "totalPages": 321, "number": 0}}))
    with app.app_context():
        ok, message = importer.check()
    assert ok and "321 upcoming concerts" in message and "London" in message and KEY not in message


def test_check_reports_a_refused_key_without_leaking_it(app, monkeypatch):
    monkeypatch.setattr(importer, "_http_get_json", FakeTicketmaster(importer.ImportFailed(
        "Ticketmaster refused the API key (HTTP 401). Check TICKETMASTER_API_KEY.", 401)))
    with app.app_context():
        ok, message = importer.check()
    assert not ok and "refused the API key" in message and KEY not in message


def test_check_without_a_key(tmp_path):
    app = make_app(tmp_path)
    with app.app_context():
        assert importer.check() == (False, "TICKETMASTER_API_KEY is not set.")


def test_check_uses_one_tiny_request(app, monkeypatch):
    fake = FakeTicketmaster({"page": {"totalElements": 0}})
    monkeypatch.setattr(importer, "_http_get_json", fake)
    with app.app_context():
        importer.check()
    assert len(fake.urls) == 1 and params(fake.urls[0])["size"] == "1"


# ------------------------------------------------------------------ flask doctor
def test_doctor_on_an_empty_unconfigured_site_says_what_is_wrong(tmp_path):
    app = make_app(tmp_path)
    out = app.test_cli_runner().invoke(args=["doctor"]).output
    assert "NOT SET: no concerts can be imported" in out and "NOT SET: say where your scene is" in out
    assert "NO gigs to find yet" in out and "docs/EVENT-IMPORT.md" in out
    assert "Place lookups" in out and "OFF (GEOCODER=none)" in out            # tests run with lookups off


def test_doctor_is_happy_when_everything_is_in_place(app, monkeypatch):
    monkeypatch.setattr(importer, "_http_get_json", FakeTicketmaster(page([tm_event(1)])))
    with app.app_context():
        importer.run()
    app.config["GEOCODER"] = "nominatim"
    out = app.test_cli_runner().invoke(args=["doctor"]).output
    assert "Imported events    1" in out and "London (40 km)" in out and "Everything needed is in place." in out
    assert "[!]" not in out


def test_doctor_online_tests_the_key(app, monkeypatch):
    monkeypatch.setattr(importer, "_http_get_json", FakeTicketmaster({"page": {"totalElements": 77}}))
    out = app.test_cli_runner().invoke(args=["doctor", "--online"]).output
    assert "Key check" in out and "77 upcoming concerts" in out and KEY not in out


def test_doctor_flags_invalid_areas_and_counts_member_gigs(tmp_path):
    app = make_app(tmp_path, TICKETMASTER_API_KEY=KEY, IMPORT_AREAS="London=oops")
    band = register(app, "kings", kind="band")
    from test_nearby import announce
    announce(band, "Camden", 51.54, -0.14)
    announce(band, "Nowhere in particular")
    out = app.test_cli_runner().invoke(args=["doctor"]).output
    assert "INVALID" in out and "1 with a map position (2 announced in total)" in out


# ------------------------------------------------------------------ the hint for admins
def test_an_empty_search_tells_only_admins_why(tmp_path):
    app = make_app(tmp_path)
    fan, boss = register(app, "rita"), register(app, "boss")
    make_admin(app, "boss")
    assert nearby(fan, lat=51.5, lon=-0.12).get_json()["hint"] is None
    hint = nearby(boss, lat=51.5, lon=-0.12).get_json()["hint"]
    assert "TICKETMASTER_API_KEY" in hint and ".env" in hint and "no event provider" in hint


def test_hint_changes_when_configured_but_not_yet_run_and_goes_away_with_data(app, monkeypatch):
    boss = register(app, "boss")
    make_admin(app, "boss")
    assert "IMPORT_ON_DEMAND=0" in nearby(boss, lat=51.5072, lon=-0.1276).get_json()["hint"]
    monkeypatch.setattr(importer, "_http_get_json", FakeTicketmaster(page([tm_event(1)])))
    with app.app_context():
        importer.run()
    found = nearby(boss, lat=51.5072, lon=-0.1276, radius_km=60).get_json()
    assert found["total"] == 1 and found["hint"] is None
    elsewhere = nearby(boss, lat=0, lon=0, radius_km=5).get_json()["hint"]       # data exists, just not there:
    assert "listed nothing here" in elsewhere                                    # so the admin hears about coverage


def test_the_panel_shows_the_hint_and_an_honest_empty_message():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    script = open(os.path.join(root, "rockconnect", "static", "js", "gigs.js"), encoding="utf-8").read()
    assert "data.hint" in script and "No gigs found within" in script
