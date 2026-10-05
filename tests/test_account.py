# File: test_account.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Account page: change password, download my data, delete my account, consent at sign-up, legal pages."""
import io
import json
import os
import zipfile

import pytest

from helpers import (PASSWORD, limits, make_app, post_form, register, scalar, signin, signup, sql,
                     token_of, user_id)

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def new_post(client, body, image=None):
    data = {"_csrf": token_of(client, "/feed"), "body": body}
    if image:
        data["image"] = (io.BytesIO(image), "x.png")
    return client.post("/posts", data=data, content_type="multipart/form-data")


@pytest.fixture
def pair(tmp_path):
    app = make_app(tmp_path)
    return app, register(app, "bacco"), register(app, "rita")


# ------------------------------------------------------------------ consent and legal pages
def test_signup_needs_consent_and_records_it(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    refused = signup(client, accept="")
    assert b"accept the terms" in refused.data and scalar(app, "SELECT count(*) FROM users") == 0
    assert b"account is ready" in signup(client).data
    assert scalar(app, "SELECT terms_accepted_at FROM users")                      # proof of consent kept
    assert b"at least 16 years old" in client.get("/users/add").data


def test_legal_pages_are_public_and_use_the_operator_details(tmp_path):
    app = make_app(tmp_path, OPERATOR_NAME="Loud Ltd", OPERATOR_ADDRESS="1 Main St, Cork",
                   CONTACT_EMAIL="legal@loud.example", MIN_AGE=18)
    client = app.test_client()
    for url, needle in (("/terms", "Terms of service"), ("/privacy", "Privacy policy"), ("/cookies", "strictly necessary")):
        page = client.get(url)
        assert page.status_code == 200 and needle.encode() in page.data
        assert b"Loud Ltd" in page.data or url == "/cookies"
        assert b"legal@loud.example" in page.data
    assert b"1 Main St, Cork" in client.get("/privacy").data
    assert b"at least 18 years old" in client.get("/terms").data
    # every public page links to them
    home = client.get("/").get_data(as_text=True)
    assert "/terms" in home and "/privacy" in home and "/cookies" in home


def test_privacy_policy_matches_what_the_app_does(tmp_path):
    """The policy promises no third parties: the pages must really load nothing external."""
    app = make_app(tmp_path)
    client = register(app)
    for url in ("/", "/feed", "/gigs", "/people", "/account/", "/edit"):
        html = client.get(url).get_data(as_text=True)
        assert "http://" not in html.replace("http://www.w3.org", "") and "https://cdn" not in html


# ------------------------------------------------------------------ change password
def change(client, current=PASSWORD, new="N3w-Password!", again=None):
    return client.post("/account/password", data={
        "current": current, "password": new, "password2": new if again is None else again,
        "_csrf": token_of(client, "/account/")}, follow_redirects=True)


def test_change_password(pair):
    app, bacco, rita = pair
    other_device = app.test_client()
    signin(other_device, "bacco")
    assert b"not right" in change(bacco, current="wrong-password").data
    assert b"do not match" in change(bacco, again="different").data
    assert b"too easy" in change(bacco, new="password").data
    assert b"Password changed" in change(bacco).data
    assert bacco.get("/feed").status_code == 200                       # this device stays signed in
    assert other_device.get("/feed").status_code == 302                # the other one is signed out
    assert signin(app.test_client(), "bacco", PASSWORD).status_code == 401
    assert b"Signed in as bacco" in signin(app.test_client(), "bacco", "N3w-Password!").data


# ------------------------------------------------------------------ my data
def test_export_contains_my_data_and_nothing_else(pair):
    app, bacco, rita = pair
    new_post(bacco, "my gig report", image=PNG)
    new_post(rita, "rita's secret post")
    post_form(bacco, "/posts/2/comments", body="nice one")
    post_form(rita, "/posts/1/like")
    post_form(bacco, "/posts/2/like")
    post_form(bacco, "/conversations/start/%d" % user_id(app, "rita"))
    post_form(bacco, "/conversations/1", page="/conversations/1", body="hi rita, private")
    post_form(rita, "/conversations/1", page="/conversations/1", body="rita's private reply")
    post_form(bacco, "/block/%d" % user_id(app, "rita"))

    r = bacco.get("/account/export")
    assert r.status_code == 200 and r.mimetype == "application/zip"
    assert "attachment" in r.headers["Content-Disposition"] and "bacco-data-export.zip" in r.headers["Content-Disposition"]
    archive = zipfile.ZipFile(io.BytesIO(r.data))
    data = json.loads(archive.read("data.json"))

    assert data["profile"]["username"] == "bacco" and data["profile"]["email"] == "bacco@example.com"
    assert [p["body"] for p in data["posts"]] == ["my gig report"]
    assert data["comments"][0]["body"] == "nice one" and len(data["likes"]) == 1
    assert [m["body"] for m in data["messages_sent"]] == ["hi rita, private"]
    assert data["messages_sent"][0]["conversation_with"] == "rita"
    assert data["blocked_members"] == ["rita"]
    photo = data["posts"][0]["photo"]
    assert archive.read(photo) == PNG                                       # the picture itself is in the zip
    everything = archive.read("data.json").decode()
    assert "$2b$" not in everything and "password" not in everything.lower()     # no password hash
    assert "rita's secret post" not in everything and "rita's private reply" not in everything


def test_export_needs_sign_in_and_is_rate_limited(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(export_user=(1, 3600)))
    assert app.test_client().get("/account/export").status_code == 302
    client = register(app)
    assert client.get("/account/export").status_code == 200
    assert client.get("/account/export").status_code == 429


def test_export_survives_a_missing_photo(pair):
    app, bacco, rita = pair
    new_post(bacco, "with picture", image=PNG)
    os.remove(os.path.join(app.config["UPLOAD_DIR"], os.listdir(app.config["UPLOAD_DIR"])[0]))
    assert bacco.get("/account/export").status_code == 200


# ------------------------------------------------------------------ deleting my account
def delete(client, password=PASSWORD, confirm="DELETE"):
    return client.post("/account/delete", data={"password": password, "confirm": confirm,
                                                "_csrf": token_of(client, "/account/")}, follow_redirects=True)


def test_delete_needs_password_and_confirmation(pair):
    app, bacco, rita = pair
    assert b"type your password" in delete(bacco, password="wrong").data
    assert b"type your password" in delete(bacco, confirm="nope").data
    assert scalar(app, "SELECT count(*) FROM users WHERE username = 'bacco'") == 1


def test_delete_erases_everything_of_mine_and_only_mine(pair):
    app, bacco, rita = pair
    new_post(bacco, "bacco post with photo", image=PNG)
    new_post(rita, "rita post")
    photo_dir = app.config["UPLOAD_DIR"]
    assert len(os.listdir(photo_dir)) == 1
    post_form(rita, "/posts/1/comments", body="rita comments on bacco")      # on my post
    post_form(bacco, "/posts/2/comments", body="bacco comments on rita")     # on her post
    post_form(rita, "/posts/1/like"), post_form(bacco, "/posts/2/like")
    post_form(bacco, "/conversations/start/%d" % user_id(app, "rita"))
    post_form(bacco, "/conversations/1", page="/conversations/1", body="private words")
    post_form(rita, "/block/%d" % user_id(app, "bacco"))
    bacco.post("/report/post/2", data={"reason": "spam", "_csrf": token_of(bacco, "/report/post/2")})
    rita.post("/report/post/1", data={"reason": "spam", "_csrf": token_of(rita, "/report/post/1")})

    assert b"Goodbye" in delete(bacco).data
    for table, where in (("users", "username = 'bacco'"), ("sessions", "1 = 1 AND user_id = 1"),
                         ("conversations", "1 = 1"), ("messages", "1 = 1"), ("conversation_reads", "1 = 1"),
                         ("blocks", "1 = 1"), ("email_tokens", "user_id = 1")):
        assert scalar(app, "SELECT count(*) FROM %s WHERE %s" % (table, where)) == 0, table
    assert [r for r in os.listdir(photo_dir)] == []                          # the photo file is gone
    # what belongs to rita stays (her post, but not bacco's comment/like on it)
    assert scalar(app, "SELECT count(*) FROM posts") == 1
    assert scalar(app, "SELECT count(*) FROM comments") == 0 and scalar(app, "SELECT count(*) FROM likes") == 0
    assert scalar(app, "SELECT count(*) FROM users WHERE username = 'rita'") == 1
    # a report about bacco's content keeps no copy of it and names nobody; the one he filed is gone
    left = scalar(app, "SELECT count(*) FROM reports")
    assert left == 1
    assert scalar(app, "SELECT snapshot FROM reports") == "" and scalar(app, "SELECT status FROM reports") == "closed"
    # he is signed out and cannot come back
    assert bacco.get("/feed").status_code == 302
    assert signin(app.test_client(), "bacco").status_code == 401
    assert b">bacco</a>" not in rita.get("/people").data                     # gone from the directory


def test_you_can_register_again_with_the_same_name_after_deleting(pair):
    app, bacco, rita = pair
    delete(bacco)
    assert b"account is ready" in signup(app.test_client(), "bacco").data


def test_account_page_shows_status(pair):
    app, bacco, rita = pair
    page = bacco.get("/account/").get_data(as_text=True)
    assert "bacco@example.com" in page and "not confirmed" in page
    sql(app, "UPDATE users SET email_verified = 1")
    assert "confirmed" in bacco.get("/account/").get_data(as_text=True)
    assert app.test_client().get("/account/").status_code == 302
