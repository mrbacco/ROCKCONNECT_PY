# File: test_private.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Private accounts: findable, but posts, photos, comments and plans only for the members they accepted."""
import io
import json
import zipfile
from datetime import datetime, timedelta

import pytest

from helpers import FETCH, make_app, post_form, register, scalar, sql, token_of, user_id

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32


def post(client, body="hello", image=False, gig=False):
    data = {"_csrf": token_of(client, "/feed"), "body": body}
    if gig:
        data.update(event_at=(datetime.now() + timedelta(days=4)).strftime("%Y-%m-%dT20:00"), event_place="Camden Underworld",
                    event_lat="51.539", event_lon="-0.143")
    if image:
        data["image"] = (io.BytesIO(PNG), "x.png")
    return client.post("/posts", data=data, content_type="multipart/form-data", follow_redirects=True)


def see(client, url="/feed"):
    return client.get(url).get_data(as_text=True)


def go_private(client, on=True):
    data = {"_csrf": token_of(client, "/edit"), "name": "Mia Private", "email": "mia@example.com", "kind": "band",
            "about": "I like rock", "location": "", "website": ""}
    if on:
        data["is_private"] = "1"
    return client.post("/edit", data=data, follow_redirects=True)


def ask(client, app, name):
    return client.post("/follow/%d" % user_id(app, name), data={"_csrf": token_of(client, "/feed")}, follow_redirects=True)


def answer(client, app, name, what):
    return client.post("/follow-requests/%d/%s" % (user_id(app, name), what),
                       data={"_csrf": token_of(client, "/follow-requests")}, follow_redirects=True)


def api(client, method, url, **data):
    return getattr(client, method)(url, data={"_csrf": token_of(client, "/feed"), **data})


@pytest.fixture
def scene(tmp_path):
    """mia (a band) goes private after posting; rita asks, sam stays a stranger, pub is a public venue."""
    app = make_app(tmp_path)
    mia = register(app, "mia", kind="band")
    rita, sam, pub = register(app, "rita"), register(app, "sam"), register(app, "pub", kind="venue")
    post(mia, "mia public era post")
    post(mia, "mia secret gig", gig=True, image=True)
    post(pub, "pub post for everyone")
    go_private(mia)
    return app, mia, rita, sam, pub


# ------------------------------------------------------------------ the setting
def test_the_setting_is_in_edit_profile_and_saved(scene):
    app, mia, rita, sam, pub = scene
    assert scalar(app, "SELECT is_private FROM users WHERE username = 'mia'") == 1
    assert 'name="is_private"' in see(mia, "/edit") and "checked" in see(mia, "/edit").split('name="is_private"')[1][:80]
    assert scalar(app, "SELECT is_private FROM users WHERE username = 'rita'") == 0
    go_private(mia, on=False)
    assert scalar(app, "SELECT is_private FROM users WHERE username = 'mia'") == 0


def test_a_private_member_is_still_found_and_their_profile_shows_the_basics(scene):
    app, mia, rita, sam, pub = scene
    assert "mia" in see(rita, "/people?q=mia")
    profile = see(rita, "/users_list/%d" % user_id(app, "mia"))
    assert "Mia Private" in profile and "I like rock" in profile and "private" in profile
    assert "This account is private." in profile and "Request to follow" in profile
    people = rita.get("/api/v1/people", query_string={"q": "mia"}).get_json()["people"]
    assert [(p["username"], p["private"]) for p in people] == [("mia", True)]


# ------------------------------------------------------------------ what a stranger cannot see
def test_a_stranger_sees_none_of_their_content(scene):
    app, mia, rita, sam, pub = scene
    for client in (rita, sam, pub):
        for url in ("/feed", "/gigs", "/users_list/%d" % user_id(app, "mia")):
            page = see(client, url)
            assert "mia public era post" not in page and "mia secret gig" not in page, url
        assert client.get("/posts/1").status_code == 404 and client.get("/posts/2").status_code == 404
    assert "pub post for everyone" in see(rita, "/feed")                                # other people's posts are untouched


def test_the_owner_still_sees_everything(scene):
    app, mia, rita, sam, pub = scene
    assert "mia public era post" in see(mia, "/feed") and "mia secret gig" in see(mia, "/users_list/%d" % user_id(app, "mia"))
    assert mia.get("/posts/2").status_code == 200


def test_their_photo_cannot_be_opened_by_a_stranger_even_with_the_link(scene):
    app, mia, rita, sam, pub = scene
    name = scalar(app, "SELECT image_filename FROM posts WHERE id = 2")
    assert mia.get("/uploads/%s" % name).status_code == 200
    assert rita.get("/uploads/%s" % name).status_code == 404 and sam.get("/uploads/%s" % name).status_code == 404
    ask(rita, app, "mia")
    answer(mia, app, "rita", "accept")
    assert rita.get("/uploads/%s" % name).status_code == 200 and sam.get("/uploads/%s" % name).status_code == 404
    other = scalar(app, "SELECT image_filename FROM posts WHERE user_id = (SELECT id FROM users WHERE username = 'pub')")
    assert other is None                                                                  # (the public post had no photo)


def test_their_gig_is_not_on_the_board_in_search_or_joinable(scene):
    app, mia, rita, sam, pub = scene
    nearby = rita.get("/api/v1/gigs/nearby", query_string={"lat": 51.539, "lon": -0.143, "radius_km": 10}).get_json()
    assert nearby["total"] == 0
    r = rita.post("/gigs/attendance", data={"_csrf": token_of(rita, "/feed"), "source": "community", "ref": "2", "status": "going"}, headers=FETCH)
    assert r.status_code == 404
    ask(rita, app, "mia")
    answer(mia, app, "rita", "accept")
    assert rita.get("/api/v1/gigs/nearby", query_string={"lat": 51.539, "lon": -0.143, "radius_km": 10}).get_json()["total"] == 1
    assert sam.get("/api/v1/gigs/nearby", query_string={"lat": 51.539, "lon": -0.143, "radius_km": 10}).get_json()["total"] == 0


def test_their_comments_and_gig_comments_are_hidden_from_strangers(scene):
    app, mia, rita, sam, pub = scene
    mia.post("/posts/3/comments", data={"_csrf": token_of(mia, "/feed"), "body": "mia secret remark"})
    assert "mia secret remark" in see(mia, "/posts/3") and "mia secret remark" not in see(rita, "/posts/3")
    # the discussion under a public gig
    post(pub, "public gig night", gig=True)
    gig = scalar(app, "SELECT id FROM posts WHERE body = 'public gig night'")
    mia.post("/api/v1/gigs/community/%d/comments" % gig, data={"_csrf": token_of(mia, "/feed"), "body": "mia gig talk"})
    sam.post("/api/v1/gigs/community/%d/comments" % gig, data={"_csrf": token_of(sam, "/feed"), "body": "sam gig talk"})
    assert [c["body"] for c in rita.get("/api/v1/gigs/community/%d/comments" % gig).get_json()["comments"]] == ["sam gig talk"]
    assert [c["body"] for c in mia.get("/api/v1/gigs/community/%d/comments" % gig).get_json()["comments"]] == ["mia gig talk", "sam gig talk"]
    ask(rita, app, "mia")
    answer(mia, app, "rita", "accept")
    assert "mia secret remark" in see(rita, "/posts/3")
    assert [c["body"] for c in rita.get("/api/v1/gigs/community/%d/comments" % gig).get_json()["comments"]] == ["mia gig talk", "sam gig talk"]


def test_their_plans_are_not_named_in_who_is_going_nor_on_the_profile(scene):
    app, mia, rita, sam, pub = scene
    post(pub, "pub gig", gig=True)
    gig = scalar(app, "SELECT id FROM posts WHERE body = 'pub gig'")
    for client in (mia, sam):
        client.post("/gigs/attendance", data={"_csrf": token_of(client, "/feed"), "source": "community", "ref": str(gig), "status": "going"}, headers=FETCH)
    who = lambda c: [p["username"] for p in c.get("/api/v1/gigs/community/%d/attendees" % gig).get_json()["people"]]  # noqa: E731
    assert who(rita) == ["sam"]                                                          # mia is not named to a stranger...
    assert rita.get("/api/v1/gigs/community/%d/attendees" % gig).get_json()["going_count"] == 2      # ...but still counted
    assert "Going to" not in see(rita, "/users_list/%d" % user_id(app, "mia"))
    ask(rita, app, "mia")
    answer(mia, app, "rita", "accept")
    assert sorted(who(rita)) == ["mia", "sam"]
    assert "Going to" in see(rita, "/users_list/%d" % user_id(app, "mia"))


# ------------------------------------------------------------------ requests
def test_asking_to_follow_makes_a_request_not_a_follow(scene):
    app, mia, rita, sam, pub = scene
    page = ask(rita, app, "mia")
    assert b"Follow request sent" in page.data
    assert scalar(app, "SELECT count(*) FROM follow_requests") == 1 and scalar(app, "SELECT count(*) FROM follows") == 0
    profile = see(rita, "/users_list/%d" % user_id(app, "mia"))
    assert "Requested" in profile and "waiting for Mia Private to accept" in profile
    assert "mia public era post" not in profile
    ask(rita, app, "mia")                                                                  # a second click changes nothing
    assert scalar(app, "SELECT count(*) FROM follow_requests") == 1
    assert scalar(app, "SELECT count(*) FROM notifications WHERE user_id = :u AND kind = 'follow_request'", u=user_id(app, "mia")) == 1


def test_the_owner_is_told_and_sees_the_request(scene):
    app, mia, rita, sam, pub = scene
    ask(rita, app, "mia")
    assert "asked to follow you" in see(mia, "/notifications")
    assert 'id="request-dot"' in see(mia) and "Follow requests" in see(mia) and ">1</span>" in see(mia).split("Follow requests")[1][:200]
    page = see(mia, "/follow-requests")
    assert "Rita Test" in page and "Accept" in page and "Decline" in page
    assert 'id="request-dot"' not in see(rita)
    assert mia.get("/api/v1/me/follow-requests").get_json()["total"] == 1


def test_accepting_lets_them_in_and_tells_them(scene):
    app, mia, rita, sam, pub = scene
    ask(rita, app, "mia")
    assert b"they can see your posts now" in answer(mia, app, "rita", "accept").data
    assert scalar(app, "SELECT count(*) FROM follow_requests") == 0 and scalar(app, "SELECT count(*) FROM follows") == 1
    profile = see(rita, "/users_list/%d" % user_id(app, "mia"))
    assert "mia secret gig" in profile and "Following" in profile
    assert "mia public era post" in see(rita, "/feed") and "mia public era post" not in see(sam, "/feed")
    assert "accepted your follow request" in see(rita, "/notifications")
    assert 'id="request-dot"' not in see(mia)


def test_declining_removes_the_request_and_says_nothing(scene):
    app, mia, rita, sam, pub = scene
    ask(rita, app, "mia")
    assert b"Request removed" in answer(mia, app, "rita", "decline").data
    assert scalar(app, "SELECT count(*) FROM follow_requests") == 0 and scalar(app, "SELECT count(*) FROM follows") == 0
    assert scalar(app, "SELECT count(*) FROM notifications WHERE user_id = :u", u=user_id(app, "rita")) == 0
    assert "Request to follow" in see(rita, "/users_list/%d" % user_id(app, "mia"))          # she can ask again, and is not told no


def test_withdrawing_a_request(scene):
    app, mia, rita, sam, pub = scene
    ask(rita, app, "mia")
    rita.post("/unfollow/%d" % user_id(app, "mia"), data={"_csrf": token_of(rita, "/feed")})
    assert scalar(app, "SELECT count(*) FROM follow_requests") == 0 and "Request to follow" in see(rita, "/users_list/%d" % user_id(app, "mia"))


def test_only_the_owner_can_answer_and_only_real_requests(scene):
    app, mia, rita, sam, pub = scene
    ask(rita, app, "mia")
    answer(sam, app, "rita", "accept")                                                      # sam is nobody to rita's request
    assert scalar(app, "SELECT count(*) FROM follows") == 0 and scalar(app, "SELECT count(*) FROM follow_requests") == 1
    assert b"not there any more" in answer(mia, app, "sam", "accept").data
    answer(rita, app, "mia", "accept")                                                      # a requester cannot accept her own request
    assert scalar(app, "SELECT count(*) FROM follows") == 0
    assert app.test_client().post("/follow-requests/1/accept", data={}).status_code in (302, 400)


def test_a_public_member_is_followed_at_once_as_before(scene):
    app, mia, rita, sam, pub = scene
    ask(rita, app, "pub")
    assert scalar(app, "SELECT count(*) FROM follows") == 1 and scalar(app, "SELECT count(*) FROM follow_requests") == 0


def test_people_who_followed_before_keep_their_access(tmp_path):
    app = make_app(tmp_path)
    mia, rita = register(app, "mia", kind="band"), register(app, "rita")
    post(mia, "before private")
    ask(rita, app, "mia")
    go_private(mia)
    assert "before private" in see(rita, "/feed")


def test_turning_private_off_lets_everyone_waiting_in(scene):
    app, mia, rita, sam, pub = scene
    ask(rita, app, "mia"), ask(sam, app, "mia")
    go_private(mia, on=False)
    assert scalar(app, "SELECT count(*) FROM follow_requests") == 0 and scalar(app, "SELECT count(*) FROM follows") == 2
    assert "mia public era post" in see(sam, "/feed")


def test_blocking_removes_follows_and_requests_both_ways(scene):
    app, mia, rita, sam, pub = scene
    ask(rita, app, "mia")
    post_form(mia, "/block/%d" % user_id(app, "rita"))
    assert scalar(app, "SELECT count(*) FROM follow_requests") == 0
    assert "cannot follow" in ask(rita, app, "mia").get_data(as_text=True)
    ask(sam, app, "mia")
    answer(mia, app, "sam", "accept")
    post_form(sam, "/block/%d" % user_id(app, "mia"))
    assert scalar(app, "SELECT count(*) FROM follows") == 0 and "mia public era post" not in see(sam, "/feed")


def test_the_follow_request_rules_match_normal_follows(scene):
    app, mia, rita, sam, pub = scene
    assert "cannot follow yourself" in ask(mia, app, "mia").get_data(as_text=True)
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'sam'")
    assert b"cannot follow" in ask(rita, app, "sam").data
    assert rita.post("/follow/99999", data={"_csrf": token_of(rita, "/feed")}).status_code == 404


# ------------------------------------------------------------------ the API
def test_the_api_for_requests(scene):
    app, mia, rita, sam, pub = scene
    r = api(rita, "post", "/api/v1/people/%d/follow" % user_id(app, "mia"))
    assert r.get_json() == {"following": False, "requested": True, "followers": 0}
    assert api(rita, "post", "/api/v1/people/%d/follow" % user_id(app, "pub")).get_json() == {"following": True, "requested": False, "followers": 1}
    waiting = mia.get("/api/v1/me/follow-requests").get_json()
    assert [(p["username"], p["private"]) for p in waiting["people"]] == [("rita", False)] and "asked_at" in waiting["people"][0]
    assert api(sam, "post", "/api/v1/follow-requests/%d/accept" % user_id(app, "rita")).get_json()["code"] == "not_found"
    assert api(mia, "post", "/api/v1/follow-requests/%d/accept" % user_id(app, "rita")).get_json() == {"accepted": True}
    assert mia.get("/api/v1/me/followers").get_json()["total"] == 1
    api(sam, "post", "/api/v1/people/%d/follow" % user_id(app, "mia"))
    assert api(mia, "post", "/api/v1/follow-requests/%d/decline" % user_id(app, "sam")).get_json() == {"declined": True}
    assert api(mia, "post", "/api/v1/follow-requests/%d/decline" % user_id(app, "sam")).status_code == 404
    unfollowed = api(rita, "post", "/api/v1/people/%d/unfollow" % user_id(app, "mia"))
    assert unfollowed.get_json() == {"following": False, "requested": False, "followers": 0}
    assert app.test_client().get("/api/v1/me/follow-requests").status_code == 401
    assert app.test_client().get("/api/v1/meta").get_json()["features"]["private_accounts"] is True


# ------------------------------------------------------------------ alerts, privacy of the data itself
def test_going_alerts_only_reach_accepted_followers(scene):
    app, mia, rita, sam, pub = scene
    post(pub, "pub gig", gig=True)
    gig = scalar(app, "SELECT id FROM posts WHERE body = 'pub gig'")
    ask(rita, app, "mia"), ask(sam, app, "mia")
    answer(mia, app, "rita", "accept")                                                      # sam is still waiting
    mia.post("/gigs/attendance", data={"_csrf": token_of(mia, "/feed"), "source": "community", "ref": str(gig), "status": "going"}, headers=FETCH)
    assert "is going to" in see(rita, "/notifications") and "is going to" not in see(sam, "/notifications")


def test_export_and_erasure_cover_requests(scene):
    app, mia, rita, sam, pub = scene
    ask(rita, app, "mia")
    ask(mia, app, "pub")
    data = json.loads(zipfile.ZipFile(io.BytesIO(rita.get("/account/export").data)).read("data.json"))
    assert data["follow_requests_sent"] == ["mia"] and data["follow_requests_received"] == []
    mine = json.loads(zipfile.ZipFile(io.BytesIO(mia.get("/account/export").data)).read("data.json"))
    assert mine["follow_requests_received"] == ["rita"] and mine["profile"]["private_account"] is True
    assert data["profile"]["private_account"] is False
    rita.post("/account/delete", data={"_csrf": token_of(rita, "/account/"), "password": "S3cret!pw", "confirm": "DELETE"})
    assert scalar(app, "SELECT count(*) FROM follow_requests WHERE follower_id = :u OR followed_id = :u", u=user_id(app, "mia")) == 0
    assert scalar(app, "SELECT count(*) FROM notifications WHERE kind = 'follow_request'") == 0       # the alert about her is gone too


def test_the_requests_page_for_a_public_member_explains_itself(scene):
    app, mia, rita, sam, pub = scene
    page = see(pub, "/follow-requests")
    assert "Your account is public" in page and "Nobody is waiting" in page
    assert "Follow requests" not in see(pub).split("dropdown-menu")[1].split("</div>")[0]            # no clutter in the menu
