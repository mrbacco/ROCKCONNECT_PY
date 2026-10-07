# File: follows.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Following people: see their gigs in a "Following" feed, and be told when they go to a gig.

Following a PUBLIC member needs no approval. A member can make their account PRIVATE (Edit profile): they stay findable (name,
picture, bio, what they play) but their posts, photos, comments and plans are only for the members they accepted, so following
them becomes a request they accept or decline (declining is silent). `follows` only ever holds accepted follows, so everything
that reads it (feed, alerts, "going" notices) respects privacy without extra checks; pending requests live in `follow_requests`.

Rules for any follow or request: not yourself, not a suspended member, not someone who blocked you or whom you blocked
(blocking removes follows and requests both ways), and not across the minor / adult line if the operator lets minors join.

Pages:  POST /follow/<id>   POST /unfollow/<id> (also cancels a request)
        GET  /follow-requests   POST /follow-requests/<id>/accept   POST /follow-requests/<id>/decline
API:    POST /api/v1/people/<id>/follow   POST /api/v1/people/<id>/unfollow
        GET  /api/v1/me/following   GET /api/v1/me/followers   GET /api/v1/me/follow-requests
        POST /api/v1/follow-requests/<id>/accept   POST /api/v1/follow-requests/<id>/decline
"""
from flask import Blueprint, abort, flash, g, jsonify, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from . import ages, notifications, ratelimit, social
from .api import ApiError, api_login_required
from .auth import login_required
from .baclog import bac_log
from .db import commit, execute, follow_requests, follows, insert, rollback
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


def is_requested(follower_id, followed_id):
    return execute("SELECT 1 FROM follow_requests WHERE follower_id = :a AND followed_id = :b",
                   a=follower_id, b=followed_id).fetchone() is not None


def request_count(user_id):
    """How many follow requests wait for this member (the number on the menu)."""
    return execute("SELECT count(*) FROM follow_requests r JOIN users u ON u.id = r.follower_id"
                   " WHERE r.followed_id = :u AND u.status = 'active'", u=user_id).scalar() or 0


@bp.app_context_processor
def inject_request_count():
    user = getattr(g, "user", None)
    return {"follow_request_count": request_count(user["id"]) if user else 0}


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
    """Follow a member, or ask to when their account is private. Returns "following", "requested" or "already"."""
    if is_following(me["id"], target_id):
        return "already"
    private = execute("SELECT is_private FROM users WHERE id = :id", id=target_id).scalar()
    if private:
        try:
            insert(follow_requests, follower_id=me["id"], followed_id=target_id, created_at=now_str())
            commit()
        except IntegrityError:
            rollback()
            return "requested"          # asked before: nothing new to tell them
        notifications.follow_request(me, target_id)
        bac_log("follows", "user id=%s asked to follow private id=%s" % (me["id"], target_id))
        return "requested"
    try:
        insert(follows, follower_id=me["id"], followed_id=target_id, created_at=now_str())
        commit()
    except IntegrityError:
        rollback()
        return "already"
    notifications.new_follower(me, target_id)
    bac_log("follows", "user id=%s follows id=%s" % (me["id"], target_id))
    return "following"


def unfollow(me_id, target_id):
    """Stop following, or withdraw a request that was not answered yet."""
    execute("DELETE FROM follows WHERE follower_id = :a AND followed_id = :b", a=me_id, b=target_id)
    execute("DELETE FROM follow_requests WHERE follower_id = :a AND followed_id = :b", a=me_id, b=target_id)
    commit()


def pending(user_id):
    """The members waiting for this member to accept them, oldest first, as in people search."""
    rows = execute(
        "SELECT u.id, u.username, u.name, u.kind, u.location, u.status, u.is_private, r.created_at AS asked_at"
        " FROM follow_requests r JOIN users u ON u.id = r.follower_id WHERE r.followed_id = :u AND u.status = 'active'"
        " AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = :u)"
        " AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = :u) ORDER BY r.created_at LIMIT :lim",
        u=user_id, lim=LIST_LIMIT).mappings().fetchall()
    tags = social._tags_for([r["id"] for r in rows])
    return [dict(social.person(r, tags[r["id"]]), asked_at=r["asked_at"]) for r in rows]


def _let_in(owner, requester_id):
    """Turn one request into a follow. Returns False when there was no such request."""
    if not is_requested(requester_id, owner["id"]):
        return False
    execute("DELETE FROM follow_requests WHERE follower_id = :a AND followed_id = :b", a=requester_id, b=owner["id"])
    try:
        insert(follows, follower_id=requester_id, followed_id=owner["id"], created_at=now_str())
    except IntegrityError:
        rollback()
        execute("DELETE FROM follow_requests WHERE follower_id = :a AND followed_id = :b", a=requester_id, b=owner["id"])
    commit()
    notifications.follow_accepted(owner, requester_id)
    return True


def accept(owner, requester_id):
    """The owner says yes. Refused (False) when there is no request, or the two may not follow each other any more."""
    requester = {"id": requester_id, "birth_date": execute("SELECT birth_date FROM users WHERE id = :id", id=requester_id).scalar()}
    if why_not(requester, owner["id"]) is not None:
        execute("DELETE FROM follow_requests WHERE follower_id = :a AND followed_id = :b", a=requester_id, b=owner["id"])
        commit()
        return False
    done = _let_in(owner, requester_id)
    if done:
        bac_log("follows", "user id=%s accepted the request of id=%s" % (owner["id"], requester_id))
    return done


def decline(owner, requester_id):
    """The owner says no; the other member is not told."""
    had = is_requested(requester_id, owner["id"])
    execute("DELETE FROM follow_requests WHERE follower_id = :a AND followed_id = :b", a=requester_id, b=owner["id"])
    commit()
    return had


def accept_all(owner):
    """The account stopped being private: everyone who was waiting is let in. Returns how many."""
    rows = execute("SELECT follower_id FROM follow_requests WHERE followed_id = :u", u=owner["id"]).fetchall()
    return sum(1 for (requester_id,) in rows if accept(owner, requester_id))


def people(user_id, direction, viewer):
    """The members `user_id` follows ('following') or who follow them ('followers'), as in people search."""
    mine, theirs = ("follower_id", "followed_id") if direction == "following" else ("followed_id", "follower_id")
    side, side_params = ages.side_clause(viewer)
    rows = execute(
        "SELECT u.id, u.username, u.name, u.kind, u.location, u.status, u.is_private FROM follows f JOIN users u ON u.id = f.%s"
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
    """Follow / ask / unfollow. Returns (state, followers, problem); state is "following", "requested" or "none"."""
    ratelimit.allow("follow_user", g.user["id"])
    if wanted:
        problem = why_not(g.user, target_id)
        if problem == "not_found":
            return None, None, "That member does not exist."
        if problem:
            return None, None, problem
        state = follow(g.user, target_id)
        return ("requested" if state == "requested" else "following"), counts(target_id)[0], None
    unfollow(g.user["id"], target_id)
    return "none", counts(target_id)[0], None


@bp.route("/follow/<int:user_id>", methods=("POST",))
@login_required
def follow_page(user_id):
    state, followers, problem = _change(user_id, True)
    if problem == "That member does not exist.":
        abort(404)
    if problem:
        flash(problem, "warning")
    elif state == "requested":
        flash("Follow request sent. You will see their posts once they accept.", "info")
    return redirect(safe_next(request.form.get("next"), url_for("views.profile", user_id=user_id)))


@bp.route("/unfollow/<int:user_id>", methods=("POST",))
@login_required
def unfollow_page(user_id):
    _change(user_id, False)
    return redirect(safe_next(request.form.get("next"), url_for("views.profile", user_id=user_id)))


@bp.route("/follow-requests")
@login_required
def requests_page():
    return render_template("follow_requests.html", waiting=pending(g.user["id"]))


def _answer(requester_id, yes):
    ratelimit.allow("follow_user", g.user["id"])
    done = accept(g.user, requester_id) if yes else decline(g.user, requester_id)
    if not done:
        flash("That request is not there any more.", "info")
    else:
        flash("Accepted: they can see your posts now." if yes else "Request removed.", "success" if yes else "info")
    return redirect(url_for("follows.requests_page"))


@bp.route("/follow-requests/<int:user_id>/accept", methods=("POST",))
@login_required
def accept_page(user_id):
    return _answer(user_id, True)


@bp.route("/follow-requests/<int:user_id>/decline", methods=("POST",))
@login_required
def decline_page(user_id):
    return _answer(user_id, False)


# ------------------------------------------------------------------ API v1
@bp.route("/api/v1/people/<int:user_id>/follow", methods=("POST",))
@api_login_required
def api_follow(user_id):
    state, followers, problem = _change(user_id, True)
    if problem == "That member does not exist.":
        raise ApiError(problem, "not_found", 404)
    if problem:
        raise ApiError(problem, "cannot_follow", 403)
    return jsonify(following=state == "following", requested=state == "requested", followers=followers)


@bp.route("/api/v1/people/<int:user_id>/unfollow", methods=("POST",))
@api_login_required
def api_unfollow(user_id):
    state, followers, _ = _change(user_id, False)
    return jsonify(following=False, requested=False, followers=followers)


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


@bp.route("/api/v1/me/follow-requests")
@api_login_required
def api_requests():
    found = pending(g.user["id"])
    return jsonify(total=len(found), people=found)


@bp.route("/api/v1/follow-requests/<int:user_id>/accept", methods=("POST",))
@api_login_required
def api_accept(user_id):
    ratelimit.allow("follow_user", g.user["id"])
    if not accept(g.user, user_id):
        raise ApiError("There is no such request.", "not_found", 404)
    return jsonify(accepted=True)


@bp.route("/api/v1/follow-requests/<int:user_id>/decline", methods=("POST",))
@api_login_required
def api_decline(user_id):
    ratelimit.allow("follow_user", g.user["id"])
    if not decline(g.user, user_id):
        raise ApiError("There is no such request.", "not_found", 404)
    return jsonify(declined=True)
