# File: __init__.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""rockconnect: a community platform for bands, venues and their fans (Flask)."""
import logging
import os
import secrets
from datetime import timedelta

from flask import (Flask, abort, flash, jsonify, make_response, redirect, render_template,
                   request, session, url_for)
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, settings, storage
from .baclog import bac_log
from .util import asset, event_label, event_time, timeago

__version__ = "1.0.0"

ERROR_TEXT = {
    400: ("Bad request", "Something was wrong with that request. Go back, reload the page and try again."),
    403: ("Not allowed", "You do not have permission to do that."),
    404: ("Page not found", "We could not find what you were looking for."),
    405: ("Not allowed", "That action is not available here."),
    429: ("Slow down", "Too many attempts. Please wait a while and try again."),
    500: ("Something went wrong", "Sorry, that was our fault. Please try again in a moment."),
}


def _is_quiet(path):
    """Requests too frequent to log: static files, health checks and the chat/badge polling calls."""
    # /api/v1/gigs/nearby: its query string holds the searcher's position, which must not end up in logs
    return (path.startswith("/static/") or path in ("/conversations/unread", "/health", "/api/v1/gigs/nearby")
            or (path.startswith("/conversations/") and path.endswith("/messages")))


class _QuietPollingFilter(logging.Filter):
    """Hide the polling lines from Flask's own access log ("GET /conversations/3/messages?after=7")."""

    def filter(self, record):
        msg = record.getMessage()
        return not ("/conversations/unread" in msg or "/messages?after=" in msg
                    or "/static/" in msg or "/health" in msg or "/api/v1/gigs/nearby" in msg)


def _content_security_policy(config):
    """Everything is served from this site (Bootstrap, jQuery and the font are bundled), so the policy
    is strict: no third-party scripts, no inline scripts, no framing. Photos may come from S3 links."""
    images = "'self' data:" + (" https:" if config.get("S3_BUCKET") else "")
    return "; ".join([
        "default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src " + images,
        "font-src 'self'", "connect-src 'self'", "object-src 'none'", "base-uri 'self'",
        "form-action 'self'", "frame-ancestors 'none'",
    ])


def create_app(test_config=None):
    # application factory: builds and configures one Flask app instance
    # The instance folder holds the database file and the uploaded photos (your real data).
    # Tests pass their own INSTANCE_PATH (a temp folder) so running them can never create,
    # touch or tempt anyone to clean up the real instance folder.
    app = Flask(__name__, instance_path=(test_config or {}).get("INSTANCE_PATH") or os.environ.get("INSTANCE_PATH") or None,
                instance_relative_config=True)
    bac_log("app", "create_app() starting, data folder: %s" % app.instance_path)
    wz = logging.getLogger("werkzeug")
    if not any(isinstance(f, _QuietPollingFilter) for f in wz.filters):
        wz.addFilter(_QuietPollingFilter())

    app.config.from_mapping(settings.build())
    # post photos are stored here when no S3 bucket is configured (the database only keeps the file name)
    app.config["UPLOAD_DIR"] = os.path.join(app.instance_path, "uploads")
    if test_config:
        app.config.update(test_config)
        if app.config.get("TESTING") and "MAIL_BACKEND" not in test_config:
            app.config["MAIL_BACKEND"] = "memory"   # tests read the e-mails instead of sending them
        if app.config.get("TESTING") and "IMPORT_ON_DEMAND" not in test_config:
            app.config["IMPORT_ON_DEMAND"] = False  # tests never fetch from the event providers by themselves
        if app.config.get("TESTING") and "GEOCODER" not in test_config:
            app.config["GEOCODER"] = "none"         # tests never call the real lookup service
        bac_log("app", "test_config applied: keys=%s" % sorted(test_config))
    settings.validate(app.config)

    # The SERVER decides when a login ends (session_store.py). The cookie is kept a day longer on
    # purpose: if it vanished at the same moment, the server could not tell "your session expired"
    # from "you never signed in" and could not show the right message.
    app.config["PERMANENT_SESSION_LIFETIME"] = (
        timedelta(minutes=app.config["SESSION_LIFETIME_MINUTES"]) + timedelta(days=1))
    bac_log("app", "environment=%s, login sessions last %s minute(s)"
            % (app.config["APP_ENV"], app.config["SESSION_LIFETIME_MINUTES"]))
    if app.config["SECRET_KEY"] == settings.DEFAULT_SECRET:
        # warn loudly: the default key must not be used outside local development
        bac_log("app", "WARNING: using the default SECRET_KEY, set SECRET_KEY in the environment")
    if app.config["TRUST_PROXY"]:
        # one proxy in front (nginx, Caddy, a PaaS router): believe its X-Forwarded-* headers
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
        bac_log("app", "TRUST_PROXY on: client address and scheme come from X-Forwarded-* headers")

    # the instance folder holds the local SQLite file; make sure it exists
    os.makedirs(app.instance_path, exist_ok=True)
    # local SQLite by default, or a remote DB when DATABASE_URL is set (see db.py)
    db.init_app(app)
    storage.init_app(app)

    from . import (account, admin, api, auth, cli, conversations, events, feed, legal, moderation,
                   system, views)

    for module in (auth, views, feed, conversations, moderation, admin, account, legal, system, api, events):
        app.register_blueprint(module.bp)
    cli.init_app(app)
    bac_log("app", "blueprints registered")

    # template helpers: {{ created_at|timeago }} and {{ asset('css/style.css') }}
    app.add_template_filter(timeago, "timeago")
    app.add_template_filter(event_time, "eventtime")
    app.add_template_filter(event_label, "eventlabel")
    app.jinja_env.globals["asset"] = asset

    csp = _content_security_policy(app.config)

    # ------------------------------------------------------------------ error pages
    def _error_response(code, message=None):
        title, default = ERROR_TEXT.get(code, ("Error", "Something went wrong."))
        message = message or default
        if request.headers.get("X-Requested-With") == "fetch" or request.path.startswith("/api/"):
            return make_response(jsonify(error=message, code="http_%d" % code), code)
        return make_response(render_template("error.html", code=code, title=title, message=message), code)

    @app.errorhandler(413)
    def upload_too_large(e):
        bac_log("app", "request body too large -> 413")
        flash("That upload is too large (photos can be up to 5 MB).", "danger")
        return redirect(url_for("feed.index"))

    @app.errorhandler(HTTPException)
    def http_error(e):
        # our own pages instead of werkzeug's plain ones; 3xx are not errors
        if e.code is None or e.code < 400:
            return e
        response = _error_response(e.code, e.description if e.code in (400, 429) else None)
        retry = dict(e.get_headers()).get("Retry-After")
        if retry:
            response.headers["Retry-After"] = retry
        return response

    @app.errorhandler(500)
    def server_error(e):
        bac_log("app", "UNHANDLED ERROR -> 500")
        return _error_response(500)

    # ------------------------------------------------------------------ request hooks
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
                abort(400, "Invalid or missing CSRF token. Reload the page and try again.")
            bac_log("csrf", "token OK for POST %s" % request.path)

    @app.after_request
    def secure_and_log(response):
        # stop browsers from guessing a different content type than the one we send
        # (important for user uploads)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"                 # nobody can frame the site (clickjacking)
        response.headers["Referrer-Policy"] = "same-origin"
        # location only for this site itself (the "gigs near me" button), never for embedded third parties
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(self)"
        response.headers.setdefault("Content-Security-Policy", csp)
        if app.config["HSTS"] and request.is_secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
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

    @app.context_processor
    def inject_site():
        # branding and operator details for every template (set from the environment, see settings.py)
        cfg = app.config
        name, accent = cfg["SITE_NAME"], cfg["SITE_NAME_ACCENT"]
        if accent and name.lower().endswith(accent.lower()) and len(accent) < len(name):
            head, tail = name[:-len(accent)], name[-len(accent):]
        else:
            head, tail = name, ""
        return {"site": {
            "name": name, "name_head": head, "name_tail": tail, "tagline": cfg["SITE_TAGLINE"],
            "operator": cfg["OPERATOR_NAME"], "address": cfg["OPERATOR_ADDRESS"],
            "contact": cfg["CONTACT_EMAIL"], "min_age": cfg["MIN_AGE"], "kinds": db.KINDS,
            "verification_required": cfg["REQUIRE_EMAIL_VERIFICATION"], "version": __version__,
        }}

    bac_log("app", "create_app() finished")
    return app
