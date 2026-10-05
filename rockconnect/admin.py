# File: admin.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Admin console (/admin): the report queue, member list with ban / unban / delete, and the audit log.

Only accounts with role "admin" get in (everyone else sees a 404, the console does not advertise itself).
Make an admin on the command line:  flask make-admin <username>
"""
import functools

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from . import modlog, session_store
from .account import delete_account_data
from .auth import login_required
from .baclog import bac_log
from .db import commit, execute
from .feed import delete_post_rows, remove_image
from .util import now_str

bp = Blueprint("admin", __name__, url_prefix="/admin")


def admin_required(view):
    @functools.wraps(view)
    @login_required
    def wrapped(**kwargs):
        if g.user["role"] != "admin":
            bac_log("admin", "user id=%s is not an admin -> 404" % g.user["id"])
            abort(404)
        return view(**kwargs)

    return wrapped


def ban_user(user_id, reason):
    """Suspend an account now: it is signed out everywhere and disappears from the directory and the feed."""
    execute("UPDATE users SET status = 'banned', ban_reason = :r WHERE id = :id",
            r=reason[:255] or None, id=user_id)
    session_store.destroy_all(user_id)
    commit()
    modlog.record("ban", "user %d" % user_id, reason)


def _target_user(user_id):
    user = execute("SELECT id, username, role, status FROM users WHERE id = :id", id=user_id).mappings().fetchone()
    if user is None:
        abort(404)
    if user["id"] == g.user["id"] or user["role"] == "admin":
        flash("Admins cannot be banned or deleted here (use `flask remove-admin` first).", "warning")
        return None
    return user


@bp.route("/")
@admin_required
def dashboard():
    count = lambda sql: execute(sql).scalar() or 0  # noqa: E731
    stats = {
        "members": count("SELECT count(*) FROM users"),
        "bands": count("SELECT count(*) FROM users WHERE kind = 'band'"),
        "venues": count("SELECT count(*) FROM users WHERE kind = 'venue'"),
        "banned": count("SELECT count(*) FROM users WHERE status = 'banned'"),
        "posts": count("SELECT count(*) FROM posts"),
        "open_reports": count("SELECT count(*) FROM reports WHERE status = 'open'"),
    }
    log = execute("SELECT * FROM mod_log ORDER BY id DESC LIMIT 15").mappings().fetchall()
    recent = execute("SELECT id, username, kind, created_at FROM users ORDER BY id DESC LIMIT 8").mappings().fetchall()
    return render_template("admin_dashboard.html", stats=stats, log=log, recent=recent)


@bp.route("/reports")
@admin_required
def report_queue():
    status = "closed" if request.args.get("status") == "closed" else "open"
    rows = execute(
        "SELECT r.*, ru.username AS reporter, tu.username AS target_username, tu.status AS target_status"
        " FROM reports r JOIN users ru ON ru.id = r.reporter_id"
        " LEFT JOIN users tu ON tu.id = r.target_user_id"
        " WHERE r.status = :s ORDER BY r.id DESC LIMIT 100", s=status).mappings().fetchall()
    return render_template("admin_reports.html", reports=rows, status=status)


@bp.route("/reports/<int:report_id>/resolve", methods=("POST",))
@admin_required
def resolve_report(report_id):
    rep = execute("SELECT * FROM reports WHERE id = :id", id=report_id).mappings().fetchone()
    if rep is None:
        abort(404)
    action = request.form.get("action")
    if action not in ("dismiss", "remove", "remove_ban"):
        abort(400)
    resolution = {"dismiss": "dismissed", "remove": "removed", "remove_ban": "removed_banned"}[action]

    if action in ("remove", "remove_ban"):
        if rep["target_type"] == "post":
            post = execute("SELECT id, user_id, image_filename FROM posts WHERE id = :id",
                           id=rep["target_id"]).mappings().fetchone()
            if post is not None:
                delete_post_rows(post)
                commit()
                remove_image(post["image_filename"])
                modlog.record("remove_post", "post %d" % post["id"], "report #%d" % report_id)
        elif rep["target_type"] == "comment":
            execute("DELETE FROM comments WHERE id = :id", id=rep["target_id"])
            commit()
            modlog.record("remove_comment", "comment %d" % rep["target_id"], "report #%d" % report_id)
        if action == "remove_ban" and rep["target_user_id"]:
            target = execute("SELECT id, role FROM users WHERE id = :id", id=rep["target_user_id"]).mappings().fetchone()
            if target is not None and target["role"] != "admin":
                ban_user(target["id"], "report #%d (%s)" % (report_id, rep["reason"]))
            else:
                resolution = "removed"

    # every open report about the same thing is settled together
    execute("UPDATE reports SET status = 'closed', resolution = :res, resolved_by = :by, resolved_at = :now"
            " WHERE status = 'open' AND target_type = :t AND target_id = :i",
            res=resolution, by=g.user["id"], now=now_str(), t=rep["target_type"], i=rep["target_id"])
    commit()
    modlog.record("report_" + resolution, "report #%d" % report_id)
    flash("Report #%d closed (%s)." % (report_id, resolution), "success")
    return redirect(url_for("admin.report_queue"))


@bp.route("/hidden")
@admin_required
def hidden():
    rows = execute("SELECT * FROM hidden_events ORDER BY id DESC LIMIT 200").mappings().fetchall()
    return render_template("admin_hidden.html", rows=rows)


@bp.route("/hidden/<int:hidden_id>/unhide", methods=("POST",))
@admin_required
def unhide(hidden_id):
    row = execute("SELECT source, external_id FROM hidden_events WHERE id = :id", id=hidden_id).fetchone()
    if row is None:
        abort(404)
    execute("DELETE FROM hidden_events WHERE id = :id", id=hidden_id)
    commit()
    modlog.record("unhide_event", "%s %s" % (row[0], row[1]))
    flash("It can be imported again at the next refresh.", "success")
    return redirect(url_for("admin.hidden"))


@bp.route("/users")
@admin_required
def members():
    q = request.args.get("q", "").strip().lower()
    params, sql = {}, ("SELECT id, username, name, email, kind, role, status, ban_reason, email_verified, created_at"
                       " FROM users")
    if q:
        sql += " WHERE lower(username) LIKE :p OR lower(name) LIKE :p OR lower(email) LIKE :p"
        params["p"] = "%" + q + "%"
    rows = execute(sql + " ORDER BY id DESC LIMIT 200", **params).mappings().fetchall()
    return render_template("admin_users.html", members=rows, q=q)


@bp.route("/users/<int:user_id>/ban", methods=("POST",))
@admin_required
def ban(user_id):
    user = _target_user(user_id)
    if user is not None:
        ban_user(user_id, request.form.get("reason", "").strip())
        flash("%s is suspended and signed out." % user["username"], "success")
    return redirect(url_for("admin.members"))


@bp.route("/users/<int:user_id>/unban", methods=("POST",))
@admin_required
def unban(user_id):
    user = execute("SELECT id, username FROM users WHERE id = :id", id=user_id).mappings().fetchone()
    if user is None:
        abort(404)
    execute("UPDATE users SET status = 'active', ban_reason = NULL WHERE id = :id", id=user_id)
    commit()
    modlog.record("unban", "user %d" % user_id)
    flash("%s can sign in again." % user["username"], "success")
    return redirect(url_for("admin.members"))


@bp.route("/users/<int:user_id>/delete", methods=("POST",))
@admin_required
def delete_member(user_id):
    """Erase an account and everything it owns (a member's GDPR erasure request sent by e-mail)."""
    user = _target_user(user_id)
    if user is not None:
        delete_account_data(user_id)
        modlog.record("delete_user", "user %d" % user_id, user["username"])
        flash("Account %s and all its content were erased." % user["username"], "success")
    return redirect(url_for("admin.members"))
