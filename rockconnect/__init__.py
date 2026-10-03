# File: __init__.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""rockconnect: Python/Flask port of the rockonnect web app (NCI HDSWT 2019)."""
import logging
import os
import secrets
from datetime import timedelta

from flask import (Flask, abort, flash, redirect, request, session,
                   url_for)

from . import db
from .baclog import bac_log
from .util import asset, timeago


def _is_quiet(path):
    """Requests too frequent to log: static files and the chat/badge polling calls."""
    return (path.startswith("/static/") or path == "/conversations/unread"
            or (path.startswith("/conversations/") and path.endswith("/messages")))


class _QuietPollingFilter(logging.Filter):
    """Hide the polling lines from Flask's own access log ("GET /conversations/3/messages?after=7")."""

    def filter(self, record):
        msg = record.getMessage()
        return not ("/conversations/unread" in msg or "/messages?after=" in msg or "/static/" in msg)


def create_app(test_config=None):
    # application factory: builds and configures one Flask app instance
    # The instance folder holds the database file and the uploaded photos (your real data).
    # Tests pass their own INSTANCE_PATH (a temp folder) so running them can never create,
    # touch or tempt anyone to clean up the real instance folder.
    app = Flask(__name__, instance_path=(test_config or {}).get("INSTANCE_PATH"),
                instance_relative_config=True)
    bac_log("app", "create_app() starting, data folder: %s" % app.instance_path)
    wz = logging.getLogger("werkzeug")
    if not any(isinstance(f, _QuietPollingFilter) for f in wz.filters):
        wz.addFilter(_QuietPollingFilter())
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
        # How long a login lasts. After this the user must sign in again. Change it with the
        # SESSION_MINUTES environment variable (default 60 minutes).
        SESSION_LIFETIME_MINUTES=int(os.environ.get("SESSION_MINUTES", 60)),
        SESSION_COOKIE_HTTPONLY=True,    # JavaScript cannot read the cookie
        SESSION_COOKIE_SAMESITE="Lax",   # not sent on cross-site POSTs
        # set SESSION_COOKIE_SECURE=1 when the site is served over https
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",
        # let browsers cache static files (css, background image) for an hour instead of
        # re-validating on every page load, which is what produced the flood of "304 Not Modified"
        SEND_FILE_MAX_AGE_DEFAULT=3600,
        # post photos are stored on disk here (the database only keeps the file name)
        UPLOAD_DIR=os.path.join(app.instance_path, "uploads"),
        # hard cap on any request body; each photo is also limited to 5 MB in feed.py
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
        bac_log("app", "test_config applied: keys=%s" % sorted(test_config))
    # The SERVER decides when a login ends (session_store.py). The cookie is kept a day longer on
    # purpose: if it vanished at the same moment, the server could not tell "your session expired"
    # from "you never signed in" and could not show the right message.
    app.config["PERMANENT_SESSION_LIFETIME"] = (
        timedelta(minutes=app.config["SESSION_LIFETIME_MINUTES"]) + timedelta(days=1))
    bac_log("app", "login sessions last %s minute(s)" % app.config["SESSION_LIFETIME_MINUTES"])
    if app.config["SECRET_KEY"] == "dev-only-change-me":
        # warn loudly: the default key must not be used outside local development
        bac_log("app", "WARNING: using the default SECRET_KEY, set SECRET_KEY in the environment")

    # the instance folder holds the local SQLite file; make sure it exists
    os.makedirs(app.instance_path, exist_ok=True)
    # local SQLite by default, or a remote DB when DATABASE_URL is set (see db.py)
    db.init_app(app)

    from . import auth, conversations, feed, views

    app.register_blueprint(auth.bp)
    app.register_blueprint(views.bp)
    app.register_blueprint(feed.bp)
    app.register_blueprint(conversations.bp)
    bac_log("app", "blueprints registered: auth, views, feed, conversations")

    # template helpers: {{ created_at|timeago }} and {{ asset('css/style.css') }}
    app.add_template_filter(timeago, "timeago")
    app.jinja_env.globals["asset"] = asset

    @app.errorhandler(413)
    def upload_too_large(e):
        bac_log("app", "request body too large -> 413")
        flash("That upload is too large (photos can be up to 5 MB).", "danger")
        return redirect(url_for("feed.index"))

    @app.before_request
    def csrf_protect():
        # log every incoming request (method + path only, never form data);
        # static files and chat polling are skipped so the log only shows real page activity
        if not _is_quiet(request.path):
            bac_log("request", "%s %s" % (request.method, request.path))
        # every POST must carry the CSRF token that was put in the session
        if request.method == "POST":
            token = session.get("_csrf")
            if not token or token != request.form.get("_csrf"):
                bac_log("csrf", "REJECTED POST %s: invalid or missing token" % request.path)
                abort(400, "Invalid or missing CSRF token")
            bac_log("csrf", "token OK for POST %s" % request.path)

    @app.after_request
    def log_response(response):
        # stop browsers from guessing a different content type than the one we send
        # (important for user uploads)
        response.headers["X-Content-Type-Options"] = "nosniff"
        # log the outcome of every request (again skipping static files and polling)
        if not _is_quiet(request.path):
            bac_log("response", "%s %s -> %s" % (request.method, request.path, response.status_code))
        return response

    @app.context_processor
    def inject_csrf():
        # make a csrf_token available to every template, creating it on first use
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
            bac_log("csrf", "new CSRF token issued for this session")
        return {"csrf_token": session["_csrf"]}

    bac_log("app", "create_app() finished")
    return app
