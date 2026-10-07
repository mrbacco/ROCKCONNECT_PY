# File: test_signup_confirm.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Confirm-first sign-up: no account exists until the link mailed to the address is opened."""
import pytest

from helpers import PASSWORD, limits, link_in, make_app, outbox, register, scalar, signin, signup, sql, token_of

CONFIRM = {"REQUIRE_EMAIL_VERIFICATION": True, "MAIL_BACKEND": "memory"}


def users(app):
    return scalar(app, "SELECT count(*) FROM users")


def pending(app):
    return scalar(app, "SELECT count(*) FROM pending_signups")


def open_link(client, link):
    """The way a person does it: the page with the button, then the button."""
    page = client.get(link)
    done = client.post(link, data={"_csrf": token_of(client, link)}, follow_redirects=True)
    return page, done


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path, **CONFIRM)


# ------------------------------------------------------------------ the flow
def test_signing_up_creates_no_account_only_a_waiting_sign_up_and_a_mail(app):
    page = signup(app.test_client())
    assert users(app) == 0 and pending(app) == 1
    assert b"Check your email" in page.data and b"We sent a link to <strong>b****@example.com</strong>" in page.data
    (mail,) = outbox(app)
    assert mail["to"] == "bacco@example.com" and "Confirm your email to join" in mail["subject"]
    assert "/users/confirm/" in mail["body"] and "Your account is created when you do" in mail["body"]


def test_nobody_can_sign_in_before_the_link_is_opened(app):
    client = app.test_client()
    signup(client)
    refused = client.post("/users/signin", data={"username": "bacco", "password": PASSWORD, "_csrf": token_of(client, "/users/signin")})
    assert refused.status_code == 403 and b"not created yet" in refused.data and b"b****@example.com" in refused.data
    wrong = client.post("/users/signin", data={"username": "bacco", "password": "Wrong-Pass-1!", "_csrf": token_of(client, "/users/signin")})
    assert wrong.status_code == 401 and b"Invalid credentials" in wrong.data and b"not created" not in wrong.data
    assert client.get("/feed").status_code == 302


def test_opening_the_link_creates_a_confirmed_account_and_the_password_still_works(app):
    client = app.test_client()
    signup(client)
    link = link_in(outbox(app)[0])
    page = client.get(link)
    assert page.status_code == 200 and b"Yes, create my account" in page.data and b"bacco@example.com" in page.data
    assert users(app) == 0                                                          # looking at the button creates nothing
    done = client.post(link, data={"_csrf": token_of(client, link)}, follow_redirects=True)
    assert b"Your email is confirmed and your account is ready" in done.data
    assert users(app) == 1 and pending(app) == 0
    row = scalar(app, "SELECT email_verified || '|' || kind || '|' || birth_date || '|' || username FROM users")
    assert row == "1|fan|1990-05-15|bacco"
    assert b"Signed in as bacco" in signin(client, "bacco").data
    assert b"confirm your email address (" not in client.get("/feed").data           # no banner: it is already confirmed
    assert scalar(app, "SELECT terms_accepted_at FROM users") is not None


def test_the_link_works_once_in_any_browser_and_expires(app):
    signup(app.test_client())
    link = link_in(outbox(app)[0])
    other_browser = app.test_client()                                               # opened on the phone, signed up on the laptop
    open_link(other_browser, link)
    assert users(app) == 1
    again = other_browser.get(link, follow_redirects=True)
    assert b"invalid or has expired" in again.data and users(app) == 1
    # an old link
    signup(app.test_client(), "rita")
    fresh = link_in(outbox(app)[-1])
    sql(app, "UPDATE pending_signups SET expires_at = '2000-01-01 00:00:00'")
    page = app.test_client().get(fresh, follow_redirects=True)
    assert b"invalid or has expired" in page.data and users(app) == 1
    assert app.test_client().get("/users/confirm/not-a-real-token", follow_redirects=True).status_code == 200


def test_the_token_and_the_password_are_not_stored_in_the_clear(app):
    signup(app.test_client())
    link = link_in(outbox(app)[0])
    token = link.rsplit("/", 1)[1]
    stored = scalar(app, "SELECT token_hash || '|' || password FROM pending_signups")
    assert token not in stored and PASSWORD not in stored and stored.split("|")[1].startswith("$2")


# ------------------------------------------------------------------ resending and replacing
def test_resending_gives_a_new_link_and_the_old_one_stops_working(app):
    client = app.test_client()
    signup(client)
    old = link_in(outbox(app)[0])
    r = client.post("/users/confirm-resend", data={"email": "bacco@example.com", "_csrf": token_of(client, "/users/add")})
    assert r.status_code == 200 and b"If that address is waiting" in r.data and len(outbox(app)) == 2
    new = link_in(outbox(app)[1])
    assert new != old and pending(app) == 1
    assert b"invalid or has expired" in client.get(old, follow_redirects=True).data
    open_link(client, new)
    assert users(app) == 1


def test_resend_says_the_same_thing_for_unknown_addresses_and_sends_nothing(app):
    client = app.test_client()
    signup(client)
    r = client.post("/users/confirm-resend", data={"email": "nobody@example.com", "_csrf": token_of(client, "/users/add")})
    assert b"If that address is waiting" in r.data and len(outbox(app)) == 1
    r = client.post("/users/confirm-resend", data={"email": "not an address", "_csrf": token_of(client, "/users/add")})
    assert b"If that address is waiting" in r.data and len(outbox(app)) == 1


def test_resending_is_limited_per_address(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(pending_resend=(2, 3600)), **CONFIRM)
    client = app.test_client()
    signup(client)
    codes = [client.post("/users/confirm-resend", data={"email": "bacco@example.com", "_csrf": token_of(client, "/users/add")}).status_code
             for _ in range(3)]
    assert codes == [200, 200, 429] and len(outbox(app)) == 3


def test_signing_up_again_with_the_same_address_replaces_the_waiting_one(app):
    signup(app.test_client(), "first_try")
    signup(app.test_client(), "second_try", email="first_try@example.com")
    assert pending(app) == 1 and scalar(app, "SELECT username FROM pending_signups") == "second_try"
    open_link(app.test_client(), link_in(outbox(app)[-1]))
    assert scalar(app, "SELECT username FROM users") == "second_try"
    assert b"invalid or has expired" in app.test_client().get(link_in(outbox(app)[0]), follow_redirects=True).data


# ------------------------------------------------------------------ names taken, mail that cannot prove anything
def test_a_registered_username_or_email_is_refused_up_front(app):
    register(app, "rita")
    for over in ({"username": "rita"}, {"email": "rita@example.com"}):
        data = dict(over)
        page = signup(app.test_client(), data.pop("username", "someone_new"), **data)
        assert b"already registered" in page.data, over
    assert pending(app) == 0 and len(outbox(app)) == 1                       # (the one mail is from rita's own sign-up)


def test_if_the_name_is_taken_while_waiting_the_link_says_so_and_creates_nothing(app):
    signup(app.test_client(), "contested", email="late@example.com")
    link = link_in(outbox(app)[0])
    register(app, "contested")                                                 # someone else finished first
    before = users(app)
    done = app.test_client().post(link, data={"_csrf": token_of(app.test_client(), "/users/add")}, follow_redirects=True) \
        if False else None
    client = app.test_client()
    page, done = open_link(client, link)
    assert b"registered by someone else" in done.data and users(app) == before and pending(app) == 0


def test_throw_away_mailboxes_are_refused(app):
    for domain in ("mailinator.com", "YopMail.com", "guerrillamail.com"):
        page = signup(app.test_client(), "tmp_user", email="x@%s" % domain)
        assert b"permanent email address" in page.data, domain
    assert pending(app) == 0 and outbox(app) == []


def test_the_operator_can_block_more_domains(tmp_path):
    app = make_app(tmp_path, BLOCKED_EMAIL_DOMAINS="spam.example, other.example", **CONFIRM)
    assert b"permanent email address" in signup(app.test_client(), "user_one", email="a@spam.example").data
    assert b"permanent email address" in signup(app.test_client(), "user_two", email="a@OTHER.example").data
    assert b"Check your email" in signup(app.test_client(), "user_three", email="a@fine.example").data


def test_changing_the_address_to_a_throw_away_one_is_refused_too(app):
    client = register(app, "rita")
    page = client.post("/edit", data={"_csrf": token_of(client, "/edit"), "name": "Rita", "email": "rita@mailinator.com", "kind": "fan",
                                      "about": "x", "location": "", "website": ""}, follow_redirects=True)
    assert b"permanent email address" in page.data and scalar(app, "SELECT email FROM users") == "rita@example.com"


# ------------------------------------------------------------------ the old rules still hold
def test_the_other_signup_rules_are_checked_before_anything_is_stored(app):
    assert b"at least 18" in signup(app.test_client(), birth_date="2015-01-01").data
    assert b"Password" in signup(app.test_client(), password="short").data
    assert b"does not look like an email" in signup(app.test_client(), email="nonsense").data
    assert pending(app) == 0 and outbox(app) == []


def test_members_who_joined_before_keep_working_unconfirmed(app):
    client = register(app, "veteran")                                           # an account that exists, address not confirmed
    assert scalar(app, "SELECT email_verified FROM users") == 0
    assert b"Signed in as veteran" in signin(app.test_client(), "veteran").data
    assert b"confirm your email" in client.get("/feed").data                       # still gated for posting, as before


def test_without_required_verification_the_account_is_created_at_once(tmp_path):
    app = make_app(tmp_path)                                                    # development: no mail server, nothing required
    page = signup(app.test_client())
    assert users(app) == 1 and pending(app) == 0 and b"Your account is ready" in page.data
    assert "/users/verify/" in outbox(app)[0]["body"]


# ------------------------------------------------------------------ housekeeping
def test_unconfirmed_sign_ups_are_forgotten_after_a_day(app):
    signup(app.test_client(), "ghost")
    sql(app, "UPDATE pending_signups SET expires_at = '2000-01-01 00:00:00'")
    register(app, "rita")                                                       # any sign-in tidies up
    assert pending(app) == 0


def test_the_sign_up_mail_names_the_site_and_the_privacy_policy_says_how_long_it_keeps_it(app):
    signup(app.test_client())
    assert app.config["SITE_NAME"] in outbox(app)[0]["body"] and "24 hours" in outbox(app)[0]["body"]
    assert "not an account" in app.test_client().get("/privacy").get_data(as_text=True)
