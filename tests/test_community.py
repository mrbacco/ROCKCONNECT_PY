# File: test_community.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""The band / venue / fan niche: member types, gigs board, profile details, directory filters."""
from datetime import datetime, timedelta

import pytest

from helpers import make_app, post_form, register, scalar, signup, sql, token_of, user_id


def when(days, hour=20):
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=30).strftime("%Y-%m-%dT%H:%M")


def announce(client, event_at, place="The Basement Bar, Galway", body="Doors at 8"):
    return client.post("/posts", data={"_csrf": token_of(client, "/feed"), "body": body, "event_at": event_at,
                                       "event_place": place}, follow_redirects=True)


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


@pytest.fixture
def scene(app):
    band = register(app, "kings", kind="band", name="The Kings")
    venue = register(app, "basement", kind="venue", name="The Basement")
    fan = register(app, "rita", kind="fan", name="Rita")
    return app, band, venue, fan


# ------------------------------------------------------------------ member types
def test_member_type_is_chosen_at_signup_and_shown(scene):
    app, band, venue, fan = scene
    assert scalar(app, "SELECT kind FROM users WHERE username = 'kings'") == "band"
    page = fan.get("/users_list/%d" % user_id(app, "kings")).get_data(as_text=True)
    assert "kind-badge kind-band" in page
    assert "kind-badge" not in fan.get("/users_list/%d" % user_id(app, "rita")).get_data(as_text=True)   # fans: no label
    assert 'class="kind-badge kind-venue"' in fan.get("/people").get_data(as_text=True)


def test_unknown_member_type_is_refused(app):
    r = signup(app.test_client(), kind="admin")
    assert b"Choose fan, band or venue" in r.data and scalar(app, "SELECT count(*) FROM users") == 0


def test_the_directory_filters_by_type_and_searches_locations(scene):
    app, band, venue, fan = scene
    sql(app, "UPDATE users SET location = 'Galway, Ireland' WHERE username = 'basement'")
    only_bands = fan.get("/people?kind=band").get_data(as_text=True)
    assert "kings" in only_bands and "basement" not in only_bands and "rita" not in only_bands.split("<ul class=\"list-group")[1]
    assert "basement" in fan.get("/people?kind=venue").get_data(as_text=True)
    assert "basement" in fan.get("/people?q=galway").get_data(as_text=True)             # location is searchable
    assert "kings" not in fan.get("/people?q=galway").get_data(as_text=True).split("<ul class=\"list-group")[1]
    assert "No users found" in fan.get("/people?kind=venue&q=zzz").get_data(as_text=True)


def test_profile_details_location_and_website(scene):
    app, band, venue, fan = scene

    def save(**fields):
        data = {"name": "The Kings", "email": "kings@example.com", "about": "loud", "kind": "band",
                "location": "Dublin", "website": "", **fields, "_csrf": token_of(band, "/edit")}
        return band.post("/edit", data=data, follow_redirects=True)

    assert b"Profile updated" in save(website="kings.example.com/music").data
    page = fan.get("/users_list/%d" % user_id(app, "kings")).get_data(as_text=True)
    assert 'href="https://kings.example.com/music"' in page and 'rel="nofollow noopener ugc"' in page
    assert "Dublin" in page
    # anything that is not a plain web link is refused, never turned into a clickable link
    for bad in ("javascript:alert(1)", "data:text/html,<b>x</b>", 'https://x.com/"onmouseover="alert(1)', "ftp://x.com"):
        assert b"normal http(s) link" in save(website=bad).data, bad
    assert scalar(app, "SELECT website FROM users WHERE username = 'kings'") == "https://kings.example.com/music"
    assert b"Profile updated" in save(website="").data                                      # can be cleared
    assert scalar(app, "SELECT website FROM users WHERE username = 'kings'") is None
    assert b"Choose fan, band or venue" in save(kind="wizard").data


# ------------------------------------------------------------------ gigs
def test_bands_and_venues_can_announce_gigs_fans_cannot(scene):
    app, band, venue, fan = scene
    assert b"Gig announced" in announce(band, when(5)).data
    assert b"Gig announced" in announce(venue, when(9), place="Basement").data
    assert b"Only bands and venues" in announce(fan, when(3)).data
    assert scalar(app, "SELECT count(*) FROM posts WHERE event_at IS NOT NULL") == 2
    # the post box offers the gig fields only to those who may use them
    assert b"Announce a gig" in band.get("/feed").data and b"Announce a gig" not in fan.get("/feed").data


def test_gig_board_lists_upcoming_gigs_soonest_first_and_hides_old_ones(scene):
    app, band, venue, fan = scene
    announce(band, when(10), place="Cork Harbour", body="late summer")
    announce(venue, when(2), place="Galway Docks", body="very soon")
    announce(band, when(-3), place="Old Place", body="already happened")
    announce(band, when(5), place="Mid Place", body="in between")
    page = fan.get("/gigs").get_data(as_text=True)
    assert page.index("very soon") < page.index("in between") < page.index("late summer")
    assert "already happened" not in page
    assert "Galway Docks" in page and "gig-when" in page
    # the past gig is still a normal post in the feed
    assert "already happened" in fan.get("/feed").get_data(as_text=True)


def test_tonights_gig_stays_on_the_board(scene):
    app, band, venue, fan = scene
    earlier_today = (datetime.now() - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M")
    announce(band, earlier_today, body="tonight show")
    assert "tonight show" in fan.get("/gigs").get_data(as_text=True)


def test_gig_validation(scene):
    app, band, venue, fan = scene
    assert b"gig date is not valid" in announce(band, "next friday").data
    assert b"gig date is not valid" in announce(band, "2026-13-45T25:61").data
    assert scalar(app, "SELECT count(*) FROM posts") == 0
    # a gig needs no text
    assert b"Gig announced" in announce(band, when(4), body="").data
    # a date typed into a long place name is cut to fit
    announce(band, when(4), place="x" * 400)
    assert max(len(p or "") for p in [scalar(app, "SELECT max(event_place) FROM posts")]) <= 120


def test_gig_board_hides_blocked_and_suspended_members(scene):
    app, band, venue, fan = scene
    announce(band, when(3), body="kings gig")
    announce(venue, when(4), body="basement gig")
    post_form(fan, "/block/%d" % user_id(app, "kings"))
    page = fan.get("/gigs").get_data(as_text=True)
    assert "basement gig" in page and "kings gig" not in page
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'basement'")
    assert "basement gig" not in fan.get("/gigs").get_data(as_text=True)


def test_gig_board_needs_sign_in(app):
    assert app.test_client().get("/gigs").status_code == 302


# ------------------------------------------------------------------ branding on the landing page
def test_landing_page_pitches_all_three_audiences(app):
    html = app.test_client().get("/").get_data(as_text=True)
    for needle in ("For bands", "For venues", "For fans", "Join free", "Sign in"):
        assert needle in html
    assert "kind=" not in html and "/users/add" in html


def test_signed_in_members_skip_the_landing_page(scene):
    app, band, venue, fan = scene
    assert band.get("/").status_code == 302
