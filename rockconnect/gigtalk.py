# File: gigtalk.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""The discussion under a gig: "who has a spare ticket?", "anyone driving from Dublin?", "doors are at 7".

One thread per concert, whichever listing you opened (the same matching as "who is going"). Authors who are
suspended, or blocked either way, or on the other side of 18, are not shown. Members delete their own comments, admins
any; anyone can report one. Like an RSVP, a comment keeps a small snapshot of the gig so the thread stays findable,
and it is deleted with the gig (a day after an imported gig, a month after a member's own).

Pages:  POST /gigs/comment   POST /gigs/comments/<id>/delete
API:    GET/POST /api/v1/gigs/<source>/<ref>/comments      POST /api/v1/gig-comments/<id>/delete
"""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, abort, current_app, flash, g, jsonify, redirect, request, url_for

from . import ages, modlog, ratelimit, review, social
from .api import ApiError, api_login_required
from .auth import login_required, verified_required
from .baclog import bac_log
from .db import commit, execute, gig_comments, insert
from .feed import VISIBLE_AUTHOR
from .util import now_str, safe_next

bp = Blueprint("gigtalk", __name__)

MAX_LENGTH = 1000
SHOWN = 100
GRACE_DAYS = 7       # comments are welcome until a week after the gig


def comment_json(row, viewer):
    return {"id": row["id"], "body": row["body"], "created_at": row["created_at"],
            "mine": row["user_id"] == viewer["id"], "pending": row["mod_state"] != "ok",
            "author": {"id": row["user_id"], "username": row["username"], "name": row["name"], "kind": row["kind"]}}


def thread(gig, viewer):
    """The comments of this concert, oldest first (the last SHOWN), as dictionaries."""
    near, near_params = social._near(gig)
    side, side_params = ages.side_clause(viewer)
    rows = execute(
        "SELECT a.id, a.user_id, a.body, a.created_at, a.mod_state, a.source, a.event_ref, a.title, a.event_at, a.latitude, a.longitude,"
        " u.username, u.name, u.kind FROM gig_comments a JOIN users u ON u.id = a.user_id"
        " WHERE substr(a.event_at, 1, 10) = :day AND " + VISIBLE_AUTHOR + side + " AND" + near +
        " AND (a.mod_state = 'ok' OR a.user_id = :me) ORDER BY a.id",
        me=viewer["id"], day=gig["event_at"][:10], **near_params, **side_params).mappings().fetchall()
    mine = [r for r in rows if social._matches(r, gig)][-SHOWN:]
    return [comment_json(r, viewer) for r in mine]


def _too_late(event_at):
    limit = (datetime.now(timezone.utc) - timedelta(days=GRACE_DAYS)).strftime("%Y-%m-%d %H:%M")
    return event_at < limit


def add(viewer, gig, body):
    """Store a comment; returns (id, held). `held` is True when it waits for a moderator. Raises social.SocialError."""
    body = (body or "").strip()
    if not body:
        raise social.SocialError("Write something first.", "empty")
    if len(body) > MAX_LENGTH:
        raise social.SocialError("That comment is too long (max %d characters)." % MAX_LENGTH, "too_long")
    if _too_late(gig["event_at"]):
        raise social.SocialError("That gig is long over, the discussion is closed.", "closed")
    ratelimit.allow("gigtalk_user", viewer["id"])
    held = review.screen(viewer, body, "gigcomment")
    new_id = insert(gig_comments, user_id=viewer["id"], source=gig["source"], event_ref=gig["ref"], title=gig["title"][:255],
                    event_at=gig["event_at"], latitude=gig["latitude"], longitude=gig["longitude"], body=body,
                    created_at=now_str(), mod_state="held" if held else "ok")
    if held:
        review.enqueue("gigcomment", new_id, viewer["id"], held, body)
    commit()
    bac_log("gigtalk", "comment id=%s (%d chars) by user id=%s held=%s" % (new_id, len(body), viewer["id"], bool(held)))
    return new_id, bool(held)


def remove(comment_id, actor):
    """Delete a comment if the actor wrote it or is an admin. Returns True if it was deleted."""
    row = execute("SELECT id, user_id FROM gig_comments WHERE id = :id", id=comment_id).mappings().fetchone()
    if row is None:
        return None
    if row["user_id"] != actor["id"] and actor["role"] != "admin":
        return False
    execute("DELETE FROM gig_comments WHERE id = :id", id=comment_id)
    if row["user_id"] != actor["id"]:
        modlog.record("remove_gig_comment", "gig comment %d" % comment_id, "author id=%s" % row["user_id"])
    commit()
    return True


def _allowed_to_write():
    cfg = current_app.config
    return not (cfg["REQUIRE_EMAIL_VERIFICATION"] and not g.user["email_verified"])


# ------------------------------------------------------------------ pages
@bp.route("/gigs/comment", methods=("POST",))
@login_required
@verified_required
def comment():
    nxt = safe_next(request.form.get("next"), url_for("feed.gigs"))
    gig = social.gig_info(request.form.get("source", ""), request.form.get("ref", ""), g.user["id"])
    if gig is None:
        abort(404)
    try:
        _, held = add(g.user, gig, request.form.get("body"))
        flash("Thanks! Your comment is waiting for a quick check by a moderator." if held else "Comment added.",
              "info" if held else "success")
    except social.SocialError as exc:
        flash(exc.message, "warning")
    return redirect(nxt.split("#")[0] + "#talk")


@bp.route("/gigs/comments/<int:comment_id>/delete", methods=("POST",))
@login_required
def delete(comment_id):
    done = remove(comment_id, g.user)
    if done is None:
        abort(404)
    if done is False:
        abort(403)
    flash("Comment deleted.", "info")
    return redirect(safe_next(request.form.get("next"), url_for("feed.gigs")).split("#")[0] + "#talk")


# ------------------------------------------------------------------ API v1
@bp.route("/api/v1/gigs/<source>/<ref>/comments", methods=("GET", "POST"))
@api_login_required
def api_comments(source, ref):
    gig = social.gig_info(source, ref, g.user["id"])
    if gig is None:
        raise ApiError("That gig does not exist (any more).", "gig_not_found", 404)
    if request.method == "POST":
        if not _allowed_to_write():
            raise ApiError("Please confirm your email address first.", "email_not_confirmed", 403)
        data = request.form if request.form else (request.get_json(silent=True) or {})
        try:
            new_id, held = add(g.user, gig, data.get("body"))
        except social.SocialError as exc:
            raise ApiError(exc.message, exc.code, exc.status)
        return jsonify(id=new_id, held=held), 201
    found = thread(gig, g.user)
    return jsonify(total=len(found), comments=found)


@bp.route("/api/v1/gig-comments/<int:comment_id>/delete", methods=("POST",))
@api_login_required
def api_delete(comment_id):
    done = remove(comment_id, g.user)
    if done is None:
        raise ApiError("No such comment.", "not_found", 404)
    if done is False:
        raise ApiError("You can only delete your own comments.", "forbidden", 403)
    return jsonify(deleted=True)
