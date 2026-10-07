# File: moderation.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""What every member can do about bad behaviour: report a post, comment or profile, and block a person.

Reports land in the admin queue (admin.py). Blocking is immediate and mutual in effect: neither side sees
the other's posts or comments, and they cannot message each other.
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from . import ratelimit, review
from .auth import login_required
from .baclog import bac_log
from .db import commit, execute, insert, reports, rollback
from .util import now_str, safe_next

bp = Blueprint("moderation", __name__)

REASONS = {
    "spam": "Spam or advertising",
    "abuse": "Harassment, hate or threats",
    "illegal": "Illegal content",
    "other": "Something else",
}
TARGET_TYPES = ("post", "comment", "user", "gigcomment")


def find_target(target_type, target_id):
    """(author id, text snapshot, short label) of the reported thing, or None when it does not exist."""
    if target_type == "post":
        row = execute("SELECT user_id, body, image_filename FROM posts WHERE id = :id", id=target_id).fetchone()
        return None if row is None else (row[0], (row[1] or "") + (" [photo]" if row[2] else ""),
                                         "post #%d" % target_id)
    if target_type == "comment":
        row = execute("SELECT user_id, body FROM comments WHERE id = :id", id=target_id).fetchone()
        return None if row is None else (row[0], row[1], "comment #%d" % target_id)
    if target_type == "gigcomment":
        row = execute("SELECT user_id, body FROM gig_comments WHERE id = :id", id=target_id).fetchone()
        return None if row is None else (row[0], row[1], "gig comment #%d" % target_id)
    if target_type == "user":
        row = execute("SELECT id, about FROM users WHERE id = :id", id=target_id).fetchone()
        return None if row is None else (row[0], row[1], "profile #%d" % target_id)
    return None


@bp.route("/report/<target_type>/<int:target_id>", methods=("GET", "POST"))
@login_required
def report(target_type, target_id):
    if target_type not in TARGET_TYPES:
        abort(404)
    target = find_target(target_type, target_id)
    if target is None:
        abort(404)
    author_id, snapshot, label = target
    next_url = safe_next(request.values.get("next"), url_for("feed.index"))
    if author_id == g.user["id"]:
        flash("You cannot report yourself.", "warning")
        return redirect(next_url)
    if request.method == "POST":
        reason = request.form.get("reason", "")
        details = request.form.get("details", "").strip()[:500]
        if reason not in REASONS:
            flash("Choose a reason.", "danger")
        else:
            ratelimit.allow("report_user", g.user["id"])
            if execute("SELECT 1 FROM reports WHERE reporter_id = :r AND target_type = :t AND target_id = :i"
                       " AND status = 'open'", r=g.user["id"], t=target_type, i=target_id).fetchone() is None:
                insert(reports, reporter_id=g.user["id"], target_type=target_type, target_id=target_id,
                       target_user_id=author_id, reason=reason, details=details, snapshot=snapshot[:1000],
                       status="open", created_at=now_str())
                commit()
                bac_log("report", "user id=%s reported %s (reason=%s)" % (g.user["id"], label, reason))
                review.maybe_autohide(target_type, target_id)
            flash("Thank you. Our moderators will look at it.", "success")
            return redirect(next_url)
    return render_template("report.html", target_type=target_type, target_id=target_id, label=label,
                           snapshot=snapshot, reasons=REASONS, next_url=next_url)


@bp.route("/block/<int:user_id>", methods=("POST",))
@login_required
def block(user_id):
    me = g.user["id"]
    if user_id == me:
        flash("You cannot block yourself.", "warning")
    elif execute("SELECT 1 FROM users WHERE id = :id", id=user_id).fetchone() is None:
        abort(404)
    else:
        try:
            execute("INSERT INTO blocks (blocker_id, blocked_id, created_at) VALUES (:a, :b, :now)",
                    a=me, b=user_id, now=now_str())
            commit()
            execute("DELETE FROM follows WHERE (follower_id = :a AND followed_id = :b) OR (follower_id = :b AND followed_id = :a)",
                    a=me, b=user_id)
            execute("DELETE FROM follow_requests WHERE (follower_id = :a AND followed_id = :b)"
                    " OR (follower_id = :b AND followed_id = :a)", a=me, b=user_id)
            commit()
            bac_log("block", "user id=%s blocked id=%s" % (me, user_id))
        except IntegrityError:
            rollback()  # already blocked: nothing to do
        flash("Blocked. You will not see each other's posts and cannot message each other.", "info")
    return redirect(safe_next(request.form.get("next"), url_for("views.profile", user_id=user_id)))


@bp.route("/unblock/<int:user_id>", methods=("POST",))
@login_required
def unblock(user_id):
    execute("DELETE FROM blocks WHERE blocker_id = :a AND blocked_id = :b", a=g.user["id"], b=user_id)
    commit()
    bac_log("block", "user id=%s unblocked id=%s" % (g.user["id"], user_id))
    flash("Unblocked.", "info")
    return redirect(safe_next(request.form.get("next"), url_for("views.profile", user_id=user_id)))
