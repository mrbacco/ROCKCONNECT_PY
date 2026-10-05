# File: test_email.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""E-mail confirmation, changing an address, forgotten password, password rules and the mail backends."""
import pytest

from helpers import (PASSWORD, limits, link_in, make_app, outbox, post_form, register,
                     scalar, signin, signup, sql, token_of)

VERIFY = {"REQUIRE_EMAIL_VERIFICATION": True}


def post_text(client, body="hello"):
    return client.post("/posts", data={"_csrf": token_of(client, "/feed"), "body": body}, follow_redirects=True)


# ------------------------------------------------------------------ confirming the address
def test_signup_sends_a_confirmation_link(tmp_path):
    app = make_app(tmp_path)
    signup(app.test_client())
    (mail,) = outbox(app)
    assert mail["to"] == "bacco@example.com" and "Confirm" in mail["subject"]
    assert "/users/verify/" in mail["body"]


def test_unconfirmed_members_cannot_post_comment_or_message_until_they_confirm(tmp_path):
    app = make_app(tmp_path, **VERIFY)
    client = register(app)
    assert b"confirm your email" in client.get("/feed").data           # banner on every page
    assert b"confirm your email address first" in post_text(client).data
    assert scalar(app, "SELECT count(*) FROM posts") == 0
    # chat: needs a second member
    register(app, "rita")
    other_id = scalar(app, "SELECT id FROM users WHERE username = 'rita'")
    assert b"confirm your email address first" in post_form(client, "/conversations/start/%d" % other_id).data
    # confirm: GET only shows a button, the POST does the work
    link = link_in(outbox(app)[0])
    page = client.get(link)
    assert page.status_code == 200 and b"Yes, confirm" in page.data
    assert scalar(app, "SELECT email_verified FROM users WHERE username = 'bacco'") == 0     # not used up by GET
    done = client.post(link, data={"_csrf": token_of(client, link)}, follow_redirects=True)
    assert b"is confirmed" in done.data
    assert scalar(app, "SELECT email_verified FROM users WHERE username = 'bacco'") == 1
    assert b"hello" in post_text(client).data
    assert b"confirm your email address (" not in client.get("/feed").data          # banner gone


def test_confirmation_link_works_once_and_expires(tmp_path):
    app = make_app(tmp_path)
    client = register(app)
    link = link_in(outbox(app)[0])
    client.post(link, data={"_csrf": token_of(client, "/feed")})
    again = client.get(link, follow_redirects=True)
    assert b"invalid or has expired" in again.data
    # an old link
    client.post("/users/verify", data={"_csrf": token_of(client, "/feed")})      # already confirmed: nothing sent
    sql(app, "UPDATE users SET email_verified = 0")
    client.post("/users/verify", data={"_csrf": token_of(client, "/feed")})
    fresh = link_in(outbox(app)[-1])
    sql(app, "UPDATE email_tokens SET expires_at = '2000-01-01 00:00:00'")
    assert b"invalid or has expired" in client.get(fresh, follow_redirects=True).data


def test_resend_confirmation_mail(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(verify_user=(1, 3600)))
    client = register(app)
    sent = len(outbox(app))
    sql(app, "UPDATE users SET email_verified = 0")
    assert b"new confirmation link" in client.post(
        "/users/verify", data={"_csrf": token_of(client, "/feed")}, follow_redirects=True).data
    assert len(outbox(app)) == sent + 1
    assert client.post("/users/verify", data={"_csrf": token_of(client, "/feed")}).status_code == 429


def test_garbage_token_is_refused(tmp_path):
    app = make_app(tmp_path)
    r = app.test_client().get("/users/verify/not-a-real-token", follow_redirects=True)
    assert b"invalid or has expired" in r.data


# ------------------------------------------------------------------ changing the address
def edit(client, **fields):
    data = {"name": "Bacco", "email": "bacco@example.com", "about": "hi", "kind": "fan", **fields,
            "_csrf": token_of(client, "/edit")}
    return client.post("/edit", data=data, follow_redirects=True)


def test_changing_email_waits_for_the_link_when_confirmation_is_required(tmp_path):
    app = make_app(tmp_path, **VERIFY)
    client = register(app)
    sql(app, "UPDATE users SET email_verified = 1")
    page = edit(client, email="new@example.com")
    assert b"your email address changes when you open it" in page.data
    assert scalar(app, "SELECT email FROM users") == "bacco@example.com"          # unchanged so far
    mail = outbox(app)[-1]
    assert mail["to"] == "new@example.com"
    link = link_in(mail)
    client.post(link, data={"_csrf": token_of(client, link)})
    assert scalar(app, "SELECT email FROM users") == "new@example.com"
    assert scalar(app, "SELECT email_verified FROM users") == 1


def test_changing_email_applies_at_once_when_confirmation_is_not_required(tmp_path):
    app = make_app(tmp_path)
    client = register(app)
    edit(client, email="new@example.com")
    assert scalar(app, "SELECT email FROM users") == "new@example.com"
    assert scalar(app, "SELECT email_verified FROM users") == 0                 # but marked unconfirmed


def test_cannot_take_someone_elses_address(tmp_path):
    app = make_app(tmp_path, **VERIFY)
    client = register(app)
    register(app, "rita")
    assert b"already registered" in edit(client, email="rita@example.com").data
    assert b"does not look like an email" in edit(client, email="nonsense").data


# ------------------------------------------------------------------ forgotten password
def request_reset(client, email):
    return client.post("/users/forgot", data={"email": email, "_csrf": token_of(client, "/users/forgot")},
                       follow_redirects=True)


def test_unknown_and_known_addresses_get_the_same_answer(tmp_path):
    app = make_app(tmp_path)
    signup(app.test_client())
    sent = len(outbox(app))
    client = app.test_client()
    unknown = request_reset(client, "nobody@example.com").get_data(as_text=True)
    assert len(outbox(app)) == sent                                              # nothing sent
    known = request_reset(client, "BACCO@example.com").get_data(as_text=True)    # case does not matter
    assert len(outbox(app)) == sent + 1 and "Reset your" in outbox(app)[-1]["subject"]
    needle = "If that address belongs to an account"
    assert needle in unknown and needle in known


def test_full_reset_flow_ends_every_session(tmp_path):
    app = make_app(tmp_path)
    laptop = register(app)
    phone = app.test_client()
    signin(phone)
    assert phone.get("/feed").status_code == 200
    anon = app.test_client()
    request_reset(anon, "bacco@example.com")
    link = link_in(outbox(app)[-1])
    assert b"Choose a new password" in anon.get(link).data

    weak = anon.post(link, data={"password": "password", "password2": "password", "_csrf": token_of(anon, link)})
    assert b"too easy to guess" in weak.data
    mismatch = anon.post(link, data={"password": "N3w-Password!", "password2": "other", "_csrf": token_of(anon, link)})
    assert b"do not match" in mismatch.data

    done = anon.post(link, data={"password": "N3w-Password!", "password2": "N3w-Password!",
                                 "_csrf": token_of(anon, link)}, follow_redirects=True)
    assert b"password was changed" in done.data
    assert laptop.get("/feed").status_code == 302 and phone.get("/feed").status_code == 302   # signed out
    assert signin(app.test_client(), password=PASSWORD).status_code == 401                    # old one is dead
    assert b"Signed in as bacco" in signin(app.test_client(), password="N3w-Password!").data
    assert b"invalid or has expired" in anon.get(link, follow_redirects=True).data            # single use
    assert scalar(app, "SELECT email_verified FROM users") == 1                               # proved the mailbox


def test_expired_reset_link_and_suspended_accounts(tmp_path):
    app = make_app(tmp_path)
    signup(app.test_client())
    anon = app.test_client()
    request_reset(anon, "bacco@example.com")
    link = link_in(outbox(app)[-1])
    sql(app, "UPDATE email_tokens SET expires_at = '2000-01-01 00:00:00'")
    assert b"invalid or has expired" in anon.get(link, follow_redirects=True).data
    sent = len(outbox(app))
    sql(app, "UPDATE users SET status = 'banned'")
    request_reset(anon, "bacco@example.com")
    assert len(outbox(app)) == sent                                              # no mail for suspended accounts


def test_reset_requests_are_limited_per_mailbox(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(forgot_email=(1, 3600)))
    client = app.test_client()
    assert request_reset(client, "a@example.com").status_code == 200
    assert client.post("/users/forgot", data={"email": "a@example.com",
                                              "_csrf": token_of(client, "/users/forgot")}).status_code == 429


# ------------------------------------------------------------------ password rules
@pytest.mark.parametrize("password,message", [
    ("short1!", b"at least 8"),
    ("password", b"too easy"),
    ("11111111", b"too easy"),
    ("x" * 73, b"too long"),
])
def test_password_rules_at_signup(tmp_path, password, message):
    app = make_app(tmp_path)
    r = signup(app.test_client(), password=password)
    assert message in r.data and scalar(app, "SELECT count(*) FROM users") == 0


def test_password_equal_to_username_or_email_is_refused(tmp_path):
    app = make_app(tmp_path)
    assert b"must not be your username" in signup(app.test_client(), "longusername", password="longusername").data
    assert b"must not be your username" in signup(app.test_client(), "rita", password="rita@example.com").data


def test_very_long_password_cannot_crash_sign_in(tmp_path):
    app = make_app(tmp_path)
    signup(app.test_client())
    r = signin(app.test_client(), password="x" * 500)
    assert r.status_code == 401


# ------------------------------------------------------------------ the mail backends
def test_console_backend_prints_instead_of_sending(tmp_path, capsys):
    app = make_app(tmp_path, MAIL_BACKEND="console")
    signup(app.test_client())
    out = capsys.readouterr().out
    assert "To: bacco@example.com" in out and "/users/verify/" in out


def test_smtp_backend_delivers_in_the_background(tmp_path, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout=None):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            sent.append("starttls")

        def login(self, user, password):
            sent.append(("login", user))

        def send_message(self, message):
            sent.append(("message", message["To"], message["Subject"], message["From"]))

    class Immediate:  # run the "background" thread right away so the test can look at the result
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("rockconnect.mail.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("rockconnect.mail.threading.Thread", Immediate)
    app = make_app(tmp_path, MAIL_BACKEND="smtp", SMTP_HOST="mail.example.com", SMTP_PORT=587,
                   SMTP_USER="robot", SMTP_PASSWORD="pw", SMTP_FROM="noreply@gighub.example")
    signup(app.test_client())
    assert ("connect", "mail.example.com", 587) in sent and "starttls" in sent and ("login", "robot") in sent
    (message,) = [m for m in sent if isinstance(m, tuple) and m[0] == "message"]
    assert message[1] == "bacco@example.com" and message[3] == "noreply@gighub.example"


def test_a_broken_mail_server_never_breaks_the_page(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection refused")

    class Immediate:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    monkeypatch.setattr("rockconnect.mail.smtplib.SMTP", boom)
    monkeypatch.setattr("rockconnect.mail.threading.Thread", Immediate)
    app = make_app(tmp_path, MAIL_BACKEND="smtp", SMTP_HOST="down.example.com")
    assert b"account is ready" in signup(app.test_client()).data
