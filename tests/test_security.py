# File: test_security.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Security headers, strict CSP, error pages, health check, theming, production safety, rate limiting."""
import pytest

from helpers import FETCH, limits, make_app, register, signin, signup, sql, token_of


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def test_security_headers_on_every_page(app):
    r = app.test_client().get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "same-origin"
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp


def test_pages_load_nothing_from_third_parties_and_use_no_inline_code(app):
    client = register(app)
    html = client.get("/feed").get_data(as_text=True) + app.test_client().get("/").get_data(as_text=True)
    assert "cdn.jsdelivr" not in html and "googleapis" not in html and "gstatic" not in html
    for banned in (" onclick=", " onsubmit=", " onchange=", " style=", "<script>"):
        assert banned not in html


def test_hsts_only_over_https_in_production(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")         # how a real deployment turns production mode on
    monkeypatch.setenv("SECRET_KEY", "x" * 40)
    prod = make_app(tmp_path)
    client = prod.test_client()
    assert "Strict-Transport-Security" not in client.get("/").headers          # plain http: no HSTS
    assert "max-age" in client.get("/", base_url="https://localhost").headers["Strict-Transport-Security"]
    assert prod.config["SESSION_COOKIE_SECURE"] is True


def test_production_refuses_to_start_with_an_unsafe_secret(tmp_path):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        make_app(tmp_path, APP_ENV="production")                      # default dev secret
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        make_app(tmp_path, APP_ENV="production", SECRET_KEY="short")
    make_app(tmp_path, APP_ENV="production", SECRET_KEY="a-long-random-value-" * 3)  # fine


def test_branded_error_pages_and_json_errors(app):
    client = app.test_client()
    page = client.get("/no/such/page")
    assert page.status_code == 404 and b"Page not found" in page.data and b"<html" in page.data
    assert client.get("/no/such/page", headers=FETCH).get_json()["error"]
    assert b"Invalid or missing CSRF" in client.post("/users/signin", data={}).data


def test_health_endpoint(app, monkeypatch):
    client = app.test_client()
    r = client.get("/health")
    assert r.status_code == 200 and r.get_json()["status"] == "ok"

    def broken(*a, **k):
        raise RuntimeError("database down")
    monkeypatch.setattr("rockconnect.system.execute", broken)
    r = client.get("/health")
    assert r.status_code == 503 and r.get_json()["database"] == "unreachable"


def test_health_needs_no_sign_in_and_leaks_nothing(app):
    body = app.test_client().get("/health").get_data(as_text=True)
    assert "password" not in body and "email" not in body


def test_theme_css_follows_accent_color(tmp_path):
    custom = make_app(tmp_path, ACCENT_COLOR="#2ec4b6")
    css = custom.test_client().get("/theme.css")
    assert css.mimetype == "text/css"
    text = css.get_data(as_text=True)
    assert "--amber: #2ec4b6" in text and "--amber-rgb: 46,196,182" in text


def test_invalid_accent_color_falls_back(monkeypatch, tmp_path):
    from rockconnect import settings
    assert settings.accent_color("red; } body { display:none") == settings.DEFAULT_ACCENT
    assert settings.accent_color("#12ab9f") == "#12ab9f"


def test_branding_comes_from_configuration(tmp_path):
    app = make_app(tmp_path, SITE_NAME="GigHub", SITE_NAME_ACCENT="Hub", SITE_TAGLINE="Gigs near you",
                   OPERATOR_NAME="GigHub Ltd", CONTACT_EMAIL="hello@gighub.example")
    html = app.test_client().get("/").get_data(as_text=True)
    assert "Gig<span>Hub</span>" in html and "Gigs near you" in html
    assert "rockconnect" not in html.lower()
    terms = app.test_client().get("/terms").get_data(as_text=True)
    assert "GigHub Ltd" in terms and "hello@gighub.example" in terms


# ------------------------------------------------------------------ rate limiting
def bad_signin(client, username="bacco", **extra):
    return client.post("/users/signin", data={"username": username, "password": "wrong-password-1",
                                              "_csrf": token_of(client), **extra})


def test_repeated_failed_sign_ins_for_one_username_are_blocked(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(signin_user=(3, 900)))
    client = app.test_client()
    signup(client)
    assert [bad_signin(client).status_code for _ in range(3)] == [401, 401, 401]
    blocked = bad_signin(client)
    assert blocked.status_code == 429 and "Retry-After" in blocked.headers
    # even the RIGHT password is refused while the lock is on: guessing gets nothing
    assert signin(client).status_code == 429
    # another account is not affected
    signup(client, "rita")
    assert b"Signed in as rita" in signin(client, "rita").data


def test_failed_sign_ins_per_address_are_blocked_and_proxy_headers_are_honoured(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(signin_ip=(2, 900)), TRUST_PROXY=True)
    client = app.test_client()
    one = {"environ_base": {"HTTP_X_FORWARDED_FOR": "203.0.113.5"}}
    other = {"environ_base": {"HTTP_X_FORWARDED_FOR": "198.51.100.9"}}
    for name in ("a1", "a2"):
        assert client.post("/users/signin", data={"username": name, "password": "nope-nope-1",
                                                  "_csrf": token_of(client)}, **one).status_code == 401
    assert client.post("/users/signin", data={"username": "a3", "password": "nope-nope-1",
                                              "_csrf": token_of(client)}, **one).status_code == 429
    assert client.post("/users/signin", data={"username": "a3", "password": "nope-nope-1",
                                              "_csrf": token_of(client)}, **other).status_code == 401


def test_limit_expires_with_the_window(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(signin_user=(2, 900)))
    client = app.test_client()
    signup(client)
    bad_signin(client), bad_signin(client)
    assert bad_signin(client).status_code == 429
    sql(app, "UPDATE rate_hits SET created_at = '2000-01-01 00:00:00'")      # the failures were long ago
    assert b"Signed in as bacco" in signin(client).data


def test_rate_limit_keys_are_hashed(tmp_path):
    app = make_app(tmp_path)
    client = app.test_client()
    bad_signin(client, "secret-name")
    with app.app_context():
        from rockconnect.db import execute
        keys = [r[0] for r in execute("SELECT bucket FROM rate_hits")]
    assert keys and all("secret-name" not in k and len(k) == 64 for k in keys)


def test_signup_is_limited_per_address(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(signup_ip=(2, 3600)))
    client = app.test_client()
    signup(client, "u1"), signup(client, "u2")
    assert client.post("/users/add", data={"_csrf": token_of(client, "/users/add"), "username": "u3"}).status_code == 429


def test_posting_is_limited_and_scripts_get_json(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(post_user=(2, 600), message_user=(1, 600)))
    client = register(app)
    for n in range(2):
        client.post("/posts", data={"_csrf": token_of(client, "/feed"), "body": "post %d" % n})
    r = client.post("/posts", data={"_csrf": token_of(client, "/feed"), "body": "one too many"}, headers=FETCH)
    assert r.status_code == 429 and "Too many" in r.get_json()["error"]


def test_limits_can_be_switched_off(tmp_path):
    app = make_app(tmp_path, RATE_LIMITS=limits(signin_user=(1, 900)), RATE_LIMITS_ENABLED=False)
    client = app.test_client()
    signup(client)
    assert [bad_signin(client).status_code for _ in range(4)] == [401] * 4


def test_limits_can_be_set_from_the_environment(monkeypatch):
    from rockconnect import settings
    monkeypatch.setenv("RATE_LIMIT_SIGNIN_IP", "5/60")
    monkeypatch.setenv("RATE_LIMIT_POST_USER", "garbage")
    built = settings.build()["RATE_LIMITS"]
    assert built["signin_ip"] == (5, 60) and built["post_user"] == settings.DEFAULT_RATE_LIMITS["post_user"]
