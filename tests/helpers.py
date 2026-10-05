# File: helpers.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Shared helpers for the newer test files: build an app, register people, read the (fake) outbox."""
import re

from rockconnect import create_app
from rockconnect.db import commit, execute
from rockconnect.settings import DEFAULT_RATE_LIMITS

FETCH = {"X-Requested-With": "fetch"}
PASSWORD = "S3cret!pw"


def make_app(tmp_path, **config):
    """A fresh app on its own temp database and photo folder."""
    base = {"TESTING": True, "INSTANCE_PATH": str(tmp_path / "instance"),
            "DATABASE_URL": "sqlite:///" + str(tmp_path / "t.sqlite"),
            "UPLOAD_DIR": str(tmp_path / "uploads")}
    return create_app({**base, **config})


def limits(**overrides):
    """The default rate limits with a few changed: limits(signin_user=(3, 900))."""
    return {**DEFAULT_RATE_LIMITS, **overrides}


def token_of(client, url="/users/signin"):
    return re.search(r'name="_csrf" value="([0-9a-f]+)"', client.get(url).get_data(as_text=True)).group(1)


def signup(client, username="bacco", **over):
    data = {"username": username, "name": username.title() + " Test", "email": username + "@example.com",
            "password": PASSWORD, "about": "I like rock", "accept": "1", "kind": "fan", **over}
    data["_csrf"] = token_of(client, "/users/add")
    return client.post("/users/add", data=data, follow_redirects=True)


def signin(client, username="bacco", password=PASSWORD, **extra):
    return client.post("/users/signin", data={"username": username, "password": password, **extra,
                                              "_csrf": token_of(client)}, follow_redirects=True)


def register(app, username="bacco", **over):
    """Sign up AND sign in on a new test client; returns the client."""
    client = app.test_client()
    signup(client, username, **over)
    signin(client, username)
    return client


def post_form(client, url, page="/feed", **data):
    """POST a form with a fresh CSRF token taken from `page`."""
    return client.post(url, data={"_csrf": token_of(client, page), **data}, follow_redirects=True)


def user_id(app, username):
    with app.app_context():
        return execute("SELECT id FROM users WHERE username = :u", u=username).scalar()


def sql(app, statement, **params):
    """Run one write statement against the app's database."""
    with app.app_context():
        result = execute(statement, **params)
        commit()
        return result


def scalar(app, statement, **params):
    with app.app_context():
        return execute(statement, **params).scalar()


def make_admin(app, username):
    sql(app, "UPDATE users SET role = 'admin' WHERE username = :u", u=username)


def outbox(app):
    return app.extensions.get("outbox", [])


def link_in(message):
    """The first http link in an e-mail body, as a path (works with the test client)."""
    return re.search(r"https?://[^/\s]+(/\S+)", message["body"]).group(1)
