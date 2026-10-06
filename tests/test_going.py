# File: test_going.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Going to gigs: the buttons, who else is going, privacy, the same concert on two listings, profiles and people search."""
import io
import json
import zipfile
from datetime import datetime, timedelta

import pytest

from helpers import FETCH, make_admin, make_app, post_form, register, scalar, sql, token_of, user_id
from rockconnect import social, taxonomy


def when(days, hour=20):
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0)


def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def announce(client, place="Camden Underworld", lat=51.539, lon=-0.143, days=4, genre="", body="gig"):
    data = {"_csrf": token_of(client, "/feed"), "body": body, "event_at": when(days).strftime("%Y-%m-%dT%H:%M"),
            "event_place": place, "event_lat": str(lat), "event_lon": str(lon), "genre": genre}
    client.post("/posts", data=data, follow_redirects=True)


def add_external(app, ext_id, title, source="ticketmaster", lat=51.539, lon=-0.143, days=4, genre="Rock"):
    sql(app, "INSERT INTO external_events (source, external_id, title, venue, city, event_at, time_known, latitude,"
             " longitude, ticket_url, genre, area, imported_at, seen_at) VALUES (:s, :e, :t, 'Camden Underworld',"
             " 'London', :at, 1, :la, :lo, 'https://example.com/t', :g, 'London', '2026-01-01 00:00:00',"
             " '2026-01-01 00:00:00')", s=source, e=ext_id, t=title, at=stamp(when(days)), la=lat, lo=lon, g=genre)


def go(client, source, ref, status="going", **extra):
    return client.post("/gigs/attendance", data={"_csrf": token_of(client, "/feed"), "source": source, "ref": ref,
                                                  "status": status, **extra}, headers=FETCH)


def api_go(client, source, ref, **data):
    return client.post("/api/v1/gigs/%s/%s/attendance" % (source, ref),
                       data={"_csrf": token_of(client, "/feed"), **data})


@pytest.fixture
def scene(tmp_path):
    """A band announces a gig; rita, sam and dee are fans; boss is an admin."""
    app = make_app(tmp_path)
    kings = register(app, "kings", kind="band")
    announce(kings, genre="rock")
    rita, sam, dee = register(app, "rita"), register(app, "sam"), register(app, "dee")
    boss = register(app, "boss")
    make_admin(app, "boss")
    post_id = str(scalar(app, "SELECT id FROM posts"))
    return app, kings, rita, sam, dee, boss, post_id


# ------------------------------------------------------------------ the basics
def test_going_and_interested_are_counted_and_can_be_changed_or_cancelled(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    r = go(rita, "community", ref)
    assert r.status_code == 200 and r.get_json() == {"source": "community", "ref": ref, "status": "going",
                                                      "visible": True, "going_count": 1, "interested_count": 0}
    assert go(sam, "community", ref, "interested").get_json()["interested_count"] == 1
    assert go(rita, "community", ref, "interested").get_json()["going_count"] == 0       # changed her mind: one row only
    assert scalar(app, "SELECT count(*) FROM attendances WHERE user_id = :u", u=user_id(app, "rita")) == 1
    gone = go(rita, "community", ref, "none").get_json()
    assert gone["status"] is None and gone["interested_count"] == 1


def test_the_buttons_are_on_the_gig_card_and_show_the_counts(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    go(sam, "community", ref)
    html = rita.get("/feed").get_data(as_text=True)
    assert "Going</button>" in html and "Interested</button>" in html and "1 going" in html
    page = rita.post("/gigs/attendance", data={"_csrf": token_of(rita, "/feed"), "source": "community", "ref": ref,
                                              "status": "going", "next": "/feed"}, follow_redirects=True)
    assert b"You are going. Other members can see it." in page.data and b"2 going" in page.data


def test_validation_and_permissions(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    assert app.test_client().post("/gigs/attendance", data={"source": "community", "ref": ref}).status_code == 400
    assert rita.post("/gigs/attendance", data={"source": "community", "ref": ref, "status": "going"}).status_code == 400
    assert go(rita, "community", ref, "maybe").get_json()["code"] == "bad_status"
    assert go(rita, "community", "99999").status_code == 404
    assert go(rita, "ticketmaster", "nope").get_json()["code"] == "gig_not_found"
    assert rita.post("/gigs/attendance", data={"_csrf": token_of(rita, "/feed"), "source": "community", "ref": ref},
                     headers=FETCH).get_json()["code"] == "missing_status"
    visitor = app.test_client()
    anon = visitor.post("/api/v1/gigs/community/%s/attendance" % ref,
                        data={"status": "going", "_csrf": token_of(visitor, "/users/signin")})
    assert anon.status_code == 401 and anon.get_json()["code"] == "session_expired"


def test_a_gig_that_is_over_cannot_be_joined_but_can_be_left(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    go(rita, "community", ref)
    sql(app, "UPDATE posts SET event_at = :d", d=stamp(datetime.now() - timedelta(days=2)))
    assert go(sam, "community", ref).get_json()["code"] == "gig_over"
    assert go(rita, "community", ref, "none").status_code == 200
    assert "This gig is over." in rita.get("/posts/%s" % ref).get_data(as_text=True)


def test_going_is_rate_limited(tmp_path):
    from helpers import limits
    app = make_app(tmp_path, RATE_LIMITS=limits(attend_user=(2, 3600)))
    band, fan = register(app, "kings", kind="band"), register(app, "rita")
    announce(band)
    ref = str(scalar(app, "SELECT id FROM posts"))
    assert [go(fan, "community", ref, s).status_code for s in ("going", "interested")] == [200, 200]
    assert go(fan, "community", ref, "going").status_code == 429


# ------------------------------------------------------------------ who is going
def test_who_is_going_lists_other_members_but_not_me(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    for fan in (rita, sam):
        go(fan, "community", ref)
    page = rita.get("/posts/%s" % ref).get_data(as_text=True)
    assert "Who is going" in page and "Sam Test" in page and "@sam" in page and "Rita Test" not in page.split("Who is going")[1]
    assert "Ask to chat" in page and "Say hi" not in page
    assert dee.get("/posts/%s" % ref).get_data(as_text=True).count("Ask to chat") == 2


def test_private_rsvps_are_counted_but_not_named(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    go(sam, "community", ref)
    assert api_go(sam, "community", ref, visible="0").get_json()["visible"] is False
    assert api_go(sam, "community", ref, visible="0").get_json()["going_count"] == 1
    page = rita.get("/posts/%s" % ref).get_data(as_text=True)
    assert "1 going" in page and "Sam Test" not in page
    names = rita.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()
    assert names["going_count"] == 1 and names["people"] == []
    api_go(sam, "community", ref, visible="1")
    assert [p["username"] for p in rita.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()["people"]] == ["sam"]


def test_hiding_all_plans_removes_you_from_lists_counts_and_your_profile(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    go(sam, "community", ref)
    assert "going to" in rita.get("/users_list/%d" % user_id(app, "sam")).get_data(as_text=True).lower()
    assert [p["username"] for p in rita.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()["people"]] == ["sam"]
    sql(app, "UPDATE users SET hide_plans = 1 WHERE username = 'sam'")
    listed = rita.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()
    assert listed["people"] == [] and listed["going_count"] == 0               # not named, and not even counted
    assert "Sam Test" not in rita.get("/posts/%s" % ref).get_data(as_text=True)
    assert "Going to" not in rita.get("/users_list/%d" % user_id(app, "sam")).get_data(as_text=True)
    assert "1 going" not in rita.get("/feed").get_data(as_text=True)          # others no longer count him
    assert "1 going" in sam.get("/feed").get_data(as_text=True)               # he still sees his own plan


def test_blocked_and_suspended_members_never_appear(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    for fan in (sam, dee):
        go(fan, "community", ref)
    attendees = lambda who: sorted(p["username"] for p in who.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()["people"])  # noqa: E731
    assert attendees(rita) == ["dee", "sam"]
    post_form(rita, "/block/%d" % user_id(app, "sam"))
    assert attendees(rita) == ["dee"] and attendees(sam) == ["dee"]                    # hidden both ways
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'dee'")
    assert attendees(rita) == []
    assert rita.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()["going_count"] == 0


def test_filters_by_instrument_genre_and_goal(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    social_tags = {"rita": (["bass"], ["rock", "punk"], ["jam"]), "sam": (["drums", "bass"], ["jazz"], ["bandmates"]),
                   "dee": ([], ["rock"], ["gig_buddies"])}
    with app.app_context():
        for name, (ins, gen, goal) in social_tags.items():
            social.save_tags(user_id(app, name), ins, gen, goal)
    for fan in (rita, sam, dee):
        go(fan, "community", ref)
    ask = lambda **q: sorted(p["username"] for p in boss.get("/api/v1/gigs/community/%s/attendees" % ref, query_string=q).get_json()["people"])  # noqa: E731
    assert ask() == ["dee", "rita", "sam"]
    assert ask(instrument="bass") == ["rita", "sam"] and ask(instrument="drums") == ["sam"]
    assert ask(genre="rock") == ["dee", "rita"] and ask(genre="jazz") == ["sam"]
    assert ask(goal="gig_buddies") == ["dee"] and ask(instrument="bass", genre="rock") == ["rita"]
    assert ask(instrument="bass", genre="jazz", goal="jam") == []
    one = boss.get("/api/v1/gigs/community/%s/attendees" % ref, query_string={"instrument": "drums"}).get_json()["people"][0]
    assert one["instruments"] == ["bass", "drums"] and one["genres"] == ["jazz"] and one["goals"] == ["bandmates"]
    assert "email" not in one and "password" not in json.dumps(one)
    bad = boss.get("/api/v1/gigs/community/%s/attendees" % ref, query_string={"instrument": "kazoo-orchestra"})
    assert bad.status_code == 400 and bad.get_json()["code"] == "bad_instrument"
    html = boss.get("/posts/%s?instrument=drums" % ref).get_data(as_text=True)
    assert "Sam Test" in html and "Dee Test" not in html and "Clear" in html
    assert "Nobody going matches" in boss.get("/posts/%s?instrument=banjo" % ref).get_data(as_text=True)


def test_status_filter(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    go(rita, "community", ref, "going"), go(sam, "community", ref, "interested")
    ask = lambda s: [p["username"] for p in boss.get("/api/v1/gigs/community/%s/attendees" % ref, query_string={"rsvp": s}).get_json()["people"]]  # noqa: E731
    assert ask("going") == ["rita"] and ask("interested") == ["sam"]


# ------------------------------------------------------------------ the same concert on two listings
def test_people_on_different_listings_of_one_concert_find_each_other(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "TM1", "Gilla Band")
    add_external(app, "SK9", "Gilla Band + support", source="skiddle")
    add_external(app, "TM2", "Gilla Band", days=5)                              # another night: a different gig
    rita, sam, dee = register(app, "rita"), register(app, "sam"), register(app, "dee")
    go(rita, "ticketmaster", "TM1"), go(sam, "skiddle", "SK9"), go(dee, "ticketmaster", "TM2")
    who = lambda c, s, r: sorted(p["username"] for p in c.get("/api/v1/gigs/%s/%s/attendees" % (s, r)).get_json()["people"])  # noqa: E731
    assert who(rita, "ticketmaster", "TM1") == ["sam"] and who(sam, "skiddle", "SK9") == ["rita"]
    assert who(rita, "ticketmaster", "TM2") == ["dee"]                          # not mixed with the other night
    both = rita.get("/api/v1/gigs/skiddle/SK9/attendees").get_json()
    assert both["going_count"] == 2 and both["my_status"] == "going"            # she is going, seen from the other listing


def test_changing_plans_from_the_other_listing_updates_the_same_rsvp(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "TM1", "Gilla Band"), add_external(app, "SK9", "Gilla Band", source="skiddle")
    rita = register(app, "rita")
    go(rita, "ticketmaster", "TM1")
    assert go(rita, "skiddle", "SK9", "interested").get_json()["interested_count"] == 1
    assert scalar(app, "SELECT count(*) FROM attendances") == 1                 # one plan, not two
    assert go(rita, "skiddle", "SK9", "none").get_json()["status"] is None and scalar(app, "SELECT count(*) FROM attendances") == 0


def test_a_members_gig_and_a_provider_listing_of_it_are_one_concert(tmp_path):
    app = make_app(tmp_path)
    band = register(app, "kings", kind="band")
    announce(band, body="our night")
    app.config["TESTING"] = True
    sql(app, "UPDATE users SET name = 'Gilla Band' WHERE username = 'kings'")
    add_external(app, "TM1", "Gilla Band", days=4)
    post = str(scalar(app, "SELECT id FROM posts"))
    rita, sam = register(app, "rita"), register(app, "sam")
    go(rita, "community", post), go(sam, "ticketmaster", "TM1")
    assert sorted(p["username"] for p in rita.get("/api/v1/gigs/ticketmaster/TM1/attendees").get_json()["people"]) == ["sam"]


def test_gigs_without_a_map_position_match_only_themselves(tmp_path):
    app = make_app(tmp_path)
    band = register(app, "kings", kind="band")
    for place in ("Hall A", "Hall B"):          # no coordinates and no place lookup: two gigs, same day, same band
        band.post("/posts", data={"_csrf": token_of(band, "/feed"), "body": "x", "event_at": when(4).strftime("%Y-%m-%dT%H:%M"),
                                  "event_place": place})
    first, second = [str(r[0]) for r in sql_rows(app, "SELECT id FROM posts ORDER BY id")]
    rita, sam = register(app, "rita"), register(app, "sam")
    go(rita, "community", first), go(sam, "community", second)
    assert rita.get("/api/v1/gigs/community/%s/attendees" % first).get_json()["people"] == []
    assert rita.get("/api/v1/gigs/community/%s/attendees" % second).get_json()["going_count"] == 1


def sql_rows(app, statement):
    from rockconnect.db import execute
    with app.app_context():
        return execute(statement).fetchall()


# ------------------------------------------------------------------ snapshots and clean-up
def test_the_snapshot_outlives_a_deleted_listing_and_is_removed_after_the_gig(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "TM1", "Gilla Band")
    rita = register(app, "rita")
    go(rita, "ticketmaster", "TM1")
    sql(app, "DELETE FROM external_events")                                       # stale listing pruned by the importer
    assert scalar(app, "SELECT title FROM attendances") == "Gilla Band"
    assert "Gilla Band" in rita.get("/gigs/mine").get_data(as_text=True)
    sql(app, "UPDATE attendances SET event_at = :d", d=stamp(datetime.now() - timedelta(days=3)))
    with app.app_context():
        assert social.prune_attendances() == 1
    assert scalar(app, "SELECT count(*) FROM attendances") == 0


def test_member_gigs_are_kept_longer_than_imported_ones(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    go(rita, "community", ref)
    sql(app, "UPDATE attendances SET event_at = :d", d=stamp(datetime.now() - timedelta(days=10)))
    with app.app_context():
        assert social.prune_attendances() == 0
    sql(app, "UPDATE attendances SET event_at = :d", d=stamp(datetime.now() - timedelta(days=40)))
    with app.app_context():
        assert social.prune_attendances() == 1


def test_the_importer_cleanup_also_clears_old_snapshots(tmp_path):
    from rockconnect import importer
    app = make_app(tmp_path)
    add_external(app, "TM1", "Old Gig")
    rita = register(app, "rita")
    go(rita, "ticketmaster", "TM1")
    sql(app, "UPDATE attendances SET event_at = '2020-01-01 20:00'")
    with app.app_context():
        importer.prune()
    assert scalar(app, "SELECT count(*) FROM attendances") == 0


# ------------------------------------------------------------------ my gigs, profiles, the API
def test_my_gigs_page_and_api(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    go(rita, "community", ref)
    page = rita.get("/gigs/mine").get_data(as_text=True)
    assert "Kings Test" in page and "going" in page
    body = rita.get("/api/v1/me/gigs").get_json()
    assert body["total"] == 1 and body["gigs"][0]["status"] == "going" and body["gigs"][0]["ref"] == ref
    assert sam.get("/api/v1/me/gigs").get_json()["total"] == 0
    assert app.test_client().get("/gigs/mine").status_code == 302


def test_profile_shows_public_plans_only_and_nothing_between_blocked_members(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    go(sam, "community", ref)
    url = "/users_list/%d" % user_id(app, "sam")
    assert "Going to" in rita.get(url).get_data(as_text=True)
    api_go(sam, "community", ref, visible="0")
    assert "Going to" not in rita.get(url).get_data(as_text=True)
    assert "Going to" in sam.get(url).get_data(as_text=True)                  # he always sees his own, private or not
    api_go(sam, "community", ref, visible="1")
    post_form(rita, "/block/%d" % user_id(app, "sam"))
    assert "Going to" not in rita.get(url).get_data(as_text=True)


def test_lists_endpoint_is_public_and_complete(scene):
    app, *_ = scene
    body = app.test_client().get("/api/v1/lists").get_json()
    assert set(body["instruments"]) == set(taxonomy.INSTRUMENTS) and "bass" in body["instruments"]
    assert set(body["genres"]) == set(taxonomy.GENRES) and set(body["goals"]) == set(taxonomy.GOALS)
    assert body["statuses"] == ["going", "interested"]


# ------------------------------------------------------------------ profile fields and people search
def edit_profile(client, **extra):
    data = {"name": "Rita Test", "email": "rita@example.com", "about": "I like rock", "kind": "fan",
            "_csrf": token_of(client, "/edit")}
    data.update(extra)
    return client.post("/edit", data=data, follow_redirects=True)


def test_profile_saves_validated_instruments_genres_and_goals(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    edit_profile(rita, instruments=["bass", "drums", "kazoo", "bass"], genres=["rock", "nonsense"], goals=["jam", "x"])
    with app.app_context():
        tags = social.user_tags(user_id(app, "rita"))
    assert tags == {"instruments": ["bass", "drums"], "instrument_levels": {"bass": None, "drums": None},
                    "genres": ["rock"], "goals": ["jam"]}
    page = sam.get("/users_list/%d" % user_id(app, "rita")).get_data(as_text=True)
    assert "Bass" in page and "Drums" in page and "Rock" in page and "Jam partners" in page
    edit_profile(rita)                                                             # nothing ticked = all cleared
    with app.app_context():
        assert social.user_tags(user_id(app, "rita")) == {"instruments": [], "instrument_levels": {}, "genres": [], "goals": []}


def test_edit_form_shows_the_current_choices_and_the_privacy_switch(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    edit_profile(rita, instruments=["cello"], hide_plans="1")
    html = rita.get("/edit").get_data(as_text=True)
    assert 'name="instruments" value="cello" checked' in html and 'name="hide_plans" value="1" checked' in html
    assert 'value="bass"' in html and 'name="instruments" value="bass" checked' not in html      # offered, not ticked


def test_people_search_filters_by_what_they_play_like_and_look_for(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    edit_profile(rita, instruments=["bass"], genres=["punk"], goals=["jam"])
    sam_page = {"name": "Sam Test", "email": "sam@example.com", "about": "x", "kind": "fan", "_csrf": token_of(sam, "/edit")}
    sam.post("/edit", data={**sam_page, "instruments": ["drums"], "genres": ["jazz"], "goals": ["bandmates"]})
    names = lambda **q: sorted(u["username"] for u in dee.get("/api/v1/people", query_string=q).get_json()["people"])  # noqa: E731
    assert "rita" in names() and "dee" in names()
    assert names(instrument="bass") == ["rita"] and names(genre="jazz") == ["sam"] and names(goal="jam") == ["rita"]
    assert names(instrument="bass", genre="jazz") == []
    html = dee.get("/people?instrument=drums").get_data(as_text=True)
    assert "sam" in html and ">rita<" not in html and "chip-instrument" in html and "Drums" in html
    assert names(kind="band") == ["kings"] and names(q="RIT") == ["rita"]


def test_people_search_leaves_out_blocked_members_and_validates_input(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    post_form(dee, "/block/%d" % user_id(app, "sam"))
    listed = lambda c: [u["username"] for u in c.get("/api/v1/people").get_json()["people"]]  # noqa: E731
    assert "sam" not in listed(dee) and "dee" not in listed(sam) and "sam" in listed(rita)
    assert dee.get("/api/v1/people?genre=polka").status_code == 400
    page = dee.get("/api/v1/people?limit=2&offset=1").get_json()
    assert page["count"] == 2 and page["limit"] == 2 and page["offset"] == 1
    assert dee.get("/api/v1/people?limit=-5&offset=-3").get_json()["limit"] == 1
    assert app.test_client().get("/api/v1/people").status_code == 401


def test_suspended_members_are_not_found(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'sam'")
    assert "sam" not in [u["username"] for u in rita.get("/api/v1/people").get_json()["people"]]


# ------------------------------------------------------------------ genres of gigs
def test_a_members_gig_carries_a_valid_genre_into_the_api_and_the_card(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    assert scalar(app, "SELECT genre FROM posts") == "rock"
    announce(kings, genre="polka", days=5, place="Elsewhere", lat=51.5395, lon=-0.1435)
    assert scalar(app, "SELECT genre FROM posts WHERE event_place = 'Elsewhere'") is None          # unknown ignored
    gigs = rita.get("/api/v1/gigs/nearby", query_string={"lat": 51.54, "lon": -0.14, "radius_km": 5, "source": "community"}).get_json()["gigs"]
    assert {g["genre_key"] for g in gigs} == {"rock", None}
    assert "chip-genre" in rita.get("/feed").get_data(as_text=True)


def test_nearby_filters_by_genre_and_carries_the_going_counts(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "R1", "Rock Night", genre="Alternative Rock", lat=51.5395)
    add_external(app, "J1", "Jazz Night", genre="Jazz", lat=51.5400)
    add_external(app, "U1", "Mystery Night", genre="Undefined", lat=51.5405)
    rita = register(app, "rita")
    go(rita, "ticketmaster", "J1")
    ask = lambda **q: rita.get("/api/v1/gigs/nearby", query_string={"lat": 51.54, "lon": -0.14, "radius_km": 3, **q}).get_json()  # noqa: E731
    everything = ask()
    assert {g["title"]: g["genre_key"] for g in everything["gigs"]} == {
        "Rock Night": "indie_alternative", "Jazz Night": "jazz", "Mystery Night": None}
    assert [g["title"] for g in ask(genre="jazz")["gigs"]] == ["Jazz Night"]
    assert [g["title"] for g in ask(genre="indie_alternative")["gigs"]] == ["Rock Night"]
    jazz = [g for g in everything["gigs"] if g["title"] == "Jazz Night"][0]
    assert jazz["my_status"] == "going" and jazz["going_count"] == 1 and jazz["ref"] == "J1" and jazz["source"] == "ticketmaster"
    assert rita.get("/api/v1/gigs/nearby?lat=1&lon=1&genre=polka").get_json()["code"] == "bad_genre"
    assert len({frozenset(g) for g in everything["gigs"]}) == 1                   # every item has the same keys


@pytest.mark.parametrize("text,key", [
    ("Rock", "rock"), ("Alternative Rock", "indie_alternative"), ("Hard Rock", "rock"), ("Pop", "pop"),
    ("Dance/Electronic", "electronic"), ("Hip-Hop/Rap", "hip_hop"), ("R&B", "rnb_soul"), ("Metal", "metal"),
    ("Punk Rock", "punk"), ("Jazz", "jazz"), ("Blues", "blues"), ("Folk", "folk"), ("Country", "country"),
    ("Classical", "classical"), ("Reggae", "reggae"), ("Ska", "reggae"), ("Latin", "world_latin"),
    ("World", "world_latin"), ("Polka", "other"), ("Undefined", None), ("", None), (None, None)])
def test_provider_genres_map_onto_the_shared_list(text, key):
    assert taxonomy.genre_key(text) == key


def test_only_valid_keeps_real_keys_once_in_order():
    assert taxonomy.only_valid(["b", "x", "a", "b"], {"a": 1, "b": 2}) == ["b", "a"]
    assert taxonomy.only_valid(None, {"a": 1}) == []
    assert all(k and " " not in k for lst in (taxonomy.INSTRUMENTS, taxonomy.GENRES, taxonomy.GOALS) for k in lst)


# ------------------------------------------------------------------ pages and the member's own data
def test_gig_pages_have_the_panel_and_imported_events_too(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "TM1", "Gilla Band")
    rita = register(app, "rita")
    eid = scalar(app, "SELECT id FROM external_events")
    html = rita.get("/events/%d" % eid).get_data(as_text=True)
    assert "Who is going" in html and "Going</button>" in html and "Nobody has said they are going yet" in html
    go(rita, "ticketmaster", "TM1")
    assert "1 going" in rita.get("/events/%d" % eid).get_data(as_text=True)
    assert "Who is going" not in rita.get("/gigs").get_data(as_text=True).split("Gigs near you")[0]


def test_export_and_erasure_cover_the_social_data(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    edit_profile(rita, instruments=["bass"], genres=["rock"], goals=["jam"], hide_plans="1")
    go(rita, "community", ref)
    data = json.loads(zipfile.ZipFile(io.BytesIO(rita.get("/account/export").data)).read("data.json"))
    assert data["interests"] == {"instruments": ["bass"], "instrument_levels": {"bass": None}, "genres": ["rock"],
                                 "goals": ["jam"]}
    assert data["gig_plans"][0]["status"] == "going" and data["profile"]["hide_gig_plans"] is True
    rita.post("/account/delete", data={"password": "S3cret!pw", "confirm": "DELETE", "_csrf": token_of(rita, "/account/")})
    for table in ("attendances", "user_instruments", "user_genres", "user_goals"):
        assert scalar(app, "SELECT count(*) FROM %s" % table) == 0, table


def test_an_admin_can_erase_a_member_with_social_data(scene):
    app, kings, rita, sam, dee, boss, ref = scene
    go(sam, "community", ref)
    with app.app_context():
        social.save_tags(user_id(app, "sam"), ["drums"], ["jazz"], ["jam"])
    boss.post("/admin/users/%d/delete" % user_id(app, "sam"), data={"_csrf": token_of(boss, "/admin/users")})
    assert scalar(app, "SELECT count(*) FROM attendances") == 0 and scalar(app, "SELECT count(*) FROM user_instruments") == 0


def test_migration_adds_the_social_tables_and_keeps_old_chats_open(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from rockconnect.migrate import upgrade
    engine = create_engine("sqlite:///" + str(tmp_path / "m.sqlite"))
    upgrade(engine, "0006")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (username, name, email, password, about) VALUES ('a','A','a@a.aa','h','x'),"
                          " ('b','B','b@b.bb','h','x')"))
        conn.execute(text("INSERT INTO conversations (user_low_id, user_high_id, created_at) VALUES (1, 2, '2026-01-01 00:00:00')"))
    upgrade(engine)
    assert {"attendances", "user_instruments", "user_genres", "user_goals"} <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert tuple(conn.execute(text("SELECT status, initiator_id FROM conversations")).fetchone()) == ("accepted", None)
