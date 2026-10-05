# File: views.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Home, people directory, profile and edit pages."""
from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, url_for)
from sqlalchemy.exc import IntegrityError

import re

from . import importer, ratelimit
from .auth import login_required, send_verification
from .baclog import bac_log
from .db import KINDS, commit, execute, rollback
from .feed import load_posts
from .util import clean_website, valid_email

bp = Blueprint("views", __name__)


def is_admin():
    return bool(g.user) and g.user["role"] == "admin"


@bp.route("/")
@bp.route("/home")
def home():
    """Landing page: only sign in / sign up for visitors; members go straight to the feed."""
    if g.user:
        bac_log("home", "signed-in user %r -> redirect to feed" % g.user["username"])
        return redirect(url_for("feed.index"))
    bac_log("home", "rendering landing page (signed out)")
    return render_template("home.html")


@bp.route("/people")
@login_required
def index():
    """The directory: ?q= filters by username, name or location, ?kind= by fan / band / venue."""
    q = request.args.get("q", "").strip()
    kind = request.args.get("kind", "")
    where, params = [], {}
    if not is_admin():
        where.append("status = 'active'")  # suspended accounts disappear from the directory
    if q:
        # lower() on both sides = case-insensitive on every database (LIKE is case-sensitive on PostgreSQL)
        where.append("(lower(username) LIKE :p OR lower(name) LIKE :p OR lower(COALESCE(location, '')) LIKE :p)")
        params["p"] = "%" + q.lower() + "%"
    if kind in KINDS:
        where.append("kind = :kind")
        params["kind"] = kind
    users = execute(
        "SELECT id, username, name, kind, location, status FROM users"
        + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY username", **params
    ).mappings().fetchall()
    bac_log("index", "search q=%r kind=%r -> %d user(s)" % (q, kind, len(users)))
    return render_template("index.html", title="rockconnect", users=users, q=q, kind=kind)


@bp.route("/users_list/<int:user_id>")
def profile(user_id):
    user = execute(
        "SELECT id, username, name, about, kind, location, website, status, created_at"
        " FROM users WHERE id = :id", id=user_id
    ).mappings().fetchone()
    if user is None or (user["status"] == "banned" and not is_admin()):
        bac_log("profile", "user id=%s not found (or suspended) -> 404" % user_id)
        abort(404)
    bac_log("profile", "showing profile of %r (id=%s)" % (user["username"], user_id))
    # the wall (that member's posts) is only for signed-in members
    wall, comments_by_post, has_more = ([], {}, False)
    i_blocked = blocked_me = False
    if g.user:
        wall, comments_by_post, has_more = load_posts(
            user_id=user_id, before=request.args.get("before", type=int))
        pair = execute("SELECT blocker_id FROM blocks WHERE (blocker_id = :me AND blocked_id = :u)"
                       " OR (blocker_id = :u AND blocked_id = :me)", me=g.user["id"], u=user_id).fetchall()
        i_blocked = any(r[0] == g.user["id"] for r in pair)
        blocked_me = any(r[0] == user_id for r in pair)
    return render_template("users_list.html", user=user, posts=wall,
                           comments_by_post=comments_by_post, has_more=has_more,
                           next_before=wall[-1]["id"] if has_more else None,
                           i_blocked=i_blocked, blocked_me=blocked_me)


def _bandsintown_form(kind):
    """The Bandsintown part of the profile form: (error, changes). `changes` is None when nothing is to be saved,
    else a dict of the two columns. Only bands can connect, and with their OWN app id (their terms: one per artist)."""
    artist = request.form.get("bandsintown_artist", "").strip()
    app_id = request.form.get("bandsintown_app_id", "").strip()
    if kind != "band" or (not artist and not app_id and request.form.get("bandsintown_disconnect") != "1"
                          and not g.user["bandsintown_artist"]):
        return None, None
    if request.form.get("bandsintown_disconnect") == "1" or not artist:
        return None, {"bandsintown_artist": None, "bandsintown_app_id": None}
    if len(artist) > 120 or "/" in artist or "?" in artist or "#" in artist:
        return "The Bandsintown artist name is not valid (no / ? # and at most 120 characters).", None
    app_id = app_id or (g.user["bandsintown_app_id"] or "")   # left blank = keep the one already saved
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", app_id):
        return "Bandsintown needs your own app id (letters, digits, dot, dash or underscore).", None
    return None, {"bandsintown_artist": artist, "bandsintown_app_id": app_id}


def _sync_bandsintown():
    """Right after saving: fetch the band's dates once so they show without waiting for the daily import."""
    if ratelimit.blocked("artist_sync_user", g.user["id"]):
        return "Your Bandsintown dates will be refreshed by the daily import."
    ratelimit.hit("artist_sync_user", g.user["id"])
    rows = importer.connected_artists(g.user["id"])
    if not rows:
        return None
    result = importer.import_artist(rows[0], timeout=importer.ONDEMAND_TIMEOUT)
    if result["error"]:
        return "Bandsintown: " + result["error"]
    return "Bandsintown: %d upcoming date(s) found for %s." % (result["fetched"], rows[0]["bandsintown_artist"])


@bp.route("/edit", methods=("GET", "POST"))
@login_required
def edit():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        about = request.form.get("about", "").strip()
        location = request.form.get("location", "").strip()
        website = clean_website(request.form.get("website"))
        kind = request.form.get("kind", g.user["kind"])
        bac_log("edit", "update attempt by %r" % g.user["username"])
        error = None
        if not (name and email and about):
            error = "Name, email and about are required."
        elif not valid_email(email):
            error = "That does not look like an email address."
        elif website is None:
            error = "The website must be a normal http(s) link."
        elif kind not in KINDS:
            error = "Choose fan, band or venue."
        elif len(name) > 120 or len(about) > 1000 or len(location) > 120:
            error = "Something you typed is too long."
        bit_error, bit_changes = _bandsintown_form(kind)
        error = error or bit_error
        if not error and email != g.user["email"].lower() and execute(
                "SELECT 1 FROM users WHERE lower(email) = :e AND id <> :id",
                e=email, id=g.user["id"]).fetchone():
            error = "That email is already registered."
        if error:
            bac_log("edit", "rejected: %s" % error)
            flash(error, "danger")
        else:
            email_changed = email != g.user["email"].lower()
            # with confirmation required the address only changes once the owner of the NEW mailbox
            # opens the link; otherwise it changes now and is simply marked as not confirmed
            wait_for_link = email_changed and current_app.config["REQUIRE_EMAIL_VERIFICATION"]
            change_now = email_changed and not wait_for_link
            try:
                execute(
                    "UPDATE users SET name = :name, about = :about, location = :location,"
                    " website = :website, kind = :kind" +
                    (", email = :email, email_verified = 0" if change_now else "") +
                    (", bandsintown_artist = :bi_artist, bandsintown_app_id = :bi_app" if bit_changes else "") +
                    " WHERE id = :id",
                    name=name, about=about, location=location or None, website=website or None,
                    kind=kind, email=email, id=g.user["id"],
                    bi_artist=(bit_changes or {}).get("bandsintown_artist"), bi_app=(bit_changes or {}).get("bandsintown_app_id"),
                )
                if bit_changes and not bit_changes["bandsintown_artist"]:
                    execute("DELETE FROM external_events WHERE user_id = :u", u=g.user["id"])   # disconnected: forget its dates
                commit()
            except IntegrityError:  # someone took that address a moment ago
                rollback()
                bac_log("edit", "rejected, email already used by another account")
                flash("That email is already registered.", "danger")
            else:
                bac_log("edit", "profile of %r updated" % g.user["username"])
                if email_changed:
                    send_verification(g.user["id"], g.user["email"], new_email=email)
                    flash("Profile updated. We sent a link to %s: %s" % (
                        email, "your email address changes when you open it." if wait_for_link
                        else "open it to confirm the new address."), "success")
                else:
                    flash("Profile updated.", "success")
                if bit_changes and bit_changes["bandsintown_artist"]:
                    note = _sync_bandsintown()
                    if note:
                        flash(note, "info")
                return redirect(url_for("views.profile", user_id=g.user["id"]))
    return render_template("edit.html")
