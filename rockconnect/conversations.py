# File: conversations.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Messenger-style private chat between two users (login required for everything).

The browser (static/js/chat.js) polls /conversations/<id>/messages every 2 seconds and sends
messages with fetch(), so new messages appear without reloading. The navbar badge polls
/conversations/unread.
"""
from flask import (Blueprint, abort, flash, g, jsonify, redirect,
                   render_template, request, url_for)
from sqlalchemy.exc import IntegrityError

from .auth import login_required
from .baclog import bac_log
from .db import commit, execute, insert, messages, rollback
from .util import now_str

bp = Blueprint("conversations", __name__, url_prefix="/conversations")

MAX_MESSAGE_LENGTH = 2000

# joins a conversation row to the OTHER participant (not the logged-in user)
_OTHER_USER_JOIN = (
    "JOIN users u ON u.id = CASE WHEN c.user_low_id = :me"
    " THEN c.user_high_id ELSE c.user_low_id END"
)

# id of the last message the logged-in user has read in conversation c
_LAST_READ = ("COALESCE((SELECT r.last_read_id FROM conversation_reads r"
              " WHERE r.conversation_id = c.id AND r.user_id = :me), 0)")


def _wants_json():
    # chat.js sends this header; plain HTML forms do not
    return request.headers.get("X-Requested-With") == "fetch"


def _message_json(row, me):
    return {"id": row["id"], "body": row["body"], "created_at": row["created_at"],
            "sender": row["sender"], "mine": row["sender_id"] == me}


def unread_count(user_id):
    """How many conversations have messages from the other person that I have not read yet."""
    return execute(
        "SELECT count(DISTINCT c.id) FROM messages m"
        " JOIN conversations c ON c.id = m.conversation_id"
        " WHERE (c.user_low_id = :me OR c.user_high_id = :me)"
        " AND m.sender_id <> :me AND m.id > " + _LAST_READ, me=user_id
    ).scalar() or 0


def _mark_read(conversation_id, upto_id):
    """Remember that the logged-in user has seen every message up to upto_id."""
    me = g.user["id"]
    row = execute("SELECT last_read_id FROM conversation_reads"
                  " WHERE conversation_id = :c AND user_id = :u", c=conversation_id, u=me
                  ).mappings().fetchone()
    try:
        if row is None:
            execute("INSERT INTO conversation_reads (conversation_id, user_id, last_read_id)"
                    " VALUES (:c, :u, :m)", c=conversation_id, u=me, m=upto_id)
        elif upto_id > row["last_read_id"]:
            execute("UPDATE conversation_reads SET last_read_id = :m"
                    " WHERE conversation_id = :c AND user_id = :u", c=conversation_id, u=me, m=upto_id)
        else:
            return
        commit()
    except IntegrityError:
        rollback()  # two tabs marked it at once: harmless


@bp.app_context_processor
def inject_unread():
    # available in every template: the navbar badge
    user = getattr(g, "user", None)
    return {"unread_count": unread_count(user["id"]) if user else 0}


def _my_conversation(conversation_id):
    """The conversation, only if the logged-in user is in it; otherwise 404.

    404 (not 403) so nobody can probe which conversation ids exist.
    """
    conv = execute(
        "SELECT c.id, u.id AS other_id, u.username AS other_username, u.name AS other_name"
        " FROM conversations c " + _OTHER_USER_JOIN +
        " WHERE c.id = :cid AND (c.user_low_id = :me OR c.user_high_id = :me)",
        me=g.user["id"], cid=conversation_id,
    ).mappings().fetchone()
    if conv is None:
        bac_log("chat", "user id=%s denied/unknown conversation id=%s -> 404"
                % (g.user["id"], conversation_id))
        abort(404)
    return conv


def _sidebar():
    """My conversations (newest activity first) with last message preview and unread flag."""
    rows = execute(
        "SELECT c.id, c.created_at, u.id AS other_id, u.username AS other_username,"
        " (SELECT m.body FROM messages m WHERE m.conversation_id = c.id"
        "  ORDER BY m.id DESC LIMIT 1) AS last_body,"
        " (SELECT m.created_at FROM messages m WHERE m.conversation_id = c.id"
        "  ORDER BY m.id DESC LIMIT 1) AS last_at,"
        " (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id"
        "  AND m.sender_id <> :me AND m.id > " + _LAST_READ + ") AS unread"
        " FROM conversations c " + _OTHER_USER_JOIN +
        " WHERE c.user_low_id = :me OR c.user_high_id = :me",
        me=g.user["id"],
    ).mappings().fetchall()
    # done in Python: NULL ordering differs between databases
    return sorted(rows, key=lambda r: r["last_at"] or r["created_at"], reverse=True)


@bp.route("/")
@login_required
def inbox():
    """Chat page with the conversation list and no conversation selected."""
    rows = _sidebar()
    bac_log("chat", "inbox of %r: %d conversation(s)" % (g.user["username"], len(rows)))
    return render_template("chat.html", conversations=rows, conv=None, messages=[])


@bp.route("/unread")
@login_required
def unread():
    """JSON for the navbar badge."""
    return jsonify(count=unread_count(g.user["id"]))


@bp.route("/start/<int:user_id>", methods=("POST",))
@login_required
def start(user_id):
    """Open the conversation with another user, creating it the first time."""
    me = g.user["id"]
    if user_id == me:
        bac_log("chat", "user id=%s tried to message themselves" % me)
        flash("You cannot message yourself.", "warning")
        return redirect(url_for("views.profile", user_id=me))
    if execute("SELECT 1 FROM users WHERE id = :id", id=user_id).fetchone() is None:
        abort(404)

    low, high = sorted((me, user_id))  # the pair is stored low < high so it is unique
    find = ("SELECT id FROM conversations"
            " WHERE user_low_id = :low AND user_high_id = :high")
    conv = execute(find, low=low, high=high).mappings().fetchone()
    if conv is None:
        try:
            execute("INSERT INTO conversations (user_low_id, user_high_id, created_at)"
                    " VALUES (:low, :high, :now)", low=low, high=high, now=now_str())
            commit()
            bac_log("chat", "created conversation between ids %s and %s" % (low, high))
        except IntegrityError:
            # both users clicked at the same moment: the other request created it first
            rollback()
            bac_log("chat", "conversation %s-%s already created concurrently" % (low, high))
        conv = execute(find, low=low, high=high).mappings().fetchone()
    return redirect(url_for("conversations.thread", conversation_id=conv["id"]))


@bp.route("/<int:conversation_id>", methods=("GET", "POST"))
@login_required
def thread(conversation_id):
    """GET shows the chat; POST sends a message (JSON reply for chat.js, redirect for plain forms)."""
    conv = _my_conversation(conversation_id)
    if request.method == "POST":
        body = request.form.get("body", "").strip()
        error = None
        if not body:
            error = "Write something first."
        elif len(body) > MAX_MESSAGE_LENGTH:
            error = "Message too long (max %d characters)." % MAX_MESSAGE_LENGTH
        if error:
            if _wants_json():
                return jsonify(error=error), 400
            flash(error, "warning")
            return redirect(url_for("conversations.thread", conversation_id=conversation_id))

        message_id = insert(messages, conversation_id=conversation_id, sender_id=g.user["id"],
                            body=body, created_at=now_str())
        commit()
        # log sizes only, never the message text (private)
        bac_log("chat", "message id=%s (%d chars) sent by id=%s in conversation %s"
                % (message_id, len(body), g.user["id"], conversation_id))
        if _wants_json():
            row = execute(
                "SELECT m.id, m.body, m.created_at, m.sender_id, u.username AS sender"
                " FROM messages m JOIN users u ON u.id = m.sender_id WHERE m.id = :id",
                id=message_id).mappings().fetchone()
            return jsonify(message=_message_json(row, g.user["id"])), 201
        return redirect(url_for("conversations.thread", conversation_id=conversation_id))

    msgs = execute(
        "SELECT m.id, m.body, m.created_at, m.sender_id, u.username AS sender"
        " FROM messages m JOIN users u ON u.id = m.sender_id"
        " WHERE m.conversation_id = :cid ORDER BY m.id",
        cid=conversation_id,
    ).mappings().fetchall()
    if msgs:
        _mark_read(conversation_id, msgs[-1]["id"])  # opening the chat = reading it
    bac_log("chat", "showing conversation %s (%d message(s))" % (conversation_id, len(msgs)))
    return render_template("chat.html", conversations=_sidebar(), conv=conv, messages=msgs,
                           max_length=MAX_MESSAGE_LENGTH)


@bp.route("/<int:conversation_id>/messages")
@login_required
def new_messages(conversation_id):
    """JSON poll: messages with id greater than ?after=. Called every 2 s by chat.js."""
    _my_conversation(conversation_id)
    after = request.args.get("after", 0, type=int)
    rows = execute(
        "SELECT m.id, m.body, m.created_at, m.sender_id, u.username AS sender"
        " FROM messages m JOIN users u ON u.id = m.sender_id"
        " WHERE m.conversation_id = :cid AND m.id > :after ORDER BY m.id",
        cid=conversation_id, after=after,
    ).mappings().fetchall()
    if rows:
        _mark_read(conversation_id, rows[-1]["id"])  # the chat is open, so they are read
        bac_log("chat", "poll conversation %s after=%s -> %d new" % (conversation_id, after, len(rows)))
    return jsonify(messages=[_message_json(r, g.user["id"]) for r in rows])
