# File: conversations.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Messenger-style private chat between two users (login required for everything).

Message requests: nobody can write to someone they have never chatted with. Pressing "Message" only sends a REQUEST
(no text). The other person sees it under "Requests" and can accept, decline, block or report. Until they accept, no
message can be sent by either side and nothing from the sender is shown to them. A declined request stays silent for the
sender: it just never gets an answer. Chats that were already open before this rule stay open.

The browser (static/js/chat.js) polls /conversations/<id>/messages every 2 seconds and sends
messages with fetch(), so new messages appear without reloading. The navbar badge polls
/conversations/unread.
"""
from flask import (Blueprint, abort, current_app, flash, g, jsonify, redirect,
                   render_template, request, url_for)
from sqlalchemy.exc import IntegrityError

from . import ages, ratelimit
from .auth import login_required, verified_required
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

# a request I received from someone I blocked (or who blocked me, or who is suspended): not shown, not counted
_UNWANTED_REQUEST = (
    "(c.status = 'pending' AND c.initiator_id <> :me AND ("
    "c.initiator_id IN (SELECT blocked_id FROM blocks WHERE blocker_id = :me)"
    " OR c.initiator_id IN (SELECT blocker_id FROM blocks WHERE blocked_id = :me)"
    " OR NOT EXISTS (SELECT 1 FROM users i WHERE i.id = c.initiator_id AND i.status = 'active')))"
)

# id of the last message the logged-in user has read in conversation c
_LAST_READ = ("COALESCE((SELECT r.last_read_id FROM conversation_reads r"
              " WHERE r.conversation_id = c.id AND r.user_id = :me), 0)")


def conversation_state(conv, me):
    """'open' (a normal chat), 'request' (they asked me, I have not answered) or 'waiting' (I asked, no answer yet)."""
    if conv["status"] == "accepted":
        return "open"
    return "waiting" if conv["initiator_id"] == me else "request"


def _wants_json():
    # chat.js sends this header; plain HTML forms do not
    return request.headers.get("X-Requested-With") == "fetch"


def messaging_blocked(me, other_id):
    """True when I cannot message `other_id`: they blocked me, I blocked them, their account is suspended, or one of us is
    a minor and the other an adult."""
    if execute("SELECT 1 FROM blocks WHERE (blocker_id = :a AND blocked_id = :b)"
               " OR (blocker_id = :b AND blocked_id = :a)", a=me, b=other_id).fetchone():
        return True
    row = execute("SELECT status, birth_date FROM users WHERE id = :id", id=other_id).fetchone()
    if row is None or row[0] != "active":
        return True
    mine = g.user["birth_date"] if g.user is not None and g.user["id"] == me else \
        execute("SELECT birth_date FROM users WHERE id = :id", id=me).scalar()
    return not ages.same_side(mine, row[1])      # minors and adults may not write to each other (only if minors can join)


def _message_json(row, me):
    return {"id": row["id"], "body": row["body"], "created_at": row["created_at"],
            "sender": row["sender"], "mine": row["sender_id"] == me}


def unread_count(user_id):
    """The navbar badge: conversations with unread messages from the other person, plus requests waiting for my answer."""
    unread = execute(
        "SELECT count(DISTINCT c.id) FROM messages m"
        " JOIN conversations c ON c.id = m.conversation_id"
        " WHERE (c.user_low_id = :me OR c.user_high_id = :me)"
        " AND NOT (c.status IN ('declined', 'pending') AND c.initiator_id <> :me)"
        " AND m.sender_id <> :me AND m.id > " + _LAST_READ, me=user_id
    ).scalar() or 0
    requests = execute(
        "SELECT count(*) FROM conversations c WHERE (c.user_low_id = :me OR c.user_high_id = :me)"
        " AND c.status = 'pending' AND c.initiator_id <> :me AND NOT " + _UNWANTED_REQUEST, me=user_id
    ).scalar() or 0
    return unread + requests


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
        "SELECT c.id, c.status, c.initiator_id, u.id AS other_id, u.username AS other_username,"
        " u.name AS other_name FROM conversations c " + _OTHER_USER_JOIN +
        " WHERE c.id = :cid AND (c.user_low_id = :me OR c.user_high_id = :me)",
        me=g.user["id"], cid=conversation_id,
    ).mappings().fetchone()
    if conv is None or (conv["status"] == "declined" and conv["initiator_id"] != g.user["id"]):
        bac_log("chat", "user id=%s denied/unknown conversation id=%s -> 404"
                % (g.user["id"], conversation_id))
        abort(404)   # a request I declined is gone for me
    return conv


def _sidebar():
    """My conversations (newest activity first) with last message preview and unread flag."""
    rows = execute(
        "SELECT c.id, c.created_at, c.status, c.initiator_id, u.id AS other_id, u.username AS other_username,"
        " (SELECT m.body FROM messages m WHERE m.conversation_id = c.id"
        "  ORDER BY m.id DESC LIMIT 1) AS last_body,"
        " (SELECT m.created_at FROM messages m WHERE m.conversation_id = c.id"
        "  ORDER BY m.id DESC LIMIT 1) AS last_at,"
        " (SELECT count(*) FROM messages m WHERE m.conversation_id = c.id"
        "  AND m.sender_id <> :me AND m.id > " + _LAST_READ + ") AS unread"
        " FROM conversations c " + _OTHER_USER_JOIN +
        " WHERE (c.user_low_id = :me OR c.user_high_id = :me)"
        " AND NOT (c.status = 'declined' AND c.initiator_id <> :me) AND NOT " + _UNWANTED_REQUEST,
        me=g.user["id"],
    ).mappings().fetchall()
    # done in Python: NULL ordering differs between databases
    rows = [dict(r, last_body=None, unread=0) if r["status"] == "pending" and r["initiator_id"] != g.user["id"] else r
            for r in rows]                       # nothing from someone I have not accepted is shown (not even a preview)
    return sorted(rows, key=lambda r: r["last_at"] or r["created_at"], reverse=True)


@bp.route("/")
@login_required
def inbox():
    """Chat page with the conversation list and no conversation selected."""
    rows = _sidebar()
    bac_log("chat", "inbox of %r: %d conversation(s)" % (g.user["username"], len(rows)))
    return render_template("chat.html", conversations=rows, conv=None, messages=[], me=g.user["id"])


@bp.route("/unread")
@login_required
def unread():
    """JSON for the navbar badge."""
    return jsonify(count=unread_count(g.user["id"]))


@bp.route("/start/<int:user_id>", methods=("POST",))
@login_required
@verified_required
def start(user_id):
    """Ask to chat with another user (the first time this only creates a request, with no text), or open the chat."""
    me = g.user["id"]
    if user_id == me:
        bac_log("chat", "user id=%s tried to message themselves" % me)
        flash("You cannot message yourself.", "warning")
        return redirect(url_for("views.profile", user_id=me))
    if execute("SELECT 1 FROM users WHERE id = :id", id=user_id).fetchone() is None:
        abort(404)
    if messaging_blocked(me, user_id):
        bac_log("chat", "user id=%s cannot start a chat with id=%s (blocked or suspended)" % (me, user_id))
        flash("You cannot message this person.", "warning")
        return redirect(url_for("views.profile", user_id=user_id))

    low, high = sorted((me, user_id))  # the pair is stored low < high so it is unique
    find = ("SELECT id FROM conversations"
            " WHERE user_low_id = :low AND user_high_id = :high")
    conv = execute(find, low=low, high=high).mappings().fetchone()
    new_request = conv is None
    if conv is None:
        ratelimit.allow("chat_request_user", me)   # only so many new people to ask per day
        try:
            execute("INSERT INTO conversations (user_low_id, user_high_id, created_at, status, initiator_id)"
                    " VALUES (:low, :high, :now, 'pending', :me)", low=low, high=high, now=now_str(), me=me)
            commit()
            bac_log("chat", "created conversation between ids %s and %s" % (low, high))
        except IntegrityError:
            # both users clicked at the same moment: the other request created it first
            rollback()
            bac_log("chat", "conversation %s-%s already created concurrently" % (low, high))
        conv = execute(find, low=low, high=high).mappings().fetchone()
    if new_request:
        flash("Request sent. You can write to them once they accept it.", "success")
    return redirect(url_for("conversations.thread", conversation_id=conv["id"]))


@bp.route("/<int:conversation_id>/accept", methods=("POST",))
@login_required
def accept(conversation_id):
    conv = _my_conversation(conversation_id)
    if conversation_state(conv, g.user["id"]) == "request":
        if messaging_blocked(g.user["id"], conv["other_id"]):
            flash("You cannot message this person.", "warning")
            return redirect(url_for("conversations.inbox"))
        execute("UPDATE conversations SET status = 'accepted' WHERE id = :id", id=conversation_id)
        commit()
        bac_log("chat", "user id=%s accepted conversation %s" % (g.user["id"], conversation_id))
        flash("Accepted. You can chat now.", "success")
    return redirect(url_for("conversations.thread", conversation_id=conversation_id))


@bp.route("/<int:conversation_id>/decline", methods=("POST",))
@login_required
def decline(conversation_id):
    conv = _my_conversation(conversation_id)
    if conversation_state(conv, g.user["id"]) == "request":
        execute("UPDATE conversations SET status = 'declined' WHERE id = :id", id=conversation_id)
        commit()
        bac_log("chat", "user id=%s declined conversation %s" % (g.user["id"], conversation_id))
        flash("Request declined. They are not told.", "info")
    return redirect(url_for("conversations.inbox"))


@bp.route("/<int:conversation_id>", methods=("GET", "POST"))
@login_required
def thread(conversation_id):
    """GET shows the chat; POST sends a message (JSON reply for chat.js, redirect for plain forms)."""
    conv = _my_conversation(conversation_id)
    if request.method == "POST":
        if current_app.config["REQUIRE_EMAIL_VERIFICATION"] and not g.user["email_verified"]:
            error, status = "Please confirm your email address first (check your inbox).", 403
        elif messaging_blocked(g.user["id"], conv["other_id"]):
            error, status = "You cannot message this person.", 403
        elif conversation_state(conv, g.user["id"]) == "waiting":
            error, status = ("Your request was sent. You can write once %s accepts it." % conv["other_name"]), 403
        elif conversation_state(conv, g.user["id"]) == "request":
            error, status = "Accept the request first, then you can chat.", 403
        else:
            ratelimit.allow("message_user", g.user["id"])
            body = request.form.get("body", "").strip()
            error, status = None, 400
            if not body:
                error = "Write something first."
            elif len(body) > MAX_MESSAGE_LENGTH:
                error = "Message too long (max %d characters)." % MAX_MESSAGE_LENGTH
        if error:
            if _wants_json():
                return jsonify(error=error), status
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
    state = conversation_state(conv, g.user["id"])
    if state == "request":
        msgs = []                      # nothing from someone I have not accepted (only chats from before this rule have any)
    if msgs:
        _mark_read(conversation_id, msgs[-1]["id"])  # opening the chat = reading it
    bac_log("chat", "showing conversation %s (%d message(s))" % (conversation_id, len(msgs)))
    return render_template("chat.html", conversations=_sidebar(), conv=conv, messages=msgs,
                           max_length=MAX_MESSAGE_LENGTH, me=g.user["id"], state=state,
                           can_message=not messaging_blocked(g.user["id"], conv["other_id"]),
                           can_write=state == "open")


@bp.route("/<int:conversation_id>/messages")
@login_required
def new_messages(conversation_id):
    """JSON poll: messages with id greater than ?after=. Called every 2 s by chat.js."""
    conv = _my_conversation(conversation_id)
    if conversation_state(conv, g.user["id"]) == "request":
        return jsonify(messages=[])
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
