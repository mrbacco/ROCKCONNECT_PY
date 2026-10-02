# File: views.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Home, user list, profile and edit pages."""
from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, url_for)
from sqlalchemy.exc import IntegrityError

from .auth import login_required
from .baclog import bac_log
from .db import commit, execute, rollback
from .feed import load_posts

bp = Blueprint("views", __name__)


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
    """List all registered users; ?q= filters by username or name."""
    q = request.args.get("q", "").strip()
    sql = "SELECT id, username, name FROM users"
    params = {}
    if q:
        # lower() on both sides = case-insensitive on every database (LIKE is case-sensitive on PostgreSQL)
        sql += " WHERE lower(username) LIKE :p OR lower(name) LIKE :p"
        params["p"] = "%" + q.lower() + "%"
    users = execute(sql + " ORDER BY username", **params).mappings().fetchall()
    bac_log("index", "search q=%r -> %d user(s)" % (q, len(users)))
    return render_template("index.html", title="rockconnect", users=users, q=q)


@bp.route("/users_list/<int:user_id>")
def profile(user_id):
    user = execute(
        "SELECT id, username, name, about FROM users WHERE id = :id", id=user_id
    ).mappings().fetchone()
    if user is None:
        bac_log("profile", "user id=%s not found -> 404" % user_id)
        abort(404)
    bac_log("profile", "showing profile of %r (id=%s)" % (user["username"], user_id))
    # the wall (that member's posts) is only for signed-in members
    wall, comments_by_post, has_more = ([], {}, False)
    if g.user:
        wall, comments_by_post, has_more = load_posts(
            user_id=user_id, before=request.args.get("before", type=int))
    return render_template("users_list.html", user=user, posts=wall,
                           comments_by_post=comments_by_post, has_more=has_more,
                           next_before=wall[-1]["id"] if has_more else None)


@bp.route("/edit", methods=("GET", "POST"))
@login_required
def edit():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip()
        about = request.form.get("about", "").strip()
        bac_log("edit", "update attempt by %r" % g.user["username"])
        if not (name and email and about):
            bac_log("edit", "rejected, empty field(s)")
            flash("Name, email and about are required.", "danger")
        else:
            try:
                execute(
                    "UPDATE users SET name = :name, email = :email, about = :about WHERE id = :id",
                    name=name, email=email, about=about, id=g.user["id"],
                )
                commit()
            except IntegrityError:
                rollback()
                bac_log("edit", "rejected, email already used by another account")
                flash("That email is already registered.", "danger")
            else:
                bac_log("edit", "profile of %r updated" % g.user["username"])
                flash("Profile updated.", "success")
                return redirect(url_for("views.profile", user_id=g.user["id"]))
    return render_template("edit.html")
