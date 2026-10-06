# File: test_sessions.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Session management: register -> sign in -> timed session -> expiry -> sign in again."""
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from rockconnect import create_app
from rockconnect.db import execute
from rockconnect.util import TIME_FORMAT
from test_app import csrf, signin, signup

FETCH = {"X-Requested-With": "fetch"}


def make_app(tmp_path, minutes=60):
    return create_app({"TESTING": True, "SESSION_LIFETIME_MINUTES": minutes,
                       "NEW_MEMBER_HOLD_POSTS": 0, "NEW_MEMBER_HOLD_HOURS": 0, "DEFAULT_WORD_LISTS": "",
                       "INSTANCE_PATH": str(tmp_path / "instance"),
                       "DATABASE_URL": "sqlite:///" + str(tmp_path / "s.sqlite"),
                       "UPLOAD_DIR": str(tmp_path / "uploads")})


def session_rows(app):
    with app.app_context():
        return execute("SELECT * FROM sessions").mappings().fetchall()


def expire_all_sessions(app):
    """Pretend the clock moved on: every session ended a minute ago."""
    ended = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime(TIME_FORMAT)
    with app.app_context():
        execute("UPDATE sessions SET expires_at = :e", e=ended)
        from rockconnect.db import commit
        commit()


def test_registering_does_not_start_a_session(client):
    r = signup(client)
    assert b"please sign in" in r.data
    assert session_rows(client.application) == []
    assert client.get("/feed").status_code == 302          # still logged out


def test_sign_in_creates_a_session_that_lasts_the_configured_time(tmp_path):
    app = make_app(tmp_path, minutes=5)
    c = app.test_client()
    signup(c)
    before = datetime.now(timezone.utc).replace(microsecond=0)
    signin(c)
    (row,) = session_rows(app)
    ends = datetime.strptime(row["expires_at"], TIME_FORMAT).replace(tzinfo=timezone.utc)
    assert timedelta(minutes=4, seconds=50) < ends - before <= timedelta(minutes=5, seconds=2)
    assert len(row["token_hash"]) == 64                     # only a hash is stored, never the token
    assert c.get("/feed").status_code == 200


def test_token_in_cookie_is_not_stored_in_the_database(tmp_path):
    app = make_app(tmp_path)
    c = app.test_client()
    signup(c)
    signin(c)
    with c.session_transaction() as s:
        token = s["sid"]
    assert token not in [r["token_hash"] for r in session_rows(app)]


def test_expired_session_sends_user_back_to_sign_in_and_they_can_continue(client):
    signup(client)
    signin(client)
    assert client.get("/feed").status_code == 200
    expire_all_sessions(client.application)

    r = client.get("/feed", follow_redirects=False)
    assert r.status_code == 302 and "/users/signin" in r.headers["Location"]
    assert "next=%2Ffeed" in r.headers["Location"] or "next=/feed" in r.headers["Location"]
    page = client.get(r.headers["Location"]).data           # the sign-in page explains what happened
    assert b"session has expired" in page
    assert client.get("/feed").status_code == 302           # still logged out

    # sign in again and land back on the page they were on
    back = client.post("/users/signin", data={"username": "bacco", "password": "S3cret!pw", "next": "/feed",
                                              "_csrf": csrf(client, "/users/signin")})
    assert back.status_code == 302 and back.headers["Location"].endswith("/feed")
    assert client.get("/feed").status_code == 200


def test_expiry_message_is_shown_once_not_on_every_page(client):
    signup(client)
    signin(client)
    expire_all_sessions(client.application)
    assert b"session has expired" in client.get("/users/signin").data
    assert b"session has expired" not in client.get("/users/signin").data


def test_background_requests_get_401_json_when_session_expired(client):
    signup(client)
    signin(client)
    start_resp = client.post("/conversations/start/1", data={"_csrf": csrf(client, "/feed")})  # self -> redirect only
    assert start_resp.status_code == 302
    expire_all_sessions(client.application)
    for url in ("/conversations/unread", "/conversations/1/messages?after=0"):
        r = client.get(url, headers=FETCH)
        assert r.status_code == 401 and r.json["error"] == "session_expired"
        assert r.json["login_url"].endswith("/users/signin")


def test_sign_out_kills_the_session_on_the_server_so_a_copied_cookie_is_useless(client):
    signup(client)
    signin(client)
    with client.session_transaction() as s:
        stolen = dict(s)                                    # a copy of the cookie contents
    client.post("/users/signout", data={"_csrf": csrf(client, "/feed")})
    assert session_rows(client.application) == []

    thief = client.application.test_client()
    with thief.session_transaction() as s:
        s.update(stolen)                                    # replay the old cookie
    assert thief.get("/feed").status_code == 302


def test_signing_in_again_replaces_the_old_session(client):
    signup(client)
    signin(client)
    signin(client)
    assert len(session_rows(client.application)) == 1


def test_two_browsers_have_independent_sessions(client):
    signup(client)
    signin(client)
    other = client.application.test_client()
    signin(other)
    assert len(session_rows(client.application)) == 2
    other.post("/users/signout", data={"_csrf": csrf(other, "/feed")})
    assert client.get("/feed").status_code == 200           # the first browser is unaffected
    assert other.get("/feed").status_code == 302


def test_menu_shows_no_countdown(client):
    signup(client)
    page = signin(client).get_data(as_text=True)
    assert "session-timer" not in page and "data-seconds-left" not in page
    assert "Your session lasts" not in page and "Signed in as bacco" in page


def set_expiry(app, delta):
    """Make the stored session end `delta` from now (so 'last renewed N days ago' can be simulated)."""
    when = (datetime.now(timezone.utc) + delta).strftime(TIME_FORMAT)
    with app.app_context():
        execute("UPDATE sessions SET expires_at = :e", e=when)
        from rockconnect.db import commit
        commit()


def expiry(app):
    (row,) = session_rows(app)
    return datetime.strptime(row["expires_at"], TIME_FORMAT).replace(tzinfo=timezone.utc)


def test_login_lasts_a_month_by_default(client):
    assert client.application.config["SESSION_LIFETIME_MINUTES"] == 60 * 24 * 30
    signup(client)
    signin(client)
    left = expiry(client.application) - datetime.now(timezone.utc)
    assert timedelta(days=29, hours=23) < left <= timedelta(days=30, seconds=5)


def test_coming_back_after_five_days_you_are_still_signed_in_and_the_session_slides_forward(client):
    signup(client)
    signin(client)
    set_expiry(client.application, timedelta(days=25))         # five days of the 30 have gone by
    assert client.get("/feed").status_code == 200              # no sign-in needed
    left = expiry(client.application) - datetime.now(timezone.utc)
    assert left > timedelta(days=29, hours=23)                 # the month starts again from this visit


def test_renewal_happens_at_most_once_a_day(client):
    signup(client)
    signin(client)
    set_expiry(client.application, timedelta(days=29, hours=22))     # renewed 2 hours ago
    before = expiry(client.application)
    for _ in range(3):
        client.get("/feed")
    assert expiry(client.application) == before                      # no write on every click
    set_expiry(client.application, timedelta(days=28))               # renewed 2 days ago
    client.get("/feed")
    assert expiry(client.application) > datetime.now(timezone.utc) + timedelta(days=29)


def test_after_a_long_absence_you_do_have_to_sign_in_again(client):
    signup(client)
    signin(client)
    set_expiry(client.application, timedelta(days=-1))               # away for over 30 days
    r = client.get("/feed")
    assert r.status_code == 302 and "/users/signin" in r.headers["Location"]
    assert b"session has expired" in client.get("/users/signin").data


def test_the_cookie_is_kept_for_the_whole_window_and_refreshed_on_every_visit(client):
    signup(client)
    signin(client)
    r = client.get("/feed")
    cookie = r.headers.get("Set-Cookie", "")
    assert "Expires=" in cookie                                       # re-issued, so its end date slides too
    ends = parsedate_to_datetime(re.search(r"Expires=([^;]+)", cookie).group(1))
    assert ends - datetime.now(timezone.utc) > timedelta(days=30)


def test_sign_out_on_all_devices(client):
    signup(client)
    signin(client)
    phone = client.application.test_client()
    signin(phone)
    assert len(session_rows(client.application)) == 2
    r = client.post("/account/signout-everywhere", data={"_csrf": csrf(client, "/account/")}, follow_redirects=True)
    assert b"signed out on every device" in r.data
    assert session_rows(client.application) == []
    assert client.get("/feed").status_code == 302 and phone.get("/feed").status_code == 302


def test_short_lifetimes_still_renew_sensibly(tmp_path):
    app = make_app(tmp_path, minutes=8)                              # 8 minute sessions (a test setting)
    c = app.test_client()
    signup(c)
    signin(c)
    set_expiry(app, timedelta(minutes=5))                            # 3 of 8 minutes used: past the quarter mark
    c.get("/feed")
    assert expiry(app) > datetime.now(timezone.utc) + timedelta(minutes=7)


def test_expired_session_data_is_cleaned_up_at_next_sign_in(client):
    signup(client)
    signin(client)
    with client.application.app_context():
        execute("UPDATE sessions SET expires_at = '2000-01-01 00:00:00'")
        from rockconnect.db import commit
        commit()
    other = client.application.test_client()
    signin(other)
    assert len(session_rows(client.application)) == 1       # the long-dead row was removed


def test_next_parameter_cannot_redirect_off_site(client):
    signup(client)
    r = client.post("/users/signin", data={"username": "bacco", "password": "S3cret!pw", "next": "//evil.example",
                                           "_csrf": csrf(client, "/users/signin")})
    assert r.status_code == 302 and "evil.example" not in r.headers["Location"]


def test_wrong_password_does_not_create_a_session(client):
    signup(client)
    assert signin(client, password="nope").status_code == 401
    assert session_rows(client.application) == []


def test_session_cookie_flags(client):
    signup(client)
    r = client.post("/users/signin", data={"username": "bacco", "password": "S3cret!pw",
                                           "_csrf": csrf(client, "/users/signin")})
    cookie = r.headers.get("Set-Cookie", "")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie
    assert "Expires=" in cookie                       # a persistent cookie, not a browser-session one


def test_cookie_outlives_the_login_so_expiry_can_be_explained(client):
    # if the cookie died with the session the server could not say "your session expired"
    cfg = client.application.config
    assert cfg["PERMANENT_SESSION_LIFETIME"] > timedelta(minutes=cfg["SESSION_LIFETIME_MINUTES"])
