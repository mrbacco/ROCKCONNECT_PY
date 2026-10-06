# File: views.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Home, people directory, profile and edit pages."""
from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template,
                   request, url_for)
from sqlalchemy.exc import IntegrityError

import re

from . import follows, importer, ratelimit, social, taxonomy
from .auth import login_required, send_verification
from .baclog import bac_log
from .db import KINDS, commit, execute, rollback
from .feed import load_posts
from .util import clean_website, valid_email

bp = Blueprint("views", __name__)


def user_hides_plans(user_id):
    return bool(execute("SELECT hide_plans FROM users WHERE id = :id", id=user_id).scalar())


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
    """The directory: ?q= username / name / place, ?kind= fan / band / venue, and by what people play, like and look for."""
    q = request.args.get("q", "").strip()
    kind = request.args.get("kind", "")
    filters = {name: request.args.get(name, "") for name in ("instrument", "genre", "goal", "level")}
    relation = request.args.get("relation") if request.args.get("relation") in ("following", "followers") else ""
    users = social.search_people(g.user, filters, q, kind, include_suspended=is_admin(), limit=200, relation=relation)
    bac_log("index", "search q=%r kind=%r filters=%s -> %d user(s)" % (
        q, kind, ",".join(k for k, v in filters.items() if v) or "none", len(users)))
    return render_template("index.html", title="rockconnect", users=users, q=q, kind=kind, filters=filters, relation=relation,
                           instruments=taxonomy.INSTRUMENTS, genres=taxonomy.GENRES, goals=taxonomy.GOALS,
                           levels=taxonomy.LEVELS)


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
    plans = []
    if g.user and not user_hides_plans(user_id):
        plans = social.my_plans(user_id, g.user["id"])      # only the public ones, and none from a blocked member
        if i_blocked or blocked_me:
            plans = []
    return render_template("users_list.html", user=user, posts=wall,
                           comments_by_post=comments_by_post, has_more=has_more,
                           next_before=wall[-1]["id"] if has_more else None,
                           i_blocked=i_blocked, blocked_me=blocked_me, tags=social.user_tags(user_id), plans=plans,
                           follower_count=follows.counts(user_id)[0], following_count=follows.counts(user_id)[1],
                           i_follow=bool(g.user) and follows.is_following(g.user["id"], user_id),
                           instruments=taxonomy.INSTRUMENTS, genres=taxonomy.GENRES, goals=taxonomy.GOALS,
                           attendance=social.post_attendance(wall, g.user["id"]) if g.user else {})


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
        ticked = taxonomy.only_valid(request.form.getlist("instruments"), taxonomy.INSTRUMENTS)
        instruments = {key: request.form.get("level_" + key, "") for key in ticked}      # key -> level (or empty)
        music_genres = taxonomy.only_valid(request.form.getlist("genres"), taxonomy.GENRES)
        goals = taxonomy.only_valid(request.form.getlist("goals"), taxonomy.GOALS)
        hide_plans = 1 if request.form.get("hide_plans") == "1" else 0
        notify_friends = 1 if request.form.get("notify_friends_going") == "1" else 0
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
                    " website = :website, kind = :kind, hide_plans = :hide_plans,"
                    " notify_friends_going = :notify_friends" +
                    (", email = :email, email_verified = 0" if change_now else "") +
                    (", bandsintown_artist = :bi_artist, bandsintown_app_id = :bi_app" if bit_changes else "") +
                    " WHERE id = :id",
                    name=name, about=about, location=location or None, website=website or None,
                    kind=kind, email=email, id=g.user["id"], hide_plans=hide_plans, notify_friends=notify_friends,
                    bi_artist=(bit_changes or {}).get("bandsintown_artist"), bi_app=(bit_changes or {}).get("bandsintown_app_id"),
                )
                if bit_changes and not bit_changes["bandsintown_artist"]:
                    execute("DELETE FROM external_events WHERE user_id = :u", u=g.user["id"])   # disconnected: forget its dates
                commit()
                social.save_tags(g.user["id"], instruments, music_genres, goals)
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
    return render_template("edit.html", tags=social.user_tags(g.user["id"]), instruments=taxonomy.INSTRUMENTS,
                           genres=taxonomy.GENRES, goals=taxonomy.GOALS)
