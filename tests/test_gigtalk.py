# File: test_gigtalk.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""The discussion under a gig: posting, deleting, reporting, who may see what, the same concert on two listings."""
import io
import json
import zipfile
from datetime import date, datetime, timedelta

import pytest

from helpers import make_admin, make_app, post_form, register, scalar, sql, token_of, user_id
from rockconnect import gigtalk

LONG = "x" * 1001


def when(days, hour=20):
    return (datetime.now() + timedelta(days=days)).replace(hour=hour, minute=0)


def stamp(dt):
    return dt.strftime("%Y-%m-%d %H:%M")


def announce(client, days=4):
    client.post("/posts", data={"_csrf": token_of(client, "/feed"), "body": "gig", "event_at": when(days).strftime("%Y-%m-%dT%H:%M"),
                                "event_place": "Camden Underworld", "event_lat": "51.539", "event_lon": "-0.143"})


def add_external(app, ext_id, title, source="ticketmaster", days=4):
    sql(app, "INSERT INTO external_events (source, external_id, title, venue, city, event_at, time_known, latitude,"
             " longitude, ticket_url, genre, area, imported_at, seen_at) VALUES (:s, :e, :t, 'Camden Underworld',"
             " 'London', :at, 1, 51.539, -0.143, 'https://example.com/t', 'Rock', 'London', '2026-01-01 00:00:00',"
             " '2026-01-01 00:00:00')", s=source, e=ext_id, t=title, at=stamp(when(days)))


def say(client, ref, body, source="community", **extra):
    return client.post("/gigs/comment", data={"_csrf": token_of(client, "/feed"), "source": source, "ref": ref, "body": body,
                                              "next": "/gigs", **extra}, follow_redirects=True)


def api_say(client, ref, body, source="community"):
    return client.post("/api/v1/gigs/%s/%s/comments" % (source, ref), data={"_csrf": token_of(client, "/feed"), "body": body})


def texts(client, ref, source="community"):
    return [c["body"] for c in client.get("/api/v1/gigs/%s/%s/comments" % (source, ref)).get_json()["comments"]]


@pytest.fixture
def scene(tmp_path):
    app = make_app(tmp_path)
    kings = register(app, "kings", kind="band")
    announce(kings)
    rita, sam = register(app, "rita"), register(app, "sam")
    boss = register(app, "boss")
    make_admin(app, "boss")
    return app, kings, rita, sam, boss, str(scalar(app, "SELECT id FROM posts"))


# ------------------------------------------------------------------ posting
def test_a_comment_is_stored_and_shown_to_everyone_on_the_gig_page(scene):
    app, kings, rita, sam, boss, ref = scene
    assert b"Comment added." in say(rita, ref, "Anyone driving from Bristol?").data
    assert scalar(app, "SELECT count(*) FROM gig_comments") == 1
    page = sam.get("/posts/%s" % ref).get_data(as_text=True)
    assert "Talk about this gig" in page and "Anyone driving from Bristol?" in page and "Rita Test" in page
    row = scalar(app, "SELECT title FROM gig_comments")
    assert row == "Kings Test"                                      # the gig snapshot travels with the comment


def test_empty_overlong_and_unknown_gig(scene):
    app, kings, rita, sam, boss, ref = scene
    assert b"Write something first." in say(rita, ref, "   ").data
    assert b"too long" in say(rita, ref, LONG).data
    assert scalar(app, "SELECT count(*) FROM gig_comments") == 0
    assert say(rita, "99999", "hello").status_code == 404
    assert say(rita, "nope", "hello", source="ticketmaster").status_code == 404
    assert say(rita, ref, "hello", source="bogus").status_code == 404


def test_comments_need_a_signed_in_member_and_a_csrf_token(scene):
    app, kings, rita, sam, boss, ref = scene
    anon = app.test_client()
    assert anon.post("/gigs/comment", data={"source": "community", "ref": ref, "body": "hi"}).status_code == 400
    r = anon.post("/gigs/comment", data={"_csrf": token_of(anon, "/users/signin"), "source": "community", "ref": ref, "body": "hi"})
    assert r.status_code == 302 and "/users/signin" in r.headers["Location"]
    assert rita.post("/gigs/comment", data={"source": "community", "ref": ref, "body": "hi"}).status_code == 400
    assert scalar(app, "SELECT count(*) FROM gig_comments") == 0
    assert anon.get("/api/v1/gigs/community/%s/comments" % ref).status_code == 401


def test_unconfirmed_email_cannot_write_when_verification_is_required(tmp_path):
    app = make_app(tmp_path, REQUIRE_EMAIL_VERIFICATION=True)
    kings = register(app, "kings", kind="band")
    sql(app, "UPDATE users SET email_verified = 1")
    announce(kings)
    ref = str(scalar(app, "SELECT id FROM posts"))
    rita = register(app, "rita")
    sql(app, "UPDATE users SET email_verified = 0 WHERE username = 'rita'")
    say(rita, ref, "hello")
    assert scalar(app, "SELECT count(*) FROM gig_comments") == 0
    assert api_say(rita, ref, "hello").get_json()["code"] == "email_not_confirmed"


def test_the_same_text_is_escaped_not_run(scene):
    app, kings, rita, sam, boss, ref = scene
    say(rita, ref, "<script>alert(1)</script> <b>hi</b>")
    page = sam.get("/posts/%s" % ref).get_data(as_text=True)
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;alert(1)&lt;/script&gt;" in page
    assert "<b>hi</b>" not in page


def test_rate_limit(tmp_path):
    from helpers import limits
    app = make_app(tmp_path, RATE_LIMITS=limits(gigtalk_user=(2, 3600)))
    kings = register(app, "kings", kind="band")
    announce(kings)
    ref = str(scalar(app, "SELECT id FROM posts"))
    rita = register(app, "rita")
    for text in ("one", "two"):
        assert api_say(rita, ref, text).status_code == 201
    assert api_say(rita, ref, "three").status_code == 429
    assert texts(rita, ref) == ["one", "two"]


def test_a_gig_long_over_is_closed_but_a_recent_one_still_welcomes_comments(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "old", "Old show", days=-9)
    add_external(app, "recent", "Recent show", days=-3)
    rita = register(app, "rita")
    assert api_say(rita, "old", "late", "ticketmaster").get_json()["code"] == "closed"
    assert api_say(rita, "recent", "great night", "ticketmaster").status_code == 201


# ------------------------------------------------------------------ deleting and reporting
def test_authors_and_admins_delete_others_cannot(scene):
    app, kings, rita, sam, boss, ref = scene
    say(rita, ref, "mine")
    cid = scalar(app, "SELECT id FROM gig_comments")
    assert sam.post("/gigs/comments/%d/delete" % cid, data={"_csrf": token_of(sam, "/feed")}).status_code == 403
    assert scalar(app, "SELECT count(*) FROM gig_comments") == 1
    assert b"Comment deleted." in rita.post("/gigs/comments/%d/delete" % cid, data={"_csrf": token_of(rita, "/feed"), "next": "/gigs"}, follow_redirects=True).data
    assert scalar(app, "SELECT count(*) FROM gig_comments") == 0
    assert rita.post("/gigs/comments/%d/delete" % cid, data={"_csrf": token_of(rita, "/feed")}).status_code == 404
    say(sam, ref, "rude")
    cid = scalar(app, "SELECT id FROM gig_comments")
    post_form(boss, "/gigs/comments/%d/delete" % cid)
    assert scalar(app, "SELECT count(*) FROM gig_comments") == 0
    assert scalar(app, "SELECT count(*) FROM mod_log WHERE action = 'remove_gig_comment'") == 1


def test_delete_buttons_show_only_where_allowed_and_report_link_on_others(scene):
    app, kings, rita, sam, boss, ref = scene
    say(rita, ref, "from rita")
    cid = scalar(app, "SELECT id FROM gig_comments")
    mine = rita.get("/posts/%s" % ref).get_data(as_text=True)
    other = sam.get("/posts/%s" % ref).get_data(as_text=True)
    admin = boss.get("/posts/%s" % ref).get_data(as_text=True)
    assert "/gigs/comments/%d/delete" % cid in mine and "/report/gigcomment/%d" % cid not in mine
    assert "/gigs/comments/%d/delete" % cid not in other and "/report/gigcomment/%d" % cid in other
    assert "/gigs/comments/%d/delete" % cid in admin


def test_report_then_admin_removes_the_comment_and_can_ban(scene):
    app, kings, rita, sam, boss, ref = scene
    say(rita, ref, "buy cheap pills")
    cid = scalar(app, "SELECT id FROM gig_comments")
    assert b"cannot report yourself" in rita.get("/report/gigcomment/%d" % cid, follow_redirects=True).data
    assert b"buy cheap pills" in sam.get("/report/gigcomment/%d" % cid).data
    assert b"Thank you" in sam.post("/report/gigcomment/%d" % cid, data={"reason": "spam", "_csrf": token_of(sam, "/report/gigcomment/%d" % cid)},
                                    follow_redirects=True).data
    rid = scalar(app, "SELECT id FROM reports")
    assert scalar(app, "SELECT target_user_id FROM reports") == user_id(app, "rita")
    assert b"buy cheap pills" in boss.get("/admin/reports").data
    post_form(boss, "/admin/reports/%d/resolve" % rid, page="/admin/reports", action="remove_ban")
    assert scalar(app, "SELECT count(*) FROM gig_comments") == 0
    assert scalar(app, "SELECT status FROM users WHERE username = 'rita'") == "banned"
    assert sam.get("/report/gigcomment/%d" % cid).status_code == 404       # gone: nothing to report any more


# ------------------------------------------------------------------ who sees what
def test_blocked_and_suspended_authors_are_hidden(scene):
    app, kings, rita, sam, boss, ref = scene
    say(rita, ref, "from rita")
    say(sam, ref, "from sam")
    assert texts(kings, ref) == ["from rita", "from sam"]
    post_form(kings, "/block/%d" % user_id(app, "rita"))
    assert texts(kings, ref) == ["from sam"]
    # the band blocked rita, so the band's gig is gone from rita's view, thread included
    assert rita.get("/api/v1/gigs/community/%s/comments" % ref).get_json()["code"] == "gig_not_found"
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'sam'")
    assert texts(kings, ref) == []


def test_blocking_works_both_ways(scene):
    app, kings, rita, sam, boss, ref = scene
    say(rita, ref, "from rita")
    say(sam, ref, "from sam")
    post_form(rita, "/block/%d" % user_id(app, "sam"))
    assert texts(rita, ref) == ["from rita"]          # I do not see someone I blocked
    assert texts(sam, ref) == ["from sam"]            # and they do not see me


def test_the_same_concert_on_two_listings_shares_one_thread(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "tm1", "Kings Live", "ticketmaster")
    add_external(app, "sk1", "Kings Live", "skiddle")
    rita, sam = register(app, "rita"), register(app, "sam")
    api_say(rita, "tm1", "Meet at the bar", "ticketmaster")
    assert texts(sam, "sk1", "skiddle") == ["Meet at the bar"]
    api_say(sam, "sk1", "I will be there", "skiddle")
    assert texts(rita, "tm1", "ticketmaster") == ["Meet at the bar", "I will be there"]


def test_another_concert_has_its_own_thread(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "a", "Kings Live", days=4)
    add_external(app, "b", "Totally Different Band", days=6)
    rita = register(app, "rita")
    api_say(rita, "a", "for a only", "ticketmaster")
    assert texts(rita, "b", "ticketmaster") == []


def test_the_page_of_an_imported_gig_shows_the_thread(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "tm1", "Kings Live")
    rita = register(app, "rita")
    api_say(rita, "tm1", "see you there", "ticketmaster")
    event_id = scalar(app, "SELECT id FROM external_events")
    page = rita.get("/events/%d" % event_id).get_data(as_text=True)
    assert "Talk about this gig" in page and "see you there" in page


def test_minors_and_adults_have_separate_threads(tmp_path):
    app = make_app(tmp_path, MIN_AGE=16)
    teen_born = (date.today() - timedelta(days=365 * 17)).isoformat()
    band = register(app, "kings", kind="band", birth_date="1980-01-01")
    announce(band)
    ref = str(scalar(app, "SELECT id FROM posts"))
    adult, teen = register(app, "alex", birth_date="1990-01-01"), register(app, "tom", birth_date=teen_born)
    api_say(adult, ref, "adults only chat")
    api_say(teen, ref, "teens only chat")
    assert texts(adult, ref) == ["adults only chat"] and texts(teen, ref) == ["teens only chat"]


# ------------------------------------------------------------------ housekeeping and the member's data
def test_old_threads_are_cleaned_up_with_the_gigs(tmp_path):
    app = make_app(tmp_path)
    add_external(app, "old", "Old show", days=-3)
    add_external(app, "new", "New show", days=5)
    rita = register(app, "rita")
    api_say(rita, "old", "gone soon", "ticketmaster")
    api_say(rita, "new", "stays", "ticketmaster")
    from rockconnect import social
    with app.app_context():
        social.prune_attendances()
    assert scalar(app, "SELECT count(*) FROM gig_comments") == 1 and scalar(app, "SELECT body FROM gig_comments") == "stays"


def test_export_and_erasure_include_gig_comments(scene):
    app, kings, rita, sam, boss, ref = scene
    say(rita, ref, "my words")
    say(sam, ref, "other words")
    data = json.loads(zipfile.ZipFile(io.BytesIO(rita.get("/account/export").data)).read("data.json"))
    assert [c["body"] for c in data["gig_comments"]] == ["my words"]
    rita.post("/account/delete", data={"_csrf": token_of(rita, "/account/"), "password": "S3cret!pw", "confirm": "DELETE"})
    assert scalar(app, "SELECT count(*) FROM gig_comments WHERE body = 'my words'") == 0
    assert scalar(app, "SELECT count(*) FROM gig_comments WHERE body = 'other words'") == 1


# ------------------------------------------------------------------ the API
def test_api_post_list_and_delete(scene):
    app, kings, rita, sam, boss, ref = scene
    created = api_say(rita, ref, "hello api")
    assert created.status_code == 201 and created.get_json()["id"] > 0
    got = sam.get("/api/v1/gigs/community/%s/comments" % ref).get_json()
    assert got["total"] == 1 and got["comments"][0]["body"] == "hello api" and got["comments"][0]["mine"] is False
    assert got["comments"][0]["author"]["username"] == "rita"
    assert rita.get("/api/v1/gigs/community/%s/comments" % ref).get_json()["comments"][0]["mine"] is True
    cid = got["comments"][0]["id"]
    assert sam.post("/api/v1/gig-comments/%d/delete" % cid, data={"_csrf": token_of(sam, "/feed")}).get_json()["code"] == "forbidden"
    assert rita.post("/api/v1/gig-comments/%d/delete" % cid, data={"_csrf": token_of(rita, "/feed")}).get_json() == {"deleted": True}
    assert rita.post("/api/v1/gig-comments/%d/delete" % cid, data={"_csrf": token_of(rita, "/feed")}).status_code == 404


def test_api_errors_and_json_body(scene):
    app, kings, rita, sam, boss, ref = scene
    assert api_say(rita, ref, "").get_json()["code"] == "empty"
    assert api_say(rita, ref, LONG).get_json()["code"] == "too_long"
    assert rita.get("/api/v1/gigs/community/99999/comments").get_json()["code"] == "gig_not_found"


def test_only_the_last_hundred_are_listed(scene):
    app, kings, rita, sam, boss, ref = scene
    with app.app_context():
        from rockconnect.db import commit, execute
        row = execute("SELECT event_at FROM posts WHERE id = :i", i=int(ref)).scalar()
        for n in range(105):
            execute("INSERT INTO gig_comments (user_id, source, event_ref, title, event_at, body, created_at) VALUES"
                    " (:u, 'community', :r, 'Kings Test', :at, :b, '2026-01-01 00:00:00')",
                    u=user_id(app, "rita"), r=ref, at=row, b="c%03d" % n)
        commit()
        assert gigtalk.SHOWN == 100
    found = texts(sam, ref)
    assert len(found) == 100 and found[0] == "c005" and found[-1] == "c104"
