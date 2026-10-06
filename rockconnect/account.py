# File: account.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""A member's own account: settings, change password, download my data (GDPR access / portability)
and delete my account (GDPR erasure)."""
import json
import tempfile
import zipfile

from flask import (Blueprint, current_app, flash, g, redirect, render_template, request,
                   send_file, session, url_for)

from . import ages, ratelimit, session_store, social, storage
from .auth import check_password, hash_password, login_required, password_error
from .baclog import bac_log
from .db import commit, execute
from .util import now_str, safe_next

bp = Blueprint("account", __name__, url_prefix="/account")


def delete_account_data(user_id):
    """Erase a member and everything they own. Private conversations they took part in are removed for
    both sides (a chat cannot exist with one participant gone). Used by the member and by admins."""
    own_posts = "SELECT id FROM posts WHERE user_id = :u"
    own_chats = "SELECT id FROM conversations WHERE user_low_id = :u OR user_high_id = :u"
    photos = [r[0] for r in execute("SELECT image_filename FROM posts WHERE user_id = :u"
                                    " AND image_filename IS NOT NULL", u=user_id)]
    for sql in (
        "DELETE FROM likes WHERE post_id IN (%s)" % own_posts,
        "DELETE FROM comments WHERE post_id IN (%s)" % own_posts,
        "DELETE FROM likes WHERE user_id = :u",
        "DELETE FROM comments WHERE user_id = :u",
        "DELETE FROM messages WHERE conversation_id IN (%s)" % own_chats,
        "DELETE FROM conversation_reads WHERE conversation_id IN (%s) OR user_id = :u" % own_chats,
        "DELETE FROM conversations WHERE user_low_id = :u OR user_high_id = :u",
        "DELETE FROM posts WHERE user_id = :u",
        "DELETE FROM blocks WHERE blocker_id = :u OR blocked_id = :u",
        "DELETE FROM sessions WHERE user_id = :u",
        "DELETE FROM email_tokens WHERE user_id = :u",
        "DELETE FROM external_events WHERE user_id = :u",
        "DELETE FROM attendances WHERE user_id = :u",
        "DELETE FROM follows WHERE follower_id = :u OR followed_id = :u",
        "DELETE FROM notifications WHERE user_id = :u",
        # what they caused in other members' lists ("X started following you", "X is going to ..."): the keys end in their id
        "DELETE FROM notifications WHERE dedupe_key = :follow_key OR dedupe_key LIKE :going_key",
        "DELETE FROM gig_comments WHERE user_id = :u",
        "DELETE FROM user_instruments WHERE user_id = :u",
        "DELETE FROM user_genres WHERE user_id = :u",
        "DELETE FROM user_goals WHERE user_id = :u",
        "DELETE FROM review_items WHERE author_id = :u",
        "DELETE FROM reports WHERE reporter_id = :u",
        # reports about their content keep no copy of it: the case is closed, nobody is identified
        "UPDATE reports SET snapshot = '', details = '', target_user_id = NULL, status = 'closed',"
        " resolution = 'author_deleted' WHERE target_user_id = :u",
        "DELETE FROM users WHERE id = :u",
    ):
        execute(sql, u=user_id, follow_key="follow:%d" % user_id, going_key="going:%d:%%" % user_id)
    commit()
    for name in photos:  # after the commit: if the database step failed, no photo is lost
        try:
            storage.get().delete(name)
        except Exception as exc:
            bac_log("account", "could not delete photo %s (%s)" % (name, type(exc).__name__))
    bac_log("account", "user id=%s erased (%d photo(s) removed)" % (user_id, len(photos)))


@bp.route("/")
@login_required
def settings():
    blocked = execute("SELECT u.id, u.username, u.name FROM blocks b JOIN users u ON u.id = b.blocked_id"
                      " WHERE b.blocker_id = :me ORDER BY u.username", me=g.user["id"]).mappings().fetchall()
    return render_template("account.html", blocked=blocked)


@bp.route("/confirm-age", methods=("GET", "POST"))
@login_required
def confirm_age():
    """Ask once for the date of birth. Set once, it cannot be changed here. Under the minimum age: the account is closed."""
    if g.user["birth_date"] is not None:
        return redirect(url_for("feed.index"))
    nxt = safe_next(request.values.get("next"), url_for("feed.index"))
    if request.method == "POST":
        text = request.form.get("birth_date", "").strip()
        problem = ages.error_for(text)
        if problem and ages.parse_birth_date(text) is not None:
            # a real date, but too young: this installation does not allow the account
            execute("UPDATE users SET birth_date = :b, status = 'banned', ban_reason = :r WHERE id = :id",
                    b=text, r="Below the minimum age", id=g.user["id"])
            session_store.destroy_all(g.user["id"])
            commit()
            bac_log("account", "user id=%s is below the minimum age -> closed" % g.user["id"])
            session.clear()
            flash("Sorry, members must be at least %d years old, so this account has been closed. "
                  "Write to %s if you want your data erased." % (current_app.config["MIN_AGE"],
                                                               current_app.config["CONTACT_EMAIL"]), "danger")
            return redirect(url_for("auth.signin"))
        if problem:
            flash(problem, "danger")
        else:
            execute("UPDATE users SET birth_date = :b WHERE id = :id AND birth_date IS NULL", b=text, id=g.user["id"])
            commit()
            flash("Thank you. Your date of birth is only used to check your age and is never shown.", "success")
            return redirect(nxt)
    return render_template("confirm_age.html", next_url=nxt)


@bp.route("/signout-everywhere", methods=("POST",))
@login_required
def signout_everywhere():
    """Logins last weeks, so members need a way to end the ones they left behind (a shared PC, a lost phone)."""
    session_store.destroy_all(g.user["id"])
    commit()
    session.clear()
    bac_log("account", "user id=%s signed out on every device" % g.user["id"])
    flash("You were signed out on every device.", "info")
    return redirect(url_for("auth.signin"))


@bp.route("/password", methods=("POST",))
@login_required
def change_password():
    ratelimit.allow("password_user", g.user["id"])
    current = request.form.get("current", "")
    new = request.form.get("password", "")
    error = None
    if not check_password(current, g.user["password"]):
        error = "Your current password is not right."
    elif new != request.form.get("password2", ""):
        error = "The two new passwords do not match."
    else:
        error = password_error(new, g.user["username"], g.user["email"])
    if error:
        flash(error, "danger")
        return redirect(url_for("account.settings"))
    execute("UPDATE users SET password = :p WHERE id = :id", p=hash_password(new), id=g.user["id"])
    # sign out every other device, keep this one
    execute("DELETE FROM sessions WHERE user_id = :u AND token_hash <> :keep", u=g.user["id"],
            keep=session_store.hash_token(session.get("sid")))
    commit()
    bac_log("account", "password changed by user id=%s" % g.user["id"])
    flash("Password changed. Other devices were signed out.", "success")
    return redirect(url_for("account.settings"))


def build_export(user):
    """Everything we hold about the member, as a zip: data.json + their photos."""
    uid = user["id"]
    rows = lambda sql, **kw: [dict(r) for r in execute(sql, u=uid, **kw).mappings()]  # noqa: E731
    posts = rows("SELECT id, body, created_at, event_at, event_place, image_filename FROM posts"
                 " WHERE user_id = :u ORDER BY id")
    data = {
        "exported_at_utc": now_str(),
        "profile": {k: user[k] for k in ("username", "name", "email", "about", "kind", "location",
                                           "website", "created_at", "terms_accepted_at", "birth_date")}
                   | {"email_confirmed": bool(user["email_verified"]), "hide_gig_plans": bool(user["hide_plans"]),
                                                           "notify_friends_going": bool(user["notify_friends_going"])},
        "posts": [{**p, "photo": "photos/" + p["image_filename"] if p["image_filename"] else None}
                  for p in posts],
        "comments": rows("SELECT id, post_id, body, created_at FROM comments WHERE user_id = :u ORDER BY id"),
        "likes": rows("SELECT post_id, created_at FROM likes WHERE user_id = :u ORDER BY post_id"),
        "messages_sent": rows(
            "SELECT m.id, o.username AS conversation_with, m.body, m.created_at FROM messages m"
            " JOIN conversations c ON c.id = m.conversation_id"
            " JOIN users o ON o.id = CASE WHEN c.user_low_id = :u THEN c.user_high_id ELSE c.user_low_id END"
            " WHERE m.sender_id = :u ORDER BY m.id"),
        "interests": social.user_tags(uid),
        "following": [r["username"] for r in rows(
            "SELECT u.username FROM follows f JOIN users u ON u.id = f.followed_id WHERE f.follower_id = :u")],
        "followers": [r["username"] for r in rows(
            "SELECT u.username FROM follows f JOIN users u ON u.id = f.follower_id WHERE f.followed_id = :u")],
        "gig_comments": rows("SELECT source, event_ref, title, event_at, body, created_at FROM gig_comments"
                             " WHERE user_id = :u ORDER BY id"),
        "gig_plans": rows("SELECT source, event_ref, status, visible, title, venue, city, event_at, created_at"
                          " FROM attendances WHERE user_id = :u ORDER BY event_at"),
        "blocked_members": [r["username"] for r in rows(
            "SELECT u.username FROM blocks b JOIN users u ON u.id = b.blocked_id WHERE b.blocker_id = :u")],
        "reports_filed": rows("SELECT target_type, target_id, reason, details, status, created_at"
                              " FROM reports WHERE reporter_id = :u ORDER BY id"),
    }
    for p in data["posts"]:
        p.pop("image_filename")
    archive = tempfile.TemporaryFile()  # on disk: a member with many photos must not fill the memory
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data.json", json.dumps(data, indent=2, ensure_ascii=False))
        zf.writestr("README.txt", "Your data from %s, exported %s UTC.\n"
                    "data.json holds your profile, posts, comments, likes, the messages you sent and your blocks;\n"
                    "photos/ holds the pictures you uploaded.\n" % (user["username"], data["exported_at_utc"]))
        for p in posts:
            if p["image_filename"]:
                try:
                    zf.writestr("photos/" + p["image_filename"], storage.get().read(p["image_filename"]))
                except Exception as exc:  # a photo that vanished from storage must not break the export
                    bac_log("account", "export: photo %s missing (%s)" % (p["image_filename"], type(exc).__name__))
    archive.seek(0)
    return archive


@bp.route("/export")
@login_required
def export():
    ratelimit.allow("export_user", g.user["id"])
    archive = build_export(g.user)
    bac_log("account", "data export downloaded by user id=%s" % g.user["id"])
    return send_file(archive, mimetype="application/zip", as_attachment=True,
                     download_name="%s-data-export.zip" % g.user["username"])


@bp.route("/delete", methods=("POST",))
@login_required
def delete():
    ratelimit.allow("password_user", g.user["id"])
    if not check_password(request.form.get("password", ""), g.user["password"]) \
            or request.form.get("confirm", "").strip().lower() != "delete":
        flash("To delete your account type your password and the word DELETE.", "danger")
        return redirect(url_for("account.settings"))
    delete_account_data(g.user["id"])
    session.clear()
    flash("Your account and everything you posted were deleted. Goodbye!", "info")
    return redirect(url_for("views.home"))
