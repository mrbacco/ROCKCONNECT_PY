# File: test_follows.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Following people, 'people I follow are going', and the notifications that come with them."""
import io
import json
import zipfile
from datetime import datetime, timedelta

import pytest

from helpers import FETCH, limits, make_app, post_form, register, scalar, sql, token_of, user_id
from rockconnect import notifications


def born(years):
    from rockconnect import ages
    return ages.cutoff(years)


@pytest.fixture
def scene(tmp_path):
    """A band with a gig; rita, sam, dee and eve are fans."""
    app = make_app(tmp_path)
    kings = register(app, "kings", kind="band")
    when = (datetime.now() + timedelta(days=4)).strftime("%Y-%m-%dT20:00")
    kings.post("/posts", data={"_csrf": token_of(kings, "/feed"), "body": "our gig", "event_at": when,
                               "event_place": "Camden", "event_lat": "51.539", "event_lon": "-0.143", "genre": "rock"})
    people = {name: register(app, name) for name in ("rita", "sam", "dee", "eve")}
    return (app, kings, people["rita"], people["sam"], people["dee"], people["eve"], str(scalar(app, "SELECT id FROM posts")))


def follow(client, app, name, undo=False):
    return client.post("/%sfollow/%d" % ("un" if undo else "", user_id(app, name)),
                       data={"_csrf": token_of(client, "/feed")}, follow_redirects=True)


def going(client, ref, status="going", **extra):
    return client.post("/gigs/attendance", data={"_csrf": token_of(client, "/feed"), "source": "community", "ref": ref,
                                                  "status": status, **extra}, headers=FETCH)


def alerts(client):
    return client.get("/api/v1/notifications").get_json()


def texts(client):
    return [n["text"] for n in alerts(client)["notifications"]]


# ------------------------------------------------------------------ following
def test_follow_and_unfollow_with_counts_on_the_profile(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    page = follow(rita, app, "sam").get_data(as_text=True)
    assert "Following" in page and "1 follower" in page
    assert scalar(app, "SELECT count(*) FROM follows") == 1
    follow(rita, app, "sam")                                                       # twice is harmless
    assert scalar(app, "SELECT count(*) FROM follows") == 1
    follow(dee, app, "sam")
    assert "2 followers" in sam.get("/users_list/%d" % user_id(app, "sam")).get_data(as_text=True)
    page = follow(rita, app, "sam", undo=True).get_data(as_text=True)
    assert "1 follower" in page and "Follow</button>" in page


def test_you_cannot_follow_yourself_or_a_missing_member(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    assert b"cannot follow yourself" in follow(rita, app, "rita").data
    assert rita.post("/follow/99999", data={"_csrf": token_of(rita, "/feed")}).status_code == 404
    assert scalar(app, "SELECT count(*) FROM follows") == 0
    api = rita.post("/api/v1/people/99999/follow", data={"_csrf": token_of(rita, "/feed")})
    assert api.status_code == 404 and api.get_json()["code"] == "not_found"


def test_blocked_and_suspended_members_cannot_be_followed(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    post_form(sam, "/block/%d" % user_id(app, "rita"))
    assert b"cannot follow this person" in follow(rita, app, "sam").data and b"cannot follow this person" in follow(sam, app, "rita").data
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'dee'")
    assert b"cannot follow this person" in follow(rita, app, "dee").data
    assert scalar(app, "SELECT count(*) FROM follows") == 0


def test_blocking_removes_follows_in_both_directions(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam"), follow(sam, app, "rita")
    assert scalar(app, "SELECT count(*) FROM follows") == 2
    post_form(rita, "/block/%d" % user_id(app, "sam"))
    assert scalar(app, "SELECT count(*) FROM follows") == 0


def test_the_people_page_lists_who_you_follow_and_who_follows_you(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam"), follow(rita, app, "dee"), follow(eve, app, "rita")
    names = lambda c, rel: sorted(p["username"] for p in c.get("/api/v1/me/%s" % rel).get_json()["people"])  # noqa: E731
    assert names(rita, "following") == ["dee", "sam"] and names(rita, "followers") == ["eve"]
    assert names(sam, "following") == [] and names(sam, "followers") == ["rita"]
    page = rita.get("/people?relation=following").get_data(as_text=True)
    assert ">sam<" in page and ">dee<" in page and ">eve<" not in page and "My followers" in page
    assert ">eve<" in rita.get("/people?relation=followers").get_data(as_text=True)
    assert "You are not following anyone yet" in sam.get("/people?relation=following").get_data(as_text=True)
    assert all(p["following"] for p in rita.get("/api/v1/me/following").get_json()["people"])


def test_suspended_people_disappear_from_the_lists_and_counts(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam")
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'sam'")
    assert rita.get("/api/v1/me/following").get_json()["total"] == 0
    from rockconnect import follows
    with app.app_context():
        assert follows.counts(user_id(app, "rita")) == (0, 0)


def test_the_following_feed_shows_only_those_you_follow_and_yourself(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    for client, text in ((sam, "post from sam"), (dee, "post from dee"), (rita, "post from rita")):
        client.post("/posts", data={"_csrf": token_of(client, "/feed"), "body": text})
    follow(rita, app, "sam")
    everyone = rita.get("/feed").get_data(as_text=True)
    mine = rita.get("/feed?following=1").get_data(as_text=True)
    assert "post from dee" in everyone and "post from sam" in everyone
    assert "post from sam" in mine and "post from rita" in mine and "post from dee" not in mine and "our gig" not in mine
    assert "Nothing from the people you follow yet" in eve.get("/feed?following=1").get_data(as_text=True)


def test_following_is_rate_limited(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(follow_user=(2, 3600)))
    rita = register(app, "rita")
    for name in ("sam", "dee", "eve"):
        register(app, name)
    assert follow(rita, app, "sam").status_code == 200 and follow(rita, app, "dee").status_code == 200
    assert rita.post("/follow/%d" % user_id(app, "eve"), data={"_csrf": token_of(rita, "/feed")}).status_code == 429


def test_the_api_follow_calls(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    r = rita.post("/api/v1/people/%d/follow" % user_id(app, "sam"), data={"_csrf": token_of(rita, "/feed")})
    assert r.get_json() == {"following": True, "requested": False, "followers": 1}
    r = rita.post("/api/v1/people/%d/unfollow" % user_id(app, "sam"), data={"_csrf": token_of(rita, "/feed")})
    assert r.get_json() == {"following": False, "requested": False, "followers": 0}
    assert app.test_client().get("/api/v1/me/following").status_code == 401
    own = rita.post("/api/v1/people/%d/follow" % user_id(app, "rita"), data={"_csrf": token_of(rita, "/feed")})
    assert own.status_code == 403 and own.get_json()["code"] == "cannot_follow"


# ------------------------------------------------------------------ the people you follow are going
def test_people_you_follow_are_counted_named_and_listed_first(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(eve, app, "dee")
    for fan in (rita, sam, dee):
        going(fan, ref)
    attendees = eve.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()["people"]
    assert [p["username"] for p in attendees][0] == "dee" and attendees[0]["following"] is True
    assert {p["username"]: p["following"] for p in attendees} == {"dee": True, "rita": False, "sam": False}
    html = eve.get("/posts/%s" % ref).get_data(as_text=True)
    assert "1 you follow" in html and "you follow</span>" in html
    assert "you follow" not in rita.get("/posts/%s" % ref).get_data(as_text=True).replace("people you follow", "")


def test_a_private_plan_of_someone_you_follow_stays_private(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(eve, app, "dee")
    going(dee, ref)
    going(dee, ref, visible="0")
    gigs = eve.get("/api/v1/gigs/nearby", query_string={"lat": 51.54, "lon": -0.14, "radius_km": 3}).get_json()["gigs"]
    assert gigs[0]["going_count"] == 1 and gigs[0]["friends_going"] == 0              # counted, but not as a named friend
    assert eve.get("/api/v1/gigs/community/%s/attendees" % ref).get_json()["people"] == []


def test_nearby_items_say_how_many_you_follow_are_going(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(eve, app, "rita"), follow(eve, app, "sam")
    for fan in (rita, sam, dee):
        going(fan, ref)
    item = eve.get("/api/v1/gigs/nearby", query_string={"lat": 51.54, "lon": -0.14, "radius_km": 3}).get_json()["gigs"][0]
    assert item["going_count"] == 3 and item["friends_going"] == 2
    assert len({frozenset(g) for g in eve.get("/api/v1/gigs/nearby", query_string={"lat": 51.54, "lon": -0.14}).get_json()["gigs"]}) == 1


# ------------------------------------------------------------------ notifications
def test_a_new_follower_is_announced_once(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam")
    assert texts(sam) == ["Rita Test started following you."]
    follow(rita, app, "sam", undo=True), follow(rita, app, "sam")                       # unfollow and follow again
    assert len(texts(sam)) == 1                                                          # not told twice
    follow(dee, app, "sam")
    assert len(texts(sam)) == 2 and texts(rita) == []


def test_followers_are_told_when_you_say_you_are_going(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam"), follow(dee, app, "sam")
    going(sam, ref)
    for who in (rita, dee):
        news = [n for n in alerts(who)["notifications"] if n["kind"] == "friend_going"]
        assert len(news) == 1 and news[0]["text"].startswith("Sam Test is going to Kings Test on ")
        assert news[0]["url"] == "/posts/%s" % ref
    assert [n for n in alerts(eve)["notifications"]] == [] and [n for n in alerts(sam)["notifications"] if n["kind"] == "friend_going"] == []


def test_nobody_is_told_about_interested_private_or_hidden_plans(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam")
    going(sam, ref, "interested")
    assert [n for n in alerts(rita)["notifications"] if n["kind"] == "friend_going"] == []
    going(sam, ref, "going", visible="0")                                               # going, but privately
    assert [n for n in alerts(rita)["notifications"] if n["kind"] == "friend_going"] == []
    going(sam, ref, "none")
    sql(app, "UPDATE users SET hide_plans = 1 WHERE username = 'sam'")
    going(sam, ref)
    assert [n for n in alerts(rita)["notifications"] if n["kind"] == "friend_going"] == []


def test_making_a_private_plan_public_tells_the_followers_then(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam")
    going(sam, ref, "going", visible="0")
    sam.post("/gigs/attendance", data={"_csrf": token_of(sam, "/feed"), "source": "community", "ref": ref, "visible": "1"}, headers=FETCH)
    assert len([n for n in alerts(rita)["notifications"] if n["kind"] == "friend_going"]) == 1


def test_changing_your_mind_does_not_repeat_the_notification(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam")
    for status in ("going", "none", "going", "interested", "going"):
        going(sam, ref, status)
    assert len([n for n in alerts(rita)["notifications"] if n["kind"] == "friend_going"]) == 1


def test_a_follower_can_switch_the_gig_alerts_off(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam"), follow(dee, app, "sam")
    rita.post("/edit", data={"name": "Rita", "email": "rita@example.com", "about": "x", "kind": "fan", "_csrf": token_of(rita, "/edit")})
    assert scalar(app, "SELECT notify_friends_going FROM users WHERE username = 'rita'") == 0     # box not ticked
    going(sam, ref)
    assert [n for n in alerts(rita)["notifications"] if n["kind"] == "friend_going"] == []
    assert len([n for n in alerts(dee)["notifications"] if n["kind"] == "friend_going"]) == 1
    assert "notify_friends_going" in rita.get("/edit").get_data(as_text=True)


def test_blocked_or_suspended_followers_are_not_told(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam"), follow(dee, app, "sam"), follow(eve, app, "sam")
    sql(app, "INSERT INTO blocks (blocker_id, blocked_id, created_at) VALUES (:a, :b, '2026-01-01 00:00:00')", a=user_id(app, "sam"), b=user_id(app, "rita"))
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'dee'")
    going(sam, ref)
    assert [n for n in alerts(eve)["notifications"] if n["kind"] == "friend_going"] != []
    assert [n for n in alerts(rita)["notifications"] if n["kind"] == "friend_going"] == []


def test_one_event_never_writes_more_than_the_cap(scene, monkeypatch):
    app, kings, rita, sam, dee, eve, ref = scene
    monkeypatch.setattr(notifications, "MAX_PER_EVENT", 2)
    for fan in (rita, dee, eve):
        follow(fan, app, "sam")
    going(sam, ref)
    assert scalar(app, "SELECT count(*) FROM notifications WHERE kind = 'friend_going'") == 2


def test_the_bell_the_page_and_reading(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam"), follow(dee, app, "sam")
    html = sam.get("/feed").get_data(as_text=True)
    assert 'id="notification-badge"' in html and '>2</span>' in html.split('id="notification-badge"')[1][:120]
    page = sam.get("/notifications").get_data(as_text=True)
    assert "Rita Test started following you." in page and "notification-new" in page
    assert alerts(sam)["unread"] == 0                                                    # opening the page read them
    assert "d-none" in sam.get("/feed").get_data(as_text=True).split('id="notification-badge"')[1][:120]
    assert "notification-new" not in sam.get("/notifications").get_data(as_text=True)


def test_the_api_lists_filters_and_marks_read(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam")
    body = alerts(sam)
    assert body["unread"] == 1 and body["count"] == 1 and body["notifications"][0]["read"] is False
    assert sam.get("/api/v1/notifications?unread=1").get_json()["count"] == 1
    assert sam.post("/api/v1/notifications/read", data={"_csrf": token_of(sam, "/feed")}).get_json() == {"unread": 0}
    assert sam.get("/api/v1/notifications?unread=1").get_json()["count"] == 0 and alerts(sam)["notifications"][0]["read"] is True
    assert app.test_client().get("/api/v1/notifications").status_code == 401


def test_you_only_see_your_own_notifications(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam")
    assert alerts(dee)["count"] == 0 and "started following" not in dee.get("/notifications").get_data(as_text=True)
    dee.post("/notifications/read", data={"_csrf": token_of(dee, "/feed")})
    assert alerts(sam)["unread"] == 1                                                    # dee's click did not touch sam's


def test_names_in_notifications_are_escaped(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    rita.post("/edit", data={"name": "<script>alert(1)</script>", "email": "rita@example.com", "about": "x", "kind": "fan",
                             "_csrf": token_of(rita, "/edit")})
    follow(rita, app, "sam")
    page = sam.get("/notifications").get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_old_notifications_are_cleaned_up(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam"), follow(dee, app, "sam")
    sql(app, "UPDATE notifications SET read_at = created_at, created_at = '2000-01-01 00:00:00' WHERE id = 1")      # read, ancient
    sql(app, "UPDATE notifications SET created_at = :d WHERE id = 2", d=(datetime.now() - timedelta(days=100)).strftime("%Y-%m-%d %H:%M:%S"))  # unread, very old
    with app.app_context():
        notifications.prune()
    assert scalar(app, "SELECT count(*) FROM notifications") == 0
    follow(eve, app, "sam")
    with app.app_context():
        notifications.prune()
    assert scalar(app, "SELECT count(*) FROM notifications") == 1                       # a fresh one stays


# ------------------------------------------------------------------ privacy, age, data rights
def test_minors_and_adults_cannot_follow_each_other_when_minors_may_join(tmp_path):
    app = make_app(tmp_path, MIN_AGE=16)
    adult, teen = register(app, "adult", birth_date=born(30)), register(app, "teen", birth_date=born(17))
    register(app, "teen2", birth_date=born(16))
    assert b"cannot follow this person" in follow(adult, app, "teen").data and b"cannot follow this person" in follow(teen, app, "adult").data
    follow(teen, app, "teen2")
    assert scalar(app, "SELECT count(*) FROM follows") == 1


def test_export_and_erasure_include_follows_notifications_and_alert_choice(scene):
    app, kings, rita, sam, dee, eve, ref = scene
    follow(rita, app, "sam"), follow(sam, app, "rita")
    going(sam, ref)
    data = json.loads(zipfile.ZipFile(io.BytesIO(rita.get("/account/export").data)).read("data.json"))
    assert data["following"] == ["sam"] and data["followers"] == ["sam"] and data["profile"]["notify_friends_going"] is True
    rita.post("/account/delete", data={"password": "S3cret!pw", "confirm": "DELETE", "_csrf": token_of(rita, "/account/")})
    assert scalar(app, "SELECT count(*) FROM follows") == 0
    assert scalar(app, "SELECT count(*) FROM notifications WHERE user_id = (SELECT id FROM users WHERE username = 'rita')") == 0
    assert "Rita Test" not in json.dumps(alerts(sam))                    # what she caused in Sam's list is gone too


def test_the_app_can_see_the_new_features(scene):
    app, *_ = scene
    features = app.test_client().get("/api/v1/meta").get_json()["features"]
    assert features["follows"] is True and features["notifications"] is True


def test_migration_adds_the_tables_and_keeps_members_without_a_birth_date(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from rockconnect.migrate import upgrade
    engine = create_engine("sqlite:///" + str(tmp_path / "m.sqlite"))
    upgrade(engine, "0007")
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (username, name, email, password, about) VALUES ('a','A','a@a.aa','h','x')"))
        conn.execute(text("INSERT INTO user_instruments (user_id, instrument) VALUES (1, 'bass')"))
    upgrade(engine)
    assert {"follows", "notifications", "gig_comments"} <= set(inspect(engine).get_table_names())
    with engine.connect() as conn:
        assert tuple(conn.execute(text("SELECT birth_date, notify_friends_going FROM users")).fetchone()) == (None, 1)
        assert tuple(conn.execute(text("SELECT instrument, level FROM user_instruments")).fetchone()) == ("bass", None)
