# File: test_geocoder_trouble.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""When the place-name service refuses or fails, say so (not 'no such place'): placeholder contact, HTTP 403, down."""
import urllib.error

import pytest

from helpers import make_app, register, scalar
from rockconnect import geo
from test_nearby import announce, nearby


@pytest.mark.parametrize("email,placeholder", [
    ("hello@example.com", True), ("a@EXAMPLE.org", True), ("x@example.net", True), ("me@localhost", True),
    ("nonsense", True), ("", True), (None, True),
    ("mrbacco04@gmail.com", False), ("hi@gighub.example", False), ("ops@mycompany.ie", False)])
def test_which_contact_addresses_count_as_placeholders(email, placeholder):
    assert geo.contact_is_placeholder(email) is placeholder


def calls_to_urlopen(monkeypatch):
    calls = []
    monkeypatch.setattr("rockconnect.geo.urllib.request.urlopen", lambda r, timeout: calls.append(r))
    return calls


def test_a_placeholder_contact_is_refused_before_any_request_is_made(tmp_path, monkeypatch):
    calls = calls_to_urlopen(monkeypatch)
    app = make_app(tmp_path, GEOCODER="nominatim", CONTACT_EMAIL="hello@example.com")
    with app.test_request_context(), pytest.raises(geo.GeocoderUnavailable, match="CONTACT_EMAIL"):
        geo._fetch("london")
    assert calls == []                                              # OpenStreetMap would have answered 403


def test_a_placeholder_is_fine_for_your_own_geocoder(tmp_path, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'[{"lat": "1.5", "lon": "2.5"}]'

    monkeypatch.setattr("rockconnect.geo.urllib.request.urlopen", lambda r, timeout: Response())
    app = make_app(tmp_path, GEOCODER="nominatim", CONTACT_EMAIL="hello@example.com",
                   GEOCODER_URL="https://geo.mycompany.ie/search")
    with app.test_request_context():
        assert geo._fetch("anywhere") == (1.5, 2.5)


@pytest.mark.parametrize("raised,words", [
    (urllib.error.HTTPError("u", 403, "x", {}, None), "HTTP 403"),
    (urllib.error.HTTPError("u", 500, "x", {}, None), "HTTP 500"),
    (urllib.error.URLError("no route"), "could not be reached"),
    (TimeoutError(), "could not be reached"),
])
def test_service_failures_become_geocoder_unavailable(tmp_path, monkeypatch, raised, words):
    def failing(request, timeout):
        raise raised
    monkeypatch.setattr("rockconnect.geo.urllib.request.urlopen", failing)
    app = make_app(tmp_path, GEOCODER="nominatim", CONTACT_EMAIL="real@mycompany.ie")
    with app.test_request_context(), pytest.raises(geo.GeocoderUnavailable, match=words):
        geo._fetch("london")


def test_geocode_never_caches_a_failure(tmp_path, monkeypatch):
    def failing(request, timeout):
        raise urllib.error.HTTPError("u", 403, "x", {}, None)
    monkeypatch.setattr("rockconnect.geo.urllib.request.urlopen", failing)
    app = make_app(tmp_path, GEOCODER="nominatim", CONTACT_EMAIL="real@mycompany.ie")
    with app.app_context():
        with pytest.raises(geo.GeocoderUnavailable):
            geo.geocode("london")
    assert scalar(app, "SELECT count(*) FROM geocache") == 0


def test_the_api_says_the_service_is_unavailable_with_a_placeholder_contact(tmp_path):
    app = make_app(tmp_path, GEOCODER="nominatim", CONTACT_EMAIL="hello@example.com")
    fan = register(app)
    r = nearby(fan, q="London")
    assert r.status_code == 503 and r.get_json()["code"] == "place_lookup_unavailable"
    assert "current location" in r.get_json()["error"]
    assert nearby(fan, lat=51.5, lon=-0.12).status_code == 200                      # searching by position still works


def test_a_gig_is_still_posted_when_the_lookup_service_fails(tmp_path):
    app = make_app(tmp_path, GEOCODER="nominatim", CONTACT_EMAIL="hello@example.com")
    band = register(app, "kings", kind="band")
    page = announce(band, "Camden Underworld, London")
    assert b"could not place it on the map" in page.data
    assert scalar(app, "SELECT count(*) FROM posts") == 1 and scalar(app, "SELECT latitude FROM posts") is None


def test_doctor_flags_a_placeholder_contact(tmp_path):
    app = make_app(tmp_path, GEOCODER="nominatim", CONTACT_EMAIL="hello@example.com")
    out = app.test_cli_runner().invoke(args=["doctor"]).output
    assert "[!] on, but CONTACT_EMAIL (hello@example.com) is a placeholder" in out


def test_doctor_online_checks_the_place_service_too(tmp_path, monkeypatch):
    monkeypatch.setattr(geo, "_fetch", lambda place: (51.5, -0.12))
    from rockconnect import importer
    from test_import import FakeTicketmaster
    monkeypatch.setattr(importer, "_http_get_json", FakeTicketmaster({"page": {"totalElements": 5}}))
    app = make_app(tmp_path, GEOCODER="nominatim", TICKETMASTER_API_KEY="k", IMPORT_AREAS="London=51.5,-0.12,40")
    out = app.test_cli_runner().invoke(args=["doctor", "--online"]).output
    assert "Place check" in out and "London -> found" in out


def test_doctor_online_reports_a_refusing_place_service(tmp_path, monkeypatch):
    def refuse(place):
        raise geo.GeocoderUnavailable("The place service refused us (HTTP 403).")
    monkeypatch.setattr(geo, "_fetch", refuse)
    from rockconnect import importer
    from test_import import FakeTicketmaster
    monkeypatch.setattr(importer, "_http_get_json", FakeTicketmaster({"page": {"totalElements": 5}}))
    app = make_app(tmp_path, GEOCODER="nominatim", TICKETMASTER_API_KEY="k", IMPORT_AREAS="London=51.5,-0.12,40")
    out = app.test_cli_runner().invoke(args=["doctor", "--online"]).output
    assert "[!] The place service refused us (HTTP 403)." in out
