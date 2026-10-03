# File: test_social.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Feed (posts, photos, likes, comments), live chat JSON endpoints, unread badge, helpers."""
import io
import re

# reuse the helpers and the `client` fixture from test_app.py
from test_app import client, csrf, signin, signup, start, two_users  # noqa: F401



def register(client, **over):
    """Sign up AND sign in (registering alone no longer starts a session)."""
    signup(client, **over)
    return signin(client, over.get("username", "bacco"))


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
FETCH = {"X-Requested-With": "fetch"}


def post(client, body="", image=None, next_="/feed"):
    data = {"body": body, "next": next_, "_csrf": csrf(client, "/feed")}
    if image is not None:
        data["image"] = image
    return client.post("/posts", data=data, follow_redirects=True)


def act(client, url, **fields):
    return client.post(url, data={"next": "/feed", "_csrf": csrf(client, "/feed"), **fields},
                       follow_redirects=True)


def send_json(client, conv_id, body):
    return client.post("/conversations/%d" % conv_id, headers=FETCH,
                       data={"body": body, "_csrf": csrf(client, "/conversations/%d" % conv_id)})


# ------------------------------------------------------------------ feed
def test_register_then_sign_in_lands_on_feed(client):
    assert b"please sign in" in signup(client).data       # registering only creates the account
    page = signin(client).data                            # signing in starts the session
    assert b"Signed in as bacco" in page and b"listening to" in page


def test_feed_requires_login_but_profile_stays_public(client):
    assert client.get("/feed").status_code == 302
    assert client.post("/posts", data={"_csrf": csrf(client), "body": "x"}).status_code == 302
    register(client)
    post(client, "my first post")
    anon = client.application.test_client()
    page = anon.get("/users_list/1").data
    assert b"I like rock" in page and b"my first post" not in page and b"to see bacco" in page


def test_create_post_shows_on_feed_and_wall_and_escapes_html(client):
    register(client)
    assert b"Posted!" in post(client, "hello <b>world</b> & <script>x</script>").data
    for url in ("/feed", "/users_list/1"):
        html = client.get(url).data
        assert b"hello &lt;b&gt;world&lt;/b&gt;" in html and b"<script>x</script>" not in html


def test_empty_and_too_long_posts_rejected(client):
    register(client)
    assert b"Write something or add a photo" in post(client, "   ").data
    assert b"Post too long" in post(client, "x" * 5001).data


def test_post_visible_to_other_members(client):
    bacco, rita = two_users(client)
    post(bacco, "shared with everyone")
    assert b"shared with everyone" in rita.get("/feed").data


def test_photo_upload_is_validated_and_served_to_members_only(client):
    register(client)
    html = post(client, "pic", image=(io.BytesIO(PNG), "../../evil name.png")).data
    name = re.search(rb"/uploads/([0-9a-f]{32}\.png)", html).group(1).decode()
    assert b"evil" not in html                       # the uploaded file name is discarded
    assert client.get("/uploads/" + name).data == PNG
    assert client.application.test_client().get("/uploads/" + name).status_code == 302  # login needed
    assert client.get("/uploads/..%2F..%2Fsecret").status_code in (302, 404)

    assert b"Only JPG" in post(client, "", image=(io.BytesIO(b"not an image at all"), "a.png")).data
    big = PNG + b"0" * (5 * 1024 * 1024)
    assert b"too big" in post(client, "", image=(io.BytesIO(big), "big.png")).data
    # a photo-only post (no text) is fine
    assert b"Write something" not in post(client, "", image=(io.BytesIO(PNG), "p.png")).data


def test_like_toggle_and_comment(client):
    bacco, rita = two_users(client)
    post(bacco, "likeable")
    assert b"Rocked" in act(rita, "/posts/1/like").data
    assert b"&#129304; 1" in rita.get("/feed").data
    assert b"&#129304; 1" not in act(rita, "/posts/1/like").data  # second click = unlike

    assert b"great post" in act(rita, "/posts/1/comments", body="great post").data
    assert b"1 comment" in bacco.get("/feed").data
    assert b"Write a comment first" in act(rita, "/posts/1/comments", body="  ").data
    assert act(rita, "/posts/99/like").status_code == 404


def test_only_owner_can_delete_post_and_image_file_is_removed(client, tmp_path):
    bacco, rita = two_users(client)
    post(bacco, "mine", image=(io.BytesIO(PNG), "p.png"))
    assert len(list((tmp_path / "uploads").iterdir())) == 1
    assert rita.post("/posts/1/delete", data={"_csrf": csrf(rita, "/feed")}).status_code == 403
    assert b"mine" in bacco.get("/feed").data
    assert b"Post deleted" in act(bacco, "/posts/1/delete").data
    assert b"mine" not in bacco.get("/feed").data
    assert list((tmp_path / "uploads").iterdir()) == []


def test_comment_deleted_by_author_or_post_owner_only(client):
    bacco, rita = two_users(client)
    post(bacco, "p")
    act(rita, "/posts/1/comments", body="from rita")
    third = client.application.test_client()
    register(third, username="eve", name="Eve", email="e@b.com")
    assert third.post("/comments/1/delete", data={"_csrf": csrf(third, "/feed")}).status_code == 403
    act(bacco, "/comments/1/delete")  # the post's owner can moderate
    assert b"from rita" not in bacco.get("/feed").data


def test_open_redirect_blocked_after_post(client):
    register(client)
    r = client.post("/posts", data={"body": "x", "next": "//evil.example", "_csrf": csrf(client, "/feed")})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_feed_pagination(client):
    register(client)
    for i in range(23):
        post(client, "post-%02d" % i)
    page1 = client.get("/feed").data
    # 20 posts per page: 22 down to 03 on page 1, the remaining 02..00 on page 2
    assert b"post-22" in page1 and b"post-03" in page1 and b"post-02" not in page1
    nxt = re.search(rb"/feed\?before=(\d+)", page1).group(0).decode()
    page2 = client.get(nxt).data
    assert b"post-02" in page2 and b"post-00" in page2 and b"post-03" not in page2
    assert b"Older posts" not in page2


# ------------------------------------------------------------------ live chat + unread
def test_send_json_and_poll_new_messages(client):
    bacco, rita = two_users(client)
    start(bacco, 2)
    r = send_json(bacco, 1, "live hello")
    assert r.status_code == 201 and r.json["message"]["mine"] is True
    first_id = r.json["message"]["id"]

    polled = rita.get("/conversations/1/messages?after=0", headers=FETCH).json["messages"]
    assert [m["body"] for m in polled] == ["live hello"] and polled[0]["mine"] is False
    assert rita.get("/conversations/1/messages?after=%d" % first_id, headers=FETCH).json["messages"] == []

    assert send_json(bacco, 1, "   ").status_code == 400
    assert send_json(bacco, 1, "x" * 2001).json["error"].startswith("Message too long")


def test_poll_is_private(client):
    bacco, _ = two_users(client)
    start(bacco, 2)
    eve = client.application.test_client()
    register(eve, username="eve", name="Eve", email="e@b.com")
    assert eve.get("/conversations/1/messages?after=0").status_code == 404
    assert client.application.test_client().get("/conversations/1/messages").status_code == 302


def test_unread_badge_counts_then_clears_when_opened(client):
    bacco, rita = two_users(client)
    start(bacco, 2)
    assert rita.get("/conversations/unread").json == {"count": 0}
    send_json(bacco, 1, "ping 1")
    send_json(bacco, 1, "ping 2")
    assert rita.get("/conversations/unread").json == {"count": 1}   # counts conversations, like Facebook
    assert bacco.get("/conversations/unread").json == {"count": 0}  # your own messages are never unread
    assert b'id="unread-badge"' in rita.get("/feed").data
    rita.get("/conversations/1")                                    # opening the chat reads it
    assert rita.get("/conversations/unread").json == {"count": 0}
    send_json(bacco, 1, "ping 3")
    assert rita.get("/conversations/unread").json == {"count": 1}
    rita.get("/conversations/1/messages?after=0", headers=FETCH)    # the open chat polling reads it too
    assert rita.get("/conversations/unread").json == {"count": 0}


# ------------------------------------------------------------------ helpers
def test_timeago_safe_next_and_image_sniffing():
    from datetime import datetime, timedelta, timezone
    from rockconnect.util import TIME_FORMAT, detect_image_ext, safe_next, timeago

    def ago(**kw):
        return (datetime.now(timezone.utc) - timedelta(**kw)).strftime(TIME_FORMAT)

    assert timeago(ago(seconds=5)) == "just now"
    assert timeago(ago(minutes=5)) == "5 min ago"
    assert timeago(ago(hours=3)) == "3 h ago"
    assert timeago(ago(days=2)) == "2 d ago"
    assert timeago("garbage") == "garbage" and timeago(None) == ""

    assert safe_next("/feed?before=3", "/x") == "/feed?before=3"
    for bad in ("//evil.com", "https://evil.com", "javascript:alert(1)", "/\\evil.com", "", None):
        assert safe_next(bad, "/x") == "/x"

    assert detect_image_ext(b"\xff\xd8\xff\xe0") == ".jpg" and detect_image_ext(PNG[:16]) == ".png"
    assert detect_image_ext(b"GIF89a....") == ".gif"
    assert detect_image_ext(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ".webp"
    assert detect_image_ext(b"<svg onload=alert(1)>") is None and detect_image_ext(b"") is None


# ------------------------------------------------------------------ landing page / menu bar
def test_visitors_see_landing_with_only_sign_in_and_sign_up_and_no_menu(client):
    for url in ("/", "/home", "/users/signin", "/users/add"):
        page = client.get(url).get_data(as_text=True)
        assert "<nav" not in page and "navbar" not in page.replace("navbar-", "x"), url
    landing = client.get("/").get_data(as_text=True)
    assert 'href="/users/signin"' in landing and 'href="/users/add"' in landing
    assert "search" not in landing.lower() and "<form" not in landing  # nothing but the two buttons


def test_signin_and_signup_pages_link_to_each_other(client):
    assert b'href="/users/add"' in client.get("/users/signin").data
    assert b'href="/users/signin"' in client.get("/users/add").data


def test_members_get_the_menu_and_skip_the_landing_page(client):
    register(client)
    r = client.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/feed")
    assert client.get("/home").status_code == 302
    page = client.get("/feed").get_data(as_text=True)
    assert "<nav" in page and 'href="/people"' in page and 'href="/conversations/"' in page


def test_people_list_needs_login_and_signout_returns_to_menu_less_landing(client):
    assert client.get("/people").status_code == 302
    register(client)
    out = client.post("/users/signout", data={"_csrf": csrf(client, "/feed")}, follow_redirects=True)
    assert b"signed out" in out.data and b"<nav" not in out.data
    assert client.get("/feed").status_code == 302
