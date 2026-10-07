# File: feed.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""The community feed: members post text, photos and gig announcements, like and comment.

Everything here needs a signed-in user. All members see all posts (there is no friends list yet),
except posts of suspended accounts and of members who blocked you or whom you blocked.
"""
import secrets
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, flash, g, redirect, render_template, request,
                   url_for)
from sqlalchemy.exc import IntegrityError

from . import geo, modlog, ratelimit, review, storage, taxonomy
from .auth import login_required, verified_required
from .baclog import bac_log
from .db import commit, comments, execute, insert, posts, rollback
from .util import detect_image_ext, now_str, safe_next

bp = Blueprint("feed", __name__)

PAGE_SIZE = 20              # posts per page
MAX_POST_LENGTH = 5000
MAX_COMMENT_LENGTH = 1000
MAX_IMAGE_BYTES = 5 * 1024 * 1024
GIG_KINDS = ("band", "venue")  # only they can announce gigs
GIG_GRACE_HOURS = 12           # a gig stays on the board until the morning after it starts

# posts/comments of suspended members, and of anyone who blocked me or whom I blocked, are never shown
VISIBLE_AUTHOR = (
    "u.status = 'active'"
    " AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = :me)"
    " AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = :me)"
)

# content (posts, comments, photos, plans) of a PRIVATE member is only for the member and the people they accepted
PRIVATE_OK = ("(u.is_private = 0 OR u.id = :me"
              " OR u.id IN (SELECT followed_id FROM follows WHERE follower_id = :me))")

POST_SELECT = (
    "SELECT p.id, p.body, p.image_filename, p.created_at, p.event_at, p.event_place, p.latitude, p.longitude, p.genre, p.mod_state,"
    " u.id AS author_id, u.username AS author, u.name AS author_name, u.kind AS author_kind,"
    " (SELECT count(*) FROM likes l WHERE l.post_id = p.id) AS like_count,"
    " (SELECT count(*) FROM likes l WHERE l.post_id = p.id AND l.user_id = :me) AS liked_by_me"
    " FROM posts p JOIN users u ON u.id = p.user_id WHERE " + VISIBLE_AUTHOR +
    " AND (p.mod_state = 'ok' OR p.user_id = :me)"      # waiting for a moderator: only its author sees it
    " AND " + PRIVATE_OK                                  # a private member's posts: only for people they accepted
)


def _attach_comments(rows):
    """One query for the comments of every post on the page (not one query per post)."""
    comments_by_post = {r["id"]: [] for r in rows}
    if rows:
        id_params = {"i%d" % n: r["id"] for n, r in enumerate(rows)}
        marks = ", ".join(":" + k for k in id_params)
        for c in execute(
            "SELECT c.id, c.post_id, c.body, c.created_at, c.user_id, c.mod_state, u.username AS author"
            " FROM comments c JOIN users u ON u.id = c.user_id"
            " WHERE c.post_id IN (" + marks + ") AND " + VISIBLE_AUTHOR +
            " AND (c.mod_state = 'ok' OR c.user_id = :me) AND " + PRIVATE_OK + " ORDER BY c.id",
            me=g.user["id"], **id_params
        ).mappings():
            comments_by_post[c["post_id"]].append(c)
    return comments_by_post


def load_posts(user_id=None, before=None, following=False):
    """Newest-first page of posts (optionally one author's), with likes and comments attached.

    `before` is the id of the last post of the previous page (keyset pagination: fast and stable
    even while new posts arrive). Returns (posts, comments_by_post_id, has_more).
    """
    params = {"me": g.user["id"], "lim": PAGE_SIZE + 1}  # +1 row tells us if there is more
    sql = POST_SELECT
    if user_id is not None:
        sql += " AND p.user_id = :uid"
        params["uid"] = user_id
    if following:       # only the people I follow, and me
        sql += " AND (p.user_id = :me OR p.user_id IN (SELECT followed_id FROM follows WHERE follower_id = :me))"
    if before:
        sql += " AND p.id < :before"
        params["before"] = before
    rows = execute(sql + " ORDER BY p.id DESC LIMIT :lim", **params).mappings().fetchall()
    has_more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]
    bac_log("feed", "loaded %d post(s) user_id=%s before=%s has_more=%s"
            % (len(rows), user_id, before, has_more))
    return rows, _attach_comments(rows), has_more


def load_gigs():
    """Announced gigs that have not happened yet, soonest first."""
    since = (datetime.now(timezone.utc) - timedelta(hours=GIG_GRACE_HOURS)).strftime("%Y-%m-%d %H:%M")
    rows = execute(POST_SELECT + " AND p.mod_state = 'ok' AND p.event_at IS NOT NULL AND p.event_at >= :since"
                   " ORDER BY p.event_at, p.id LIMIT 100", me=g.user["id"], since=since).mappings().fetchall()
    bac_log("feed", "loaded %d upcoming gig(s)" % len(rows))
    return rows, _attach_comments(rows)


def _back(post_id=None):
    """Go back to the page the form was on (validated), jumping to the post that changed."""
    target = safe_next(request.form.get("next"), url_for("feed.index"))
    return redirect(target + ("#post-%d" % post_id if post_id else ""))


def remove_image(filename):
    if filename and storage.get().delete(filename):
        bac_log("feed", "image file %s deleted" % filename)


def _get_post_or_404(post_id):
    post = execute("SELECT id, user_id, image_filename, mod_state FROM posts WHERE id = :id",
                   id=post_id).mappings().fetchone()
    if post is not None and post["mod_state"] != "ok" and post["user_id"] != g.user["id"] and not review.is_staff(g.user):
        post = None     # held or hidden: only its author (and staff, through the review queue) can touch it
    if post is None:
        bac_log("feed", "post id=%s not found -> 404" % post_id)
        abort(404)
    return post


def delete_post_rows(post):
    """Remove a post with its likes, comments and photo (used by the author, by admins and on account deletion).
    Does not commit."""
    # children first (foreign keys), then the post itself
    execute("DELETE FROM likes WHERE post_id = :id", id=post["id"])
    execute("DELETE FROM comments WHERE post_id = :id", id=post["id"])
    execute("DELETE FROM posts WHERE id = :id", id=post["id"])


def _is_admin():
    return g.user["role"] == "admin"


def _gig_position(place):
    """Where a gig is: the coordinates sent by the 'use my location' button, else a lookup of the place name.
    (None, None) when neither works; the gig is then simply not part of 'near me' searches."""
    lat = geo.parse_coord(request.form.get("event_lat"), 90)
    lon = geo.parse_coord(request.form.get("event_lon"), 180)
    if lat is not None and lon is not None:
        return round(lat, 5), round(lon, 5)
    if place and not ratelimit.blocked("geocode_user", g.user["id"]):
        ratelimit.hit("geocode_user", g.user["id"])      # lookups go to an outside service: count them
        try:
            found = geo.geocode(place)
        except geo.GeocoderUnavailable:
            found = None            # the gig is still posted; the poster is told it is not on the map
        if found:
            return round(found[0], 5), round(found[1], 5)
    return None, None


@bp.route("/feed")
@login_required
def index():
    from . import social    # imported here: social.py itself imports this module
    following = request.args.get("following") == "1"
    rows, comments_by_post, has_more = load_posts(before=request.args.get("before", type=int), following=following)
    return render_template("feed.html", posts=rows, comments_by_post=comments_by_post, following=following,
                           has_more=has_more, next_before=rows[-1]["id"] if has_more else None,
                           attendance=social.post_attendance(rows, g.user["id"]))


@bp.route("/posts/<int:post_id>")
@login_required
def single_post(post_id):
    """One post on its own page (the link from 'gigs near me' results)."""
    rows = execute(POST_SELECT + " AND p.id = :pid", me=g.user["id"], pid=post_id).mappings().fetchall()
    if not rows:
        abort(404)  # missing, or by someone suspended / who blocked you
    from . import social
    gig = social.gig_info("community", post_id, g.user["id"]) if rows[0]["event_at"] else None
    return render_template("post.html", posts=rows, comments_by_post=_attach_comments(rows),
                           attendance=social.post_attendance(rows, g.user["id"]),
                           panel=social.panel(gig, g.user["id"], request.args) if gig else None)


@bp.route("/gigs")
@login_required
def gigs():
    from . import social
    rows, comments_by_post = load_gigs()
    return render_template("gigs.html", posts=rows, comments_by_post=comments_by_post,
                           attendance=social.post_attendance(rows, g.user["id"]))


@bp.route("/posts", methods=("POST",))
@login_required
@verified_required
def create_post():
    ratelimit.allow("post_user", g.user["id"])
    body = request.form.get("body", "").strip()
    upload = request.files.get("image")
    has_image = upload is not None and bool(upload.filename)

    # a gig announcement carries a date (and optionally a place); only bands and venues may post them
    event_at = event_place = event_lat = event_lon = event_genre = None
    event_raw = request.form.get("event_at", "").strip()
    if event_raw:
        if g.user["kind"] not in GIG_KINDS and not _is_admin():
            flash("Only bands and venues can announce gigs.", "warning")
            return _back()
        try:
            event_at = datetime.strptime(event_raw, "%Y-%m-%dT%H:%M").strftime("%Y-%m-%d %H:%M")
        except ValueError:
            flash("That gig date is not valid.", "danger")
            return _back()
        event_place = request.form.get("event_place", "").strip()[:120] or None
        event_genre = request.form.get("genre") if request.form.get("genre") in taxonomy.GENRES else None
        event_lat, event_lon = _gig_position(event_place)

    if not body and not has_image and not event_at:
        flash("Write something or add a photo first.", "warning")
        return _back()
    if len(body) > MAX_POST_LENGTH:
        flash("Post too long (max %d characters)." % MAX_POST_LENGTH, "danger")
        return _back()

    filename = None
    if has_image:
        data = upload.read(MAX_IMAGE_BYTES + 1)  # read one byte past the limit to detect "too big"
        if len(data) > MAX_IMAGE_BYTES:
            bac_log("feed", "image rejected: bigger than %d bytes" % MAX_IMAGE_BYTES)
            flash("That photo is too big (max 5 MB).", "danger")
            return _back()
        ext = detect_image_ext(data[:16])
        if ext is None:
            bac_log("feed", "image rejected: content is not jpg/png/gif/webp")
            flash("Only JPG, PNG, GIF or WEBP photos are allowed.", "danger")
            return _back()
        # random name: nobody can guess it and the uploaded name (which could be malicious) is dropped
        filename = secrets.token_hex(16) + ext
        storage.get().save(filename, data)

    held = review.screen(g.user, "%s %s" % (body, event_place or ""), "post", bool(filename))
    try:
        post_id = insert(posts, user_id=g.user["id"], body=body, image_filename=filename,
                         created_at=now_str(), event_at=event_at, event_place=event_place,
                         latitude=event_lat, longitude=event_lon, genre=event_genre,
                         mod_state="held" if held else "ok")
        if held:
            review.enqueue("post", post_id, g.user["id"], held, body or "(photo or gig announcement)")
        commit()
    except Exception:
        rollback()
        remove_image(filename)  # do not leave an orphan file behind
        raise
    bac_log("feed", "post id=%s created by %r (%d chars, image=%s, gig=%s)"
            % (post_id, g.user["username"], len(body), bool(filename), bool(event_at)))
    if held:
        flash("Thanks! Your post is waiting for a quick check by a moderator. Until then only you can see it.", "info")
    elif event_at and event_lat is None:
        flash("Gig announced! We could not place it on the map, so it will not appear in 'gigs near me' searches. "
              "Post it from the venue with the location button to fix that.", "warning")
    else:
        flash("Gig announced!" if event_at else "Posted!", "success")
    return _back(post_id)


@bp.route("/posts/<int:post_id>/delete", methods=("POST",))
@login_required
def delete_post(post_id):
    post = _get_post_or_404(post_id)
    own = post["user_id"] == g.user["id"]
    if not own and not _is_admin():
        bac_log("feed", "user id=%s tried to delete post %s of someone else -> 403"
                % (g.user["id"], post_id))
        abort(403)
    delete_post_rows(post)
    if not own:
        modlog.record("remove_post", "post %d" % post_id, "author id=%s" % post["user_id"])
    commit()
    remove_image(post["image_filename"])
    bac_log("feed", "post id=%s deleted by %r" % (post_id, g.user["username"]))
    flash("Post deleted.", "info")
    return _back()


@bp.route("/posts/<int:post_id>/like", methods=("POST",))
@login_required
def toggle_like(post_id):
    _get_post_or_404(post_id)
    me = g.user["id"]
    already = execute("SELECT 1 FROM likes WHERE post_id = :p AND user_id = :u",
                      p=post_id, u=me).fetchone()
    if already:
        execute("DELETE FROM likes WHERE post_id = :p AND user_id = :u", p=post_id, u=me)
        commit()
        bac_log("feed", "user id=%s UNliked post %s" % (me, post_id))
    else:
        try:
            execute("INSERT INTO likes (post_id, user_id, created_at) VALUES (:p, :u, :now)",
                    p=post_id, u=me, now=now_str())
            commit()
            bac_log("feed", "user id=%s liked post %s" % (me, post_id))
        except IntegrityError:
            rollback()  # double click: the like already exists, which is the state we wanted
    return _back(post_id)


@bp.route("/posts/<int:post_id>/comments", methods=("POST",))
@login_required
@verified_required
def add_comment(post_id):
    _get_post_or_404(post_id)
    ratelimit.allow("comment_user", g.user["id"])
    body = request.form.get("body", "").strip()
    if not body:
        flash("Write a comment first.", "warning")
    elif len(body) > MAX_COMMENT_LENGTH:
        flash("Comment too long (max %d characters)." % MAX_COMMENT_LENGTH, "danger")
    else:
        held = review.screen(g.user, body, "comment")
        comment_id = insert(comments, post_id=post_id, user_id=g.user["id"], body=body,
                            created_at=now_str(), mod_state="held" if held else "ok")
        if held:
            review.enqueue("comment", comment_id, g.user["id"], held, body)
        commit()
        if held:
            flash("Thanks! Your comment is waiting for a quick check by a moderator.", "info")
        bac_log("feed", "comment id=%s (%d chars) by %r on post %s"
                % (comment_id, len(body), g.user["username"], post_id))
    return _back(post_id)


@bp.route("/comments/<int:comment_id>/delete", methods=("POST",))
@login_required
def delete_comment(comment_id):
    row = execute(
        "SELECT c.id, c.post_id, c.user_id, p.user_id AS post_owner_id"
        " FROM comments c JOIN posts p ON p.id = c.post_id WHERE c.id = :id", id=comment_id
    ).mappings().fetchone()
    if row is None:
        abort(404)
    # the comment's author and the owner of the post may remove it, and so may an admin
    if g.user["id"] not in (row["user_id"], row["post_owner_id"]) and not _is_admin():
        bac_log("feed", "user id=%s not allowed to delete comment %s -> 403" % (g.user["id"], comment_id))
        abort(403)
    execute("DELETE FROM comments WHERE id = :id", id=comment_id)
    if g.user["id"] not in (row["user_id"], row["post_owner_id"]):
        modlog.record("remove_comment", "comment %d" % comment_id, "author id=%s" % row["user_id"])
    commit()
    bac_log("feed", "comment id=%s deleted by id=%s" % (comment_id, g.user["id"]))
    return _back(row["post_id"])


@bp.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    # the photo of a private member's post is only for the people they accepted (the file name is unguessable, but a copied
    # link must not work for everyone)
    if execute("SELECT 1 FROM posts p JOIN users u ON u.id = p.user_id WHERE p.image_filename = :f AND u.is_private = 1"
               " AND u.id <> :me AND u.id NOT IN (SELECT followed_id FROM follows WHERE follower_id = :me)",
               f=filename, me=g.user["id"]).fetchone():
        abort(404)
    response = storage.get().response(filename)
    response.cache_control.private = True  # photos are for signed-in members only
    response.cache_control.public = False
    return response
