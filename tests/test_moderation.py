# File: test_moderation.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Reports, the admin console, suspending members, blocking and the audit log."""
import io

import pytest

from helpers import (FETCH, limits, make_admin, make_app, post_form, register, scalar, signin,
                     sql, token_of, user_id)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


@pytest.fixture
def scene(tmp_path):
    """A site with an admin (boss), a member who posts bad stuff (troll) and a normal member (rita)."""
    app = make_app(tmp_path)
    boss, troll, rita = register(app, "boss"), register(app, "troll"), register(app, "rita")
    make_admin(app, "boss")
    return app, boss, troll, rita


def new_post(client, body, image=None):
    data = {"_csrf": token_of(client, "/feed"), "body": body}
    if image:
        data["image"] = (io.BytesIO(image), "x.png")
    client.post("/posts", data=data, content_type="multipart/form-data")
    return None


def report(client, kind, target_id, reason="spam", details="", page="/feed"):
    return client.post("/report/%s/%d" % (kind, target_id),
                       data={"reason": reason, "details": details, "next": page,
                             "_csrf": token_of(client, "/report/%s/%d" % (kind, target_id))},
                       follow_redirects=True)


# ------------------------------------------------------------------ reporting
def test_report_a_post_creates_one_open_report(scene):
    app, boss, troll, rita = scene
    new_post(troll, "buy cheap followers now")
    assert b"Report post" in rita.get("/feed").data
    assert b"Report " in rita.get("/report/post/1").data
    assert b"Thank you" in report(rita, "post", 1, "spam", "it is an ad").data
    report(rita, "post", 1, "spam")                                # again: no second open report
    assert scalar(app, "SELECT count(*) FROM reports WHERE status = 'open'") == 1
    row = scalar(app, "SELECT snapshot FROM reports")
    assert row == "buy cheap followers now"


def test_report_validation(scene):
    app, boss, troll, rita = scene
    new_post(troll, "x1")
    new_post(rita, "my own post")
    assert b"cannot report yourself" in rita.get("/report/post/2", follow_redirects=True).data
    assert b"Choose a reason" in report(rita, "post", 1, reason="nonsense").data
    assert rita.get("/report/post/999").status_code == 404
    assert rita.get("/report/planet/1").status_code == 404
    anon = app.test_client()
    assert anon.get("/report/post/1").status_code == 302


def test_report_a_comment_and_a_profile(scene):
    app, boss, troll, rita = scene
    new_post(rita, "hello")
    post_form(troll, "/posts/1/comments", body="rude comment")
    assert b"Thank you" in report(rita, "comment", 1, "abuse").data
    assert b"Thank you" in report(rita, "user", user_id(app, "troll"), "other").data
    assert scalar(app, "SELECT count(*) FROM reports") == 2


def test_reporting_is_rate_limited(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(report_user=(1, 3600)))
    troll, rita = register(app, "troll"), register(app, "rita")
    new_post(troll, "a"), new_post(troll, "b")
    report(rita, "post", 1)
    assert rita.post("/report/post/2", data={"reason": "spam", "_csrf": token_of(rita, "/report/post/2")}).status_code == 429


# ------------------------------------------------------------------ the admin console
def test_only_admins_can_open_the_console(scene):
    app, boss, troll, rita = scene
    assert boss.get("/admin/").status_code == 200
    for url in ("/admin/", "/admin/reports", "/admin/users"):
        assert rita.get(url).status_code == 404                       # does not advertise itself
        assert app.test_client().get(url).status_code == 302          # signed out: sent to sign in
    assert b"admin" in boss.get("/feed").data and b">admin<" not in rita.get("/feed").data
    # a plain member cannot act either
    assert rita.post("/admin/users/2/ban", data={"_csrf": token_of(rita, "/feed")}).status_code == 404


def test_dashboard_numbers(scene):
    app, boss, troll, rita = scene
    new_post(troll, "x")
    report(rita, "post", 1)
    page = boss.get("/admin/").get_data(as_text=True)
    assert "Open reports" in page and "Members" in page


def test_dismiss_a_report(scene):
    app, boss, troll, rita = scene
    new_post(troll, "fine post")
    report(rita, "post", 1)
    assert b"fine post" in boss.get("/admin/reports").data
    boss.post("/admin/reports/1/resolve", data={"action": "dismiss", "_csrf": token_of(boss, "/admin/reports")})
    assert scalar(app, "SELECT resolution FROM reports") == "dismissed"
    assert scalar(app, "SELECT count(*) FROM posts") == 1                              # content stays
    assert b"No open reports" in boss.get("/admin/reports").data
    assert b"fine post" in boss.get("/admin/reports?status=closed").data


def test_remove_reported_post_deletes_its_photo_and_closes_duplicate_reports(scene):
    app, boss, troll, rita = scene
    new_post(troll, "spam with picture", image=PNG)
    photo = scalar(app, "SELECT image_filename FROM posts")
    import os
    path = os.path.join(app.config["UPLOAD_DIR"], photo)
    assert os.path.exists(path)
    report(rita, "post", 1)
    report(boss, "post", 1, "illegal")
    boss.post("/admin/reports/1/resolve", data={"action": "remove", "_csrf": token_of(boss, "/admin/reports")})
    assert scalar(app, "SELECT count(*) FROM posts") == 0 and not os.path.exists(path)
    assert scalar(app, "SELECT count(*) FROM reports WHERE status = 'open'") == 0           # both closed
    assert scalar(app, "SELECT action FROM mod_log WHERE action = 'remove_post'") == "remove_post"


def test_remove_reported_comment(scene):
    app, boss, troll, rita = scene
    new_post(rita, "hello")
    post_form(troll, "/posts/1/comments", body="nasty")
    report(rita, "comment", 1, "abuse")
    boss.post("/admin/reports/1/resolve", data={"action": "remove", "_csrf": token_of(boss, "/admin/reports")})
    assert scalar(app, "SELECT count(*) FROM comments") == 0


def test_remove_and_suspend_author(scene):
    app, boss, troll, rita = scene
    new_post(troll, "scam link")
    assert troll.get("/feed").status_code == 200
    report(rita, "post", 1, "spam")
    boss.post("/admin/reports/1/resolve", data={"action": "remove_ban", "_csrf": token_of(boss, "/admin/reports")})
    assert scalar(app, "SELECT status FROM users WHERE username = 'troll'") == "banned"
    assert scalar(app, "SELECT resolution FROM reports") == "removed_banned"
    # the suspended member is thrown out of the session they had open ...
    gone = troll.get("/feed", follow_redirects=True)
    assert b"session has expired" in gone.data
    # ... cannot sign in again, and shows up nowhere
    refused = signin(app.test_client(), "troll")
    assert refused.status_code == 403 and b"suspended" in refused.data
    assert b"troll" not in rita.get("/people").data
    assert rita.get("/users_list/%d" % user_id(app, "troll")).status_code == 404
    assert b"troll" in boss.get("/people").data                                    # admins still see them


def test_a_member_suspended_while_signed_in_is_signed_out_at_once(scene):
    app, boss, troll, rita = scene
    assert troll.get("/feed").status_code == 200
    sql(app, "UPDATE users SET status = 'banned' WHERE username = 'troll'")     # session row still exists
    gone = troll.get("/feed", follow_redirects=True)
    assert b"account has been suspended" in gone.data
    assert scalar(app, "SELECT count(*) FROM sessions WHERE user_id = :u", u=user_id(app, "troll")) == 0


def test_suspended_members_content_is_hidden_and_unsuspending_restores_it(scene):
    app, boss, troll, rita = scene
    new_post(troll, "visible while active")
    post_form(troll, "/posts/1/comments", body="troll comment")
    assert b"visible while active" in rita.get("/feed").data
    tid = user_id(app, "troll")
    boss.post("/admin/users/%d/ban" % tid, data={"reason": "spamming", "_csrf": token_of(boss, "/admin/users")})
    assert b"visible while active" not in rita.get("/feed").data
    assert b"spamming" in boss.get("/admin/users").data
    assert b"spamming" in signin(app.test_client(), "troll").data              # told why
    boss.post("/admin/users/%d/unban" % tid, data={"_csrf": token_of(boss, "/admin/users")})
    assert b"visible while active" in rita.get("/feed").data
    assert b"Signed in as troll" in signin(app.test_client(), "troll").data


def test_admins_cannot_be_banned_or_erased_and_not_by_themselves(scene):
    app, boss, troll, rita = scene
    bid = user_id(app, "boss")
    register(app, "boss2")
    make_admin(app, "boss2")
    for who, target in ((boss, bid), (boss, user_id(app, "boss2"))):
        who.post("/admin/users/%d/ban" % target, data={"_csrf": token_of(who, "/admin/users")})
        who.post("/admin/users/%d/delete" % target, data={"_csrf": token_of(who, "/admin/users")})
    assert scalar(app, "SELECT count(*) FROM users WHERE status = 'banned'") == 0
    assert scalar(app, "SELECT count(*) FROM users WHERE role = 'admin'") == 2


def test_admin_can_erase_a_member_on_request(scene):
    app, boss, troll, rita = scene
    new_post(troll, "to be erased", image=PNG)
    tid = user_id(app, "troll")
    boss.post("/admin/users/%d/delete" % tid, data={"_csrf": token_of(boss, "/admin/users")})
    assert scalar(app, "SELECT count(*) FROM users WHERE username = 'troll'") == 0
    assert scalar(app, "SELECT count(*) FROM posts") == 0
    assert scalar(app, "SELECT action FROM mod_log WHERE action = 'delete_user'") == "delete_user"


def test_admin_can_remove_any_post_and_comment_from_the_feed(scene):
    app, boss, troll, rita = scene
    new_post(troll, "moderate me")
    post_form(rita, "/posts/1/comments", body="rita says hi")
    assert b"rita says hi" in boss.get("/feed").data
    boss.post("/comments/1/delete", data={"_csrf": token_of(boss, "/feed")})
    boss.post("/posts/1/delete", data={"_csrf": token_of(boss, "/feed")})
    assert scalar(app, "SELECT count(*) FROM posts") == 0
    assert scalar(app, "SELECT count(*) FROM mod_log") == 2
    # but ordinary members still cannot delete other people's things
    new_post(troll, "safe from rita")
    post_id = scalar(app, "SELECT id FROM posts")
    assert rita.post("/posts/%d/delete" % post_id, data={"_csrf": token_of(rita, "/feed")}).status_code == 403


def test_make_admin_command(tmp_path):
    app = make_app(tmp_path)
    register(app, "rita")
    runner = app.test_cli_runner()
    assert "now admin" in runner.invoke(args=["make-admin", "rita"]).output
    assert scalar(app, "SELECT role FROM users") == "admin"
    assert "now member" in runner.invoke(args=["remove-admin", "rita"]).output
    assert runner.invoke(args=["make-admin", "ghost"]).exit_code != 0


# ------------------------------------------------------------------ blocking
def test_blocking_hides_posts_and_comments_both_ways(scene):
    app, boss, troll, rita = scene
    new_post(troll, "troll post")
    new_post(rita, "rita post")
    post_form(troll, "/posts/2/comments", body="troll comment on rita")
    tid = user_id(app, "troll")
    assert b"troll comment on rita" in rita.get("/feed").data
    assert b"Block" in rita.get("/users_list/%d" % tid).data
    rita.post("/block/%d" % tid, data={"_csrf": token_of(rita, "/feed")})
    mine, theirs = rita.get("/feed").get_data(as_text=True), troll.get("/feed").get_data(as_text=True)
    assert "troll post" not in mine and "troll comment on rita" not in mine
    assert "rita post" not in theirs                                         # hidden the other way too
    assert "Unblock" in rita.get("/users_list/%d" % tid).get_data(as_text=True)
    assert "troll post" in boss.get("/feed").get_data(as_text=True)          # others unaffected
    rita.post("/unblock/%d" % tid, data={"_csrf": token_of(rita, "/feed")})
    assert "troll post" in rita.get("/feed").get_data(as_text=True)


def test_blocking_stops_messages_in_both_directions(scene):
    app, boss, troll, rita = scene
    tid, rid = user_id(app, "troll"), user_id(app, "rita")
    post_form(rita, "/conversations/start/%d" % tid)                         # a chat exists
    post_form(troll, "/conversations/1/accept", page="/conversations/1")
    post_form(troll, "/conversations/1", page="/conversations/1", body="hello rita")
    assert scalar(app, "SELECT count(*) FROM messages") == 1
    rita.post("/block/%d" % tid, data={"_csrf": token_of(rita, "/feed")})
    # troll can no longer write (JSON for the chat script, a notice for plain forms)
    r = troll.post("/conversations/1", data={"body": "let me in", "_csrf": token_of(troll, "/conversations/1")},
                   headers=FETCH)
    assert r.status_code == 403 and "cannot message" in r.get_json()["error"]
    assert b"cannot message" in post_form(troll, "/conversations/1", page="/conversations/1", body="again").data
    # and rita cannot write to someone she blocked either
    assert b"cannot message" in post_form(rita, "/conversations/1", page="/conversations/1", body="hmm").data
    assert b"cannot send messages" in rita.get("/conversations/1").data
    # no new chats with them
    assert b"cannot message this person" in post_form(troll, "/conversations/start/%d" % rid).data
    assert scalar(app, "SELECT count(*) FROM messages") == 1


def test_cannot_message_a_suspended_member(scene):
    app, boss, troll, rita = scene
    boss.post("/admin/users/%d/ban" % user_id(app, "troll"), data={"_csrf": token_of(boss, "/admin/users")})
    assert b"cannot message this person" in post_form(rita, "/conversations/start/%d" % user_id(app, "troll")).data


def test_block_edge_cases(scene):
    app, boss, troll, rita = scene
    assert b"cannot block yourself" in post_form(rita, "/block/%d" % user_id(app, "rita")).data
    assert rita.post("/block/999", data={"_csrf": token_of(rita, "/feed")}).status_code == 404
    tid = user_id(app, "troll")
    post_form(rita, "/block/%d" % tid)
    post_form(rita, "/block/%d" % tid)                                       # twice is fine
    assert scalar(app, "SELECT count(*) FROM blocks") == 1
    assert b"@troll" in rita.get("/account/").data                             # listed on the account page
