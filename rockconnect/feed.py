# File: feed.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Facebook-style feed: users post text and photos, like and comment on each other's posts.

Everything here needs a signed-in user. All members see all posts (there is no friends list yet).
"""
import os
import secrets

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, send_from_directory, url_for)
from sqlalchemy.exc import IntegrityError

from .auth import login_required
from .baclog import bac_log
from .db import commit, comments, execute, insert, likes, posts, rollback
from .util import detect_image_ext, now_str, safe_next

bp = Blueprint("feed", __name__)

PAGE_SIZE = 20              # posts per page
MAX_POST_LENGTH = 5000
MAX_COMMENT_LENGTH = 1000
MAX_IMAGE_BYTES = 5 * 1024 * 1024


def load_posts(user_id=None, before=None):
    """Newest-first page of posts (optionally one author's), with likes and comments attached.

    `before` is the id of the last post of the previous page (keyset pagination: fast and stable
    even while new posts arrive). Returns (posts, comments_by_post_id, has_more).
    """
    where, params = [], {"me": g.user["id"], "lim": PAGE_SIZE + 1}  # +1 row tells us if there is more
    if user_id is not None:
        where.append("p.user_id = :uid")
        params["uid"] = user_id
    if before:
        where.append("p.id < :before")
        params["before"] = before
    rows = execute(
        "SELECT p.id, p.body, p.image_filename, p.created_at,"
        " u.id AS author_id, u.username AS author, u.name AS author_name,"
        " (SELECT count(*) FROM likes l WHERE l.post_id = p.id) AS like_count,"
        " (SELECT count(*) FROM likes l WHERE l.post_id = p.id AND l.user_id = :me) AS liked_by_me"
        " FROM posts p JOIN users u ON u.id = p.user_id"
        + (" WHERE " + " AND ".join(where) if where else "") +
        " ORDER BY p.id DESC LIMIT :lim", **params
    ).mappings().fetchall()
    has_more = len(rows) > PAGE_SIZE
    rows = rows[:PAGE_SIZE]

    # one query for the comments of every post on the page (not one query per post)
    comments_by_post = {r["id"]: [] for r in rows}
    if rows:
        id_params = {"i%d" % n: r["id"] for n, r in enumerate(rows)}
        marks = ", ".join(":" + k for k in id_params)
        for c in execute(
            "SELECT c.id, c.post_id, c.body, c.created_at, c.user_id, u.username AS author"
            " FROM comments c JOIN users u ON u.id = c.user_id"
            " WHERE c.post_id IN (" + marks + ") ORDER BY c.id", **id_params
        ).mappings():
            comments_by_post[c["post_id"]].append(c)
    bac_log("feed", "loaded %d post(s) user_id=%s before=%s has_more=%s"
            % (len(rows), user_id, before, has_more))
    return rows, comments_by_post, has_more


def _back(post_id=None):
    """Go back to the page the form was on (validated), jumping to the post that changed."""
    target = safe_next(request.form.get("next"), url_for("feed.index"))
    return redirect(target + ("#post-%d" % post_id if post_id else ""))


def _upload_dir():
    return current_app.config["UPLOAD_DIR"]


def _remove_image(filename):
    if filename:
        try:
            os.remove(os.path.join(_upload_dir(), filename))
            bac_log("feed", "image file %s deleted" % filename)
        except OSError:
            pass  # already gone: nothing to do


def _get_post_or_404(post_id):
    post = execute("SELECT id, user_id, image_filename FROM posts WHERE id = :id",
                   id=post_id).mappings().fetchone()
    if post is None:
        bac_log("feed", "post id=%s not found -> 404" % post_id)
        abort(404)
    return post


@bp.route("/feed")
@login_required
def index():
    rows, comments_by_post, has_more = load_posts(before=request.args.get("before", type=int))
    return render_template("feed.html", posts=rows, comments_by_post=comments_by_post,
                           has_more=has_more, next_before=rows[-1]["id"] if has_more else None)


@bp.route("/posts", methods=("POST",))
@login_required
def create_post():
    body = request.form.get("body", "").strip()
    upload = request.files.get("image")
    has_image = upload is not None and bool(upload.filename)

    if not body and not has_image:
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
        os.makedirs(_upload_dir(), exist_ok=True)
        with open(os.path.join(_upload_dir(), filename), "wb") as fh:
            fh.write(data)

    try:
        post_id = insert(posts, user_id=g.user["id"], body=body, image_filename=filename,
                         created_at=now_str())
        commit()
    except Exception:
        rollback()
        _remove_image(filename)  # do not leave an orphan file behind
        raise
    bac_log("feed", "post id=%s created by %r (%d chars, image=%s)"
            % (post_id, g.user["username"], len(body), bool(filename)))
    flash("Posted!", "success")
    return _back(post_id)


@bp.route("/posts/<int:post_id>/delete", methods=("POST",))
@login_required
def delete_post(post_id):
    post = _get_post_or_404(post_id)
    if post["user_id"] != g.user["id"]:
        bac_log("feed", "user id=%s tried to delete post %s of someone else -> 403"
                % (g.user["id"], post_id))
        abort(403)
    # children first (foreign keys), then the post itself
    execute("DELETE FROM likes WHERE post_id = :id", id=post_id)
    execute("DELETE FROM comments WHERE post_id = :id", id=post_id)
    execute("DELETE FROM posts WHERE id = :id", id=post_id)
    commit()
    _remove_image(post["image_filename"])
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
def add_comment(post_id):
    _get_post_or_404(post_id)
    body = request.form.get("body", "").strip()
    if not body:
        flash("Write a comment first.", "warning")
    elif len(body) > MAX_COMMENT_LENGTH:
        flash("Comment too long (max %d characters)." % MAX_COMMENT_LENGTH, "danger")
    else:
        comment_id = insert(comments, post_id=post_id, user_id=g.user["id"], body=body,
                            created_at=now_str())
        commit()
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
    # the comment's author and the owner of the post may remove it
    if g.user["id"] not in (row["user_id"], row["post_owner_id"]):
        bac_log("feed", "user id=%s not allowed to delete comment %s -> 403" % (g.user["id"], comment_id))
        abort(403)
    execute("DELETE FROM comments WHERE id = :id", id=comment_id)
    commit()
    bac_log("feed", "comment id=%s deleted by id=%s" % (comment_id, g.user["id"]))
    return _back(row["post_id"])


@bp.route("/uploads/<filename>")
@login_required
def uploaded_file(filename):
    # send_from_directory refuses paths that escape the folder (../ tricks)
    response = send_from_directory(_upload_dir(), filename, max_age=86400)
    response.cache_control.private = True  # photos are for signed-in members only
    response.cache_control.public = False
    return response
