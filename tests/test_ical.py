# File: test_ical.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Add to calendar: the .ics file of one gig and of all my plans."""
from datetime import datetime, timedelta

import pytest

from helpers import make_app, register, scalar, sql, token_of
from rockconnect import ical


def when(days, hour=20):
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)


def add_external(app, ext_id, title, at, source="ticketmaster", venue="Camden Underworld", city="London"):
    sql(app, "INSERT INTO external_events (source, external_id, title, venue, city, event_at, time_known, latitude,"
             " longitude, ticket_url, genre, area, imported_at, seen_at) VALUES (:s, :e, :t, :v, :c, :at, 1, 51.539,"
             " -0.143, 'https://example.com/t?a=1&b=2', 'Rock', 'London', '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
        s=source, e=ext_id, t=title, v=venue, c=city, at=at.strftime("%Y-%m-%d %H:%M"))


def go(client, source, ref, status="going"):
    client.post("/gigs/attendance", data={"_csrf": token_of(client, "/feed"), "source": source, "ref": ref, "status": status},
                headers={"X-Requested-With": "fetch"})


def unfold(text):
    return text.replace("\r\n ", "")


@pytest.fixture
def app_and_rita(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "tm1", "Kings of Leon, Live; Tour", when(5))
    return app, register(app, "rita")


# ------------------------------------------------------------------ the pieces
def test_escape_covers_the_special_characters():
    assert ical.escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    assert ical.escape("line\r\nbreak") == "line\\nbreak"
    assert ical.escape("bell\x07gone") == "bellgone"
    assert ical.escape(None) == ""


def test_long_lines_are_folded_at_75_bytes_without_splitting_a_character():
    line = "SUMMARY:" + "é" * 80
    folded = ical.fold(line)
    parts = folded.split("\r\n ")
    assert len(parts) > 1
    assert all(len(p.encode("utf-8")) <= 75 for p in parts)
    assert "".join(parts) == line
    assert ical.fold("SHORT:line") == "SHORT:line"


# ------------------------------------------------------------------ one gig
def test_a_gig_file_has_the_right_fields(app_and_rita):
    app, rita = app_and_rita
    go(rita, "ticketmaster", "tm1")
    r = rita.get("/gigs/ticketmaster/tm1.ics")
    assert r.status_code == 200 and r.mimetype == "text/calendar"
    assert 'attachment; filename="kings-of-leon-live-tour.ics"' in r.headers["Content-Disposition"]
    assert "no-store" in r.headers["Cache-Control"]
    body = r.get_data(as_text=True)
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert body.count("BEGIN:VEVENT") == body.count("END:VEVENT") == 1
    text = unfold(body)
    start = when(5)
    assert "DTSTART:" + start.strftime("%Y%m%dT%H%M%S") in text
    assert "DTEND:" + (start + timedelta(hours=3)).strftime("%Y%m%dT%H%M%S") in text
    assert "SUMMARY:Kings of Leon\\, Live\\; Tour" in text
    assert "LOCATION:Camden Underworld\\, London" in text
    assert "\r\nGEO:51.53900;-0.14300\r\n" in body
    assert "STATUS:CONFIRMED" in text
    assert "UID:gig-ticketmaster-tm1@localhost" in text
    assert "Tickets: https://example.com/t?a=1&b=2" in text.replace("\\,", ",")
    event_id = scalar(app, "SELECT id FROM external_events")
    assert "URL:http://localhost/events/%d" % event_id in text
    assert all(len(line.encode("utf-8")) <= 75 for line in body.split("\r\n"))


def test_interested_is_tentative_and_not_signed_up_is_confirmed(app_and_rita):
    app, rita = app_and_rita
    go(rita, "ticketmaster", "tm1", "interested")
    assert "STATUS:TENTATIVE" in rita.get("/gigs/ticketmaster/tm1.ics").get_data(as_text=True)
    go(rita, "ticketmaster", "tm1", "going")
    assert "STATUS:CONFIRMED" in rita.get("/gigs/ticketmaster/tm1.ics").get_data(as_text=True)
    go(rita, "ticketmaster", "tm1", "none")
    assert "STATUS:CONFIRMED" in rita.get("/gigs/ticketmaster/tm1.ics").get_data(as_text=True)


def test_a_gig_with_no_known_time_is_an_all_day_event(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "d1", "Festival", when(6, hour=0))
    rita = register(app, "rita")
    text = unfold(rita.get("/gigs/ticketmaster/d1.ics").get_data(as_text=True))
    day = when(6, hour=0)
    assert "DTSTART;VALUE=DATE:" + day.strftime("%Y%m%d") in text
    assert "DTEND;VALUE=DATE:" + (day + timedelta(days=1)).strftime("%Y%m%d") in text


def test_a_members_own_gig_at_midnight_keeps_its_time(tmp_path):
    app = make_app(tmp_path)
    kings = register(app, "kings", kind="band")
    kings.post("/posts", data={"_csrf": token_of(kings, "/feed"), "body": "late one",
                               "event_at": when(4, hour=0).strftime("%Y-%m-%dT%H:%M"), "event_place": "Cellar"})
    ref = str(scalar(app, "SELECT id FROM posts"))
    text = unfold(kings.get("/gigs/community/%s.ics" % ref).get_data(as_text=True))
    assert "DTSTART:" + when(4, hour=0).strftime("%Y%m%dT%H%M%S") in text and "VALUE=DATE" not in text


def test_unknown_gig_needs_login_and_the_calendar_is_never_public(app_and_rita):
    app, rita = app_and_rita
    assert rita.get("/gigs/ticketmaster/nope.ics").status_code == 404
    assert rita.get("/gigs/community/99999.ics").status_code == 404
    assert rita.get("/gigs/bogus/1.ics").status_code == 404
    anon = app.test_client()
    for url in ("/gigs/ticketmaster/tm1.ics", "/gigs/mine.ics"):
        r = anon.get(url)
        assert r.status_code == 302 and "/users/signin" in r.headers["Location"], url


def test_a_hidden_gig_of_a_blocked_band_has_no_file(tmp_path):
    app = make_app(tmp_path)
    kings = register(app, "kings", kind="band")
    kings.post("/posts", data={"_csrf": token_of(kings, "/feed"), "body": "gig", "event_at": when(4).strftime("%Y-%m-%dT%H:%M"),
                               "event_place": "Hall"})
    ref = str(scalar(app, "SELECT id FROM posts"))
    rita = register(app, "rita")
    assert rita.get("/gigs/community/%s.ics" % ref).status_code == 200
    kings.post("/block/%d" % scalar(app, "SELECT id FROM users WHERE username = 'rita'"), data={"_csrf": token_of(kings, "/feed")})
    assert rita.get("/gigs/community/%s.ics" % ref).status_code == 404


# ------------------------------------------------------------------ all my plans
def test_my_gigs_file_lists_only_my_upcoming_plans(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "a", "Alpha", when(3))
    add_external(app, "b", "Bravo", when(8))
    add_external(app, "c", "Charlie", when(9))
    rita, sam = register(app, "rita"), register(app, "sam")
    go(rita, "ticketmaster", "a", "going")
    go(rita, "ticketmaster", "b", "interested")
    go(sam, "ticketmaster", "c", "going")
    body = unfold(rita.get("/gigs/mine.ics").get_data(as_text=True))
    assert body.count("BEGIN:VEVENT") == 2
    assert "SUMMARY:Alpha" in body and "SUMMARY:Bravo" in body and "Charlie" not in body
    assert body.index("SUMMARY:Alpha") < body.index("SUMMARY:Bravo")
    assert "STATUS:CONFIRMED" in body and "STATUS:TENTATIVE" in body
    assert 'filename="my-gigs.ics"' in rita.get("/gigs/mine.ics").headers["Content-Disposition"]


def test_my_gigs_file_is_valid_when_empty(tmp_path):
    app = make_app(tmp_path)
    rita = register(app, "rita")
    body = rita.get("/gigs/mine.ics").get_data(as_text=True)
    assert "BEGIN:VCALENDAR" in body and "VEVENT" not in body and body.endswith("END:VCALENDAR\r\n")


# ------------------------------------------------------------------ the buttons
def test_buttons_are_on_the_gig_page_and_my_gigs(app_and_rita):
    app, rita = app_and_rita
    event_id = scalar(app, "SELECT id FROM external_events")
    assert "/gigs/ticketmaster/tm1.ics" in rita.get("/events/%d" % event_id).get_data(as_text=True)
    assert "/gigs/mine.ics" not in rita.get("/gigs/mine").get_data(as_text=True)       # nothing planned: no button
    go(rita, "ticketmaster", "tm1")
    assert "/gigs/mine.ics" in rita.get("/gigs/mine").get_data(as_text=True)


def test_no_calendar_button_once_the_gig_is_over(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "old", "Old show", when(-2))
    rita = register(app, "rita")
    event_id = scalar(app, "SELECT id FROM external_events")
    assert "/gigs/ticketmaster/old.ics" not in rita.get("/events/%d" % event_id).get_data(as_text=True)
