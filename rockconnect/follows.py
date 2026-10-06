# File: follows.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Following people: see their gigs in a "Following" feed, and be told when they go to a gig.

Following needs no approval (their plans are already visible to members, and a member who does not want to be
followed can block). Rules: not yourself, not a suspended member, not someone who blocked you or whom you blocked
(blocking removes follows both ways), and not across the minor / adult line if the operator lets minors join.

Pages:  POST /follow/<id>   POST /unfollow/<id>
API:    POST /api/v1/people/<id>/follow   POST /api/v1/people/<id>/unfollow
        GET  /api/v1/me/following          GET  /api/v1/me/followers
"""
from flask import Blueprint, abort, flash, g, jsonify, redirect, request, url_for
from sqlalchemy.exc import IntegrityError

from . import ages, notifications, ratelimit, social
from .api import ApiError, api_login_required
from .auth import login_required
from .baclog import bac_log
from .db import commit, execute, follows, insert, rollback
from .util import now_str, safe_next

bp = Blueprint("follows", __name__)
LIST_LIMIT = 200


def counts(user_id):
    """(followers, following) of a member, not counting suspended accounts."""
    followers = execute("SELECT count(*) FROM follows f JOIN users u ON u.id = f.follower_id"
                        " WHERE f.followed_id = :u AND u.status = 'active'", u=user_id).scalar() or 0
    following = execute("SELECT count(*) FROM follows f JOIN users u ON u.id = f.followed_id"
                        " WHERE f.follower_id = :u AND u.status = 'active'", u=user_id).scalar() or 0
    return followers, following


def is_following(follower_id, followed_id):
    return execute("SELECT 1 FROM follows WHERE follower_id = :a AND followed_id = :b",
                   a=follower_id, b=followed_id).fetchone() is not None


def why_not(me, target_id):
    """Why `me` cannot follow this member (text), or None when it is allowed."""
    if target_id == me["id"]:
        return "You cannot follow yourself."
    target = execute("SELECT id, status, birth_date FROM users WHERE id = :id", id=target_id).mappings().fetchone()
    if target is None:
        return "not_found"
    if target["status"] != "active":
        return "You cannot follow this person."
    if execute("SELECT 1 FROM blocks WHERE (blocker_id = :a AND blocked_id = :b) OR (blocker_id = :b AND blocked_id = :a)",
               a=me["id"], b=target_id).fetchone() or not ages.same_side(me["birth_date"], target["birth_date"]):
        return "You cannot follow this person."
    return None


def follow(me, target_id):
    """Start following. Returns True if this was new."""
    try:
        insert(follows, follower_id=me["id"], followed_id=target_id, created_at=now_str())
        commit()
    except IntegrityError:
        rollback()
        return False        # already following
    notifications.new_follower(me, target_id)
    bac_log("follows", "user id=%s follows id=%s" % (me["id"], target_id))
    return True


def unfollow(me_id, target_id):
    execute("DELETE FROM follows WHERE follower_id = :a AND followed_id = :b", a=me_id, b=target_id)
    commit()


def people(user_id, direction, viewer):
    """The members `user_id` follows ('following') or who follow them ('followers'), as in people search."""
    mine, theirs = ("follower_id", "followed_id") if direction == "following" else ("followed_id", "follower_id")
    side, side_params = ages.side_clause(viewer)
    rows = execute(
        "SELECT u.id, u.username, u.name, u.kind, u.location, u.status FROM follows f JOIN users u ON u.id = f.%s"
        " WHERE f.%s = :u AND u.status = 'active'"
        " AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = :me)"
        " AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = :me)" % (theirs, mine) + side +
        " ORDER BY f.created_at DESC LIMIT :lim", u=user_id, me=viewer["id"], lim=LIST_LIMIT, **side_params
    ).mappings().fetchall()
    tags = social._tags_for([r["id"] for r in rows])
    followed = social.followed_ids(viewer["id"])
    return [dict(social.person(r, tags[r["id"]]), following=r["id"] in followed) for r in rows]


# ------------------------------------------------------------------ pages
def _change(target_id, wanted):
    ratelimit.allow("follow_user", g.user["id"])
    if wanted:
        problem = why_not(g.user, target_id)
        if problem == "not_found":
            return None, "That member does not exist."
        if problem:
            return None, problem
        follow(g.user, target_id)
    else:
        unfollow(g.user["id"], target_id)
    return counts(target_id)[0], None


@bp.route("/follow/<int:user_id>", methods=("POST",))
@login_required
def follow_page(user_id):
    followers, problem = _change(user_id, True)
    if problem == "That member does not exist.":
        abort(404)
    if problem:
        flash(problem, "warning")
    return redirect(safe_next(request.form.get("next"), url_for("views.profile", user_id=user_id)))


@bp.route("/unfollow/<int:user_id>", methods=("POST",))
@login_required
def unfollow_page(user_id):
    _change(user_id, False)
    return redirect(safe_next(request.form.get("next"), url_for("views.profile", user_id=user_id)))


# ------------------------------------------------------------------ API v1
@bp.route("/api/v1/people/<int:user_id>/follow", methods=("POST",))
@api_login_required
def api_follow(user_id):
    followers, problem = _change(user_id, True)
    if problem == "That member does not exist.":
        raise ApiError(problem, "not_found", 404)
    if problem:
        raise ApiError(problem, "cannot_follow", 403)
    return jsonify(following=True, followers=followers)


@bp.route("/api/v1/people/<int:user_id>/unfollow", methods=("POST",))
@api_login_required
def api_unfollow(user_id):
    followers, _ = _change(user_id, False)
    return jsonify(following=False, followers=followers)


@bp.route("/api/v1/me/following")
@api_login_required
def api_following():
    found = people(g.user["id"], "following", g.user)
    return jsonify(total=len(found), people=found)


@bp.route("/api/v1/me/followers")
@api_login_required
def api_followers():
    found = people(g.user["id"], "followers", g.user)
    return jsonify(total=len(found), people=found)
