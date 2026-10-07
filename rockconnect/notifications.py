# File: notifications.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Notifications in the app (the bell): someone followed you, or someone you follow is going to a gig.

Rows in the `notifications` table; the page, the API and (later) a push sender for the Android app all read the same
rows. The same event never notifies the same person twice (dedupe_key), blocked or suspended members never notify
anyone, and people who switched "tell me when people I follow go to gigs" off are skipped.

    GET  /notifications            the list (opening it marks everything read)
    POST /notifications/read       mark everything read
    GET  /api/v1/notifications     ?unread=1&limit=50       POST /api/v1/notifications/read
"""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from . import ages
from .api import api_login_required
from .auth import login_required
from .baclog import bac_log
from .db import commit, execute, notifications, insert, rollback
from .util import event_time, now_str, safe_next

bp = Blueprint("notifications", __name__)

MAX_PER_EVENT = 200      # an RSVP of someone with a huge following does not write thousands of rows
KEEP_READ_DAYS, KEEP_ANY_DAYS = 30, 90


def notify(user_id, kind, text, url, dedupe_key):
    """Add one notification. Returns False when this person was already told about this event."""
    try:
        insert(notifications, user_id=user_id, kind=kind, text=text[:255], url=(url or "")[:255] or None,
               dedupe_key=dedupe_key[:120], created_at=now_str())
        commit()
        return True
    except IntegrityError:
        rollback()
        return False


def new_follower(follower, followed_id):
    notify(followed_id, "follow", "%s started following you." % follower["name"],
           url_for("views.profile", user_id=follower["id"]), "follow:%d" % follower["id"])


def follow_request(follower, target_id):
    notify(target_id, "follow_request", "%s asked to follow you." % follower["name"],
           url_for("follows.requests_page"), "followreq:%d" % follower["id"])


def follow_accepted(owner, requester_id):
    notify(requester_id, "follow_accepted", "%s accepted your follow request." % owner["name"],
           url_for("views.profile", user_id=owner["id"]), "followok:%d" % owner["id"])


def friend_going(actor, gig):
    """Tell the followers of `actor` that they are going to `gig`. Returns how many were told."""
    if actor["hide_plans"]:
        return 0
    rows = execute(
        "SELECT u.id, u.birth_date FROM follows f JOIN users u ON u.id = f.follower_id"
        " WHERE f.followed_id = :actor AND u.status = 'active' AND u.notify_friends_going = 1"
        " AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = :actor)"
        " AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = :actor) LIMIT :cap",
        actor=actor["id"], cap=MAX_PER_EVENT).fetchall()
    text = "%s is going to %s on %s." % (actor["name"], gig["title"], event_time(gig["event_at"]))
    told = 0
    for follower_id, birth_date in rows:
        if ages.same_side(actor["birth_date"], birth_date) and notify(
                follower_id, "friend_going", text, gig.get("url"), "going:%d:%s:%s" % (actor["id"], gig["source"], gig["ref"])):
            told += 1
    bac_log("notify", "%d follower(s) told that user id=%s is going to a gig" % (told, actor["id"]))
    return told


def unread_count(user_id):
    return execute("SELECT count(*) FROM notifications WHERE user_id = :u AND read_at IS NULL", u=user_id).scalar() or 0


def latest(user_id, limit=50, unread_only=False):
    sql = "SELECT * FROM notifications WHERE user_id = :u" + (" AND read_at IS NULL" if unread_only else "")
    return execute(sql + " ORDER BY id DESC LIMIT :lim", u=user_id, lim=limit).mappings().fetchall()


def mark_all_read(user_id):
    execute("UPDATE notifications SET read_at = :now WHERE user_id = :u AND read_at IS NULL", now=now_str(), u=user_id)
    commit()


def prune():
    """Read notifications older than 30 days, and any older than 90, are deleted."""
    now = datetime.now(timezone.utc)
    stamp = lambda days: (now - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: E731
    execute("DELETE FROM notifications WHERE (read_at IS NOT NULL AND created_at < :a) OR created_at < :b",
            a=stamp(KEEP_READ_DAYS), b=stamp(KEEP_ANY_DAYS))
    commit()


def as_json(row):
    return {"id": row["id"], "kind": row["kind"], "text": row["text"], "url": row["url"],
            "created_at": row["created_at"], "read": row["read_at"] is not None}


@bp.app_context_processor
def inject_notification_count():
    """The number on the bell, in every page of a signed-in member."""
    user = getattr(g, "user", None)
    return {"notification_count": unread_count(user["id"]) if user else 0}


@bp.route("/notifications")
@login_required
def index():
    rows = latest(g.user["id"], 100)
    shown = [dict(r) for r in rows]
    mark_all_read(g.user["id"])          # opening the list is reading it (the dots in the page still show what was new)
    return render_template("notifications.html", notifications=shown)


@bp.route("/notifications/read", methods=("POST",))
@login_required
def read():
    mark_all_read(g.user["id"])
    return redirect(safe_next(request.form.get("next"), url_for("notifications.index")))


@bp.route("/api/v1/notifications")
@api_login_required
def api_list():
    limit = max(1, min(100, request.args.get("limit", 50, type=int)))
    rows = latest(g.user["id"], limit, request.args.get("unread") == "1")
    return jsonify(unread=unread_count(g.user["id"]), count=len(rows), notifications=[as_json(r) for r in rows])


@bp.route("/api/v1/notifications/read", methods=("POST",))
@api_login_required
def api_read():
    mark_all_read(g.user["id"])
    return jsonify(unread=0)
