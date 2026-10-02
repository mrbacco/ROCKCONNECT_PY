# File: auth.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Sign up, sign in and sign out (the /users/add and /users/signin routes)."""
import functools

import bcrypt
from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)
from sqlalchemy.exc import IntegrityError

from .baclog import bac_log
from .db import commit, execute, insert, rollback, users

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


def login_required(view):
    """Decorator: redirect to sign-in when nobody is logged in."""
    @functools.wraps(view)
    def wrapped(**kwargs):
        if g.user is None:
            bac_log("auth", "login_required: anonymous access to %s blocked" % request.path)
            flash("Please sign in first.", "warning")
            return redirect(url_for("auth.signin"))
        return view(**kwargs)

    return wrapped


@bp.before_app_request
def load_logged_in_user():
    # runs before every request: puts the logged-in user (or None) on g.user
    user_id = session.get("user_id")
    g.user = None
    if user_id is not None:
        g.user = execute(
            "SELECT * FROM users WHERE id = :id", id=user_id
        ).mappings().fetchone()
        bac_log("auth", "session user_id=%s -> %s" % (
            user_id, g.user["username"] if g.user else "NOT FOUND in db"))


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
                bac_log("signup", "user %r created (id=%s), signing in automatically"
                        % (form["username"], new_id))
                # like Facebook: a new member lands straight on the feed, already signed in
                session.clear()
                session.permanent = True
                session["user_id"] = new_id
                flash("Welcome to rockconnect, %s! Account created, share your first post." % form["name"],
                      "success")
                return redirect(url_for("feed.index"))
    form.pop("password", None)
    return render_template("add_users.html", title="rockconnect", form=form)


@bp.route("/signin", methods=("GET", "POST"))
def signin():
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
            return render_template("signin_users.html", title="rockconnect"), 401
        session.clear()  # drop any old session data (prevents session fixation)
        session.permanent = True
        session["user_id"] = user["id"]
        bac_log("signin", "SUCCESS user=%r id=%s, session created" % (user["username"], user["id"]))
        flash("Successfully logged in as %s." % user["username"], "success")
        return redirect(url_for("feed.index"))  # land on the news feed
    return render_template("signin_users.html", title="rockconnect")


@bp.route("/signout", methods=("POST",))
def signout():
    bac_log("signout", "user id=%s signed out" % session.get("user_id"))
    session.clear()
    flash("You have been signed out.", "info")
    return redirect(url_for("views.home"))
