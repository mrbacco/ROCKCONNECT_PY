# File: test_app.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
import re

import pytest

from rockconnect import create_app

USER = dict(username="bacco", name="Andrea B", email="a@b.com",
            password="s3cret!", about="I like rock")


@pytest.fixture
def client(tmp_path):
    app = create_app({"TESTING": True,
                      "INSTANCE_PATH": str(tmp_path / "instance"),  # never the real instance folder
                      "DATABASE_URL": "sqlite:///" + str(tmp_path / "t.sqlite"),
                      "UPLOAD_DIR": str(tmp_path / "uploads")})
    return app.test_client()


def csrf(client, url="/users/add"):
    html = client.get(url).get_data(as_text=True)
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', html).group(1)


def signup(client, **over):
    data = {**USER, **over, "_csrf": csrf(client)}
    return client.post("/users/add", data=data, follow_redirects=True)


def signin(client, username="bacco", password="s3cret!"):
    return client.post("/users/signin", data={
        "username": username, "password": password, "_csrf": csrf(client, "/users/signin")},
        follow_redirects=True)


def test_pages_render(client):
    for url in ("/", "/home", "/users/add", "/users/signin"):
        assert client.get(url).status_code == 200


def test_signup_lists_user_and_hashes_password(client):
    assert b"Account created" in signup(client).data
    assert b"bacco" in client.get("/people").data
    with client.application.app_context():
        from rockconnect.db import execute
        pw = execute("SELECT password FROM users").fetchone()[0]
    assert pw.startswith("$2") and pw != USER["password"]


def test_signup_rejects_duplicates_and_missing_fields(client):
    signup(client)
    assert b"already registered" in signup(client).data
    assert b"missing: about" in signup(client, username="x", email="x@y.z", about="").data


def test_signin_good_and_bad_password(client):
    signup(client)
    assert b"Successfully logged in" in signin(client).data
    bad = signin(client.application.test_client(), password="wrong")
    assert bad.status_code == 401 and b"Invalid credentials" in bad.data


def test_profile_and_404(client):
    signup(client)
    assert b"I like rock" in client.get("/users_list/1").data
    assert client.get("/users_list/99").status_code == 404


def test_search(client):
    signup(client)
    assert b"bacco" in client.get("/people?q=bac").data
    assert b"No users found" in client.get("/people?q=zzz").data


def test_edit_requires_login_then_updates(client):
    assert client.get("/edit").status_code == 302
    signup(client)
    signin(client)
    r = client.post("/edit", data={"name": "New Name", "email": "a@b.com",
                                   "about": "changed", "_csrf": csrf(client, "/edit")},
                    follow_redirects=True)
    assert b"Profile updated" in r.data and b"changed" in r.data


def test_csrf_enforced(client):
    assert client.post("/users/add", data=USER).status_code == 400


def test_xss_escaped(client):
    signup(client, about="<script>alert(1)</script>")
    assert b"<script>alert(1)</script>" not in client.get("/users_list/1").data


# ---------------------------------------------------------------- conversations
def two_users(client):
    """bacco (client) and rita (second client with its own session), both signed in."""
    signup(client)
    signin(client)
    other = client.application.test_client()
    signup(other, username="rita", name="Rita R", email="r@b.com")
    signin(other, "rita")
    return client, other


def start(client, user_id):
    return client.post("/conversations/start/%d" % user_id,
                       data={"_csrf": csrf(client, "/feed")}, follow_redirects=True)


def send(client, conv_id, body):
    return client.post("/conversations/%d" % conv_id,
                       data={"body": body, "_csrf": csrf(client, "/conversations/%d" % conv_id)},
                       follow_redirects=True)


def test_conversations_require_login(client):
    assert client.get("/conversations/").status_code == 302
    assert client.post("/conversations/start/1", data={"_csrf": csrf(client)}).status_code == 302


def test_start_conversation_is_idempotent_and_both_see_messages(client):
    bacco, rita = two_users(client)
    assert b'id="chat"' in start(bacco, 2).data  # the open-chat pane is rendered
    start(rita, 1)  # rita opening it from her side must reuse the same conversation
    with client.application.app_context():
        from rockconnect.db import execute
        assert execute("SELECT count(*) FROM conversations").scalar() == 1

    assert b"hello rita" in send(bacco, 1, "hello rita").data
    assert b"hi bacco" in send(rita, 1, "hi bacco").data
    page = bacco.get("/conversations/1").data
    assert b"hello rita" in page and b"hi bacco" in page
    assert b"hi bacco" in bacco.get("/conversations/").data  # inbox preview


def test_conversation_is_private(client):
    bacco, rita = two_users(client)
    start(bacco, 2)
    third = client.application.test_client()
    signup(third, username="eve", name="Eve", email="e@b.com")
    signin(third, "eve")
    assert third.get("/conversations/1").status_code == 404
    assert third.post("/conversations/1", data={"body": "x", "_csrf": csrf(third, "/feed")}).status_code == 404


def test_cannot_message_self_or_unknown_user(client):
    bacco, _ = two_users(client)
    assert b"cannot message yourself" in start(bacco, 1).data
    assert start(bacco, 99).status_code == 404


def test_message_validation(client):
    bacco, _ = two_users(client)
    start(bacco, 2)
    assert b"Write something first" in send(bacco, 1, "   ").data
    assert b"too long" in send(bacco, 1, "x" * 2001).data


def test_message_html_escaped(client):
    bacco, _ = two_users(client)
    start(bacco, 2)
    assert b"<script>alert(1)</script>" not in send(bacco, 1, "<script>alert(1)</script>").data


def test_database_url_selects_backend(tmp_path):
    from rockconnect.db import get_engine
    url = "sqlite:///" + str(tmp_path / "other.sqlite")
    app = create_app({"TESTING": True, "DATABASE_URL": url,
                      "INSTANCE_PATH": str(tmp_path / "instance")})
    with app.app_context():
        assert get_engine().url.get_backend_name() == "sqlite"
        assert get_engine().url.database.endswith("other.sqlite")
