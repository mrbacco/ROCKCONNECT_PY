# File: auth.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Sign up, sign in and sign out (the /users/add and /users/signin routes) + session handling.

Flow:  1. register (/users/add)         creates the account only
       2. sign in (/users/signin)       creates a server-side session that lasts SESSION_MINUTES
       3. every request                 checks the session is still alive (see session_store.py)
       4. session expired or signed out the user has to sign in again (and returns to where they were)
"""
import functools

import bcrypt
from flask import (Blueprint, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from sqlalchemy.exc import IntegrityError

from . import session_store
from .baclog import bac_log
from .db import commit, execute, insert, rollback, users
from .util import safe_next

bp = Blueprint("auth", __name__, url_prefix="/users")

FIELDS = ("username", "name", "email", "password", "about")


def hash_password(password):
    # bcrypt with cost factor 10 (same as bcryptjs genSalt(10) in the original)
    bac_log("auth", "hashing password with bcrypt (cost 10)")
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(10)).decode()


def check_password(password, hashed):
    # compares the typed password with the stored hash; never logs either value
    ok = bcrypt.checkpw(password.encode(), hashed.encode())
    bac_log("auth", "bcrypt password check -> %s" % ("match" if ok else "NO match"))
    return ok


def _wants_json():
    # chat.js / main.js send this header; plain page loads and HTML forms do not
    return request.headers.get("X-Requested-With") == "fetch"


def login_required(view):
    """Decorator: no valid session -> sign-in page (HTML) or 401 JSON (for the background scripts)."""
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            bac_log("auth", "login_required: no valid session for %s %s -> sign in"
                    % (request.method, request.path))
            if _wants_json():
                # the page's scripts see this and send the browser to the sign-in page
                return jsonify(error="session_expired", login_url=url_for("auth.signin")), 401
            if not g.session_expired:  # an expired session already queued its own message
                flash("Please sign in first.", "warning")
            # remember where the user was (GET only: a POST cannot be replayed)
            target = request.full_path.rstrip("?") if request.method == "GET" else None
            return redirect(url_for("auth.signin", next=target))
        return view(**kwargs)

    return wrapped


@bp.before_app_request
def load_logged_in_user():
    """Runs before every request: finds the session behind the cookie and sets g.user (or None)."""
    g.user = None
    g.session_expired = False
    g.session_seconds_left = 0
    if request.endpoint == "static":
        return  # css/js/images need no session (and no database query)
    token = session.get("sid")
    if not token:
        return
    user = session_store.find_valid(token)
    if user is None:
        # the cookie is still there but the server session ended (expired or signed out elsewhere)
        session.pop("sid", None)
        g.session_expired = True
        flash("Your session has expired. Please sign in again.", "warning")
        bac_log("session", "expired or unknown session cookie -> user must sign in again")
        return
    g.user = user
    g.session_seconds_left = session_store.seconds_left(user["session_expires_at"])


@bp.app_context_processor
def inject_session_info():
    # available in every template: drives the countdown in the menu bar
    return {"session_seconds_left": getattr(g, "session_seconds_left", 0)}


@bp.route("/add", methods=("GET", "POST"))
def signup():
    form = {}
    if request.method == "POST":
        # collect the registration fields (password is kept exactly as typed)
        form = {f: request.form.get(f, "").strip() for f in FIELDS}
        form["password"] = request.form.get("password", "")  # keep as typed
        bac_log("signup", "attempt for username=%r" % form["username"])
        missing = [f for f in FIELDS if not form[f]]
        if missing:
            bac_log("signup", "rejected, missing fields: %s" % ", ".join(missing))
            flash("All fields are required (missing: %s)." % ", ".join(missing), "danger")
        else:
            try:
                new_id = insert(
                    users, username=form["username"], name=form["name"], email=form["email"],
                    password=hash_password(form["password"]), about=form["about"],
                )
                commit()
            except IntegrityError:
                # UNIQUE constraint on username or email was violated
                rollback()  # a failed statement leaves the transaction unusable on PostgreSQL
                bac_log("signup", "rejected, duplicate username/email for %r" % form["username"])
                flash("That username or email is already registered.", "danger")
            else:
                # registering only creates the account; the session starts when the user signs in
                bac_log("signup", "user %r created (id=%s), now needs to sign in"
                        % (form["username"], new_id))
                flash("Welcome to rockconnect, %s! Your account is ready, please sign in."
                      % form["name"], "success")
                return redirect(url_for("auth.signin"))
    form.pop("password", None)
    return render_template("add_users.html", title="rockconnect", form=form)


@bp.route("/signin", methods=("GET", "POST"))
def signin():
    # where to go after signing in (set when a protected page sent the user here)
    next_url = request.values.get("next", "")
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        bac_log("signin", "attempt for username=%r" % username)
        user = execute(
            "SELECT * FROM users WHERE username = :username", username=username
        ).mappings().fetchone()
        if user is None or not check_password(password, user["password"]):
            # same message for unknown user and wrong password (no user enumeration)
            bac_log("signin", "FAILED for username=%r (unknown user or bad password)" % username)
            flash("Invalid credentials.", "danger")
            return render_template("signin_users.html", title="rockconnect", next_url=next_url), 401
        session_store.destroy(session.get("sid"))  # end any older session of this browser
        session.clear()                            # fresh cookie (prevents session fixation)
        token, expires_at = session_store.create(user["id"])
        session["sid"] = token
        session.permanent = True                   # cookie outlives the login (see create_app) so expiry can be explained
        bac_log("signin", "SUCCESS user=%r id=%s, session valid until %s UTC"
                % (user["username"], user["id"], expires_at))
        flash("Signed in as %s. Your session lasts %d minutes."
              % (user["username"], session_store.lifetime_minutes()), "success")
        return redirect(safe_next(next_url, url_for("feed.index")))
    return render_template("signin_users.html", title="rockconnect", next_url=next_url)


@bp.route("/signout", methods=("POST",))
def signout():
    bac_log("signout", "user id=%s signs out" % (g.user["id"] if g.user else None))
    session_store.destroy(session.get("sid"))  # the session dies on the server, not just in the browser
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("views.home"))
