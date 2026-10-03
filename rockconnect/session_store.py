# File: session_store.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Server-side login sessions.

Life cycle:  register -> sign in -> create() a session that lasts SESSION_LIFETIME_MINUTES
             -> every request find_valid() checks it -> when it has expired the user must sign in again.

The browser cookie holds only a random token (cookie key "sid"). The database holds the sha256 of that
token plus the expiry, so a session can be ended at any time (sign out, expiry) even if somebody kept a
copy of the cookie. Tokens are never logged.
"""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app

from .baclog import bac_log
from .db import commit, execute, insert, sessions
from .util import TIME_FORMAT, now_str


def _hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def lifetime_minutes():
    return current_app.config["SESSION_LIFETIME_MINUTES"]


def lifetime():
    return timedelta(minutes=lifetime_minutes())


def create(user_id):
    """Start a session for the user. Returns (token for the cookie, expiry text)."""
    now = datetime.now(timezone.utc)
    expires_at = (now + lifetime()).strftime(TIME_FORMAT)
    token = secrets.token_urlsafe(32)  # 256 random bits: cannot be guessed
    # housekeeping: forget sessions that ended long ago so the table does not grow forever
    execute("DELETE FROM sessions WHERE expires_at < :old",
            old=(now - timedelta(days=1)).strftime(TIME_FORMAT))
    insert(sessions, token_hash=_hash(token), user_id=user_id,
           created_at=now.strftime(TIME_FORMAT), expires_at=expires_at)
    commit()
    bac_log("session", "created for user id=%s, expires at %s UTC (%s min)"
            % (user_id, expires_at, current_app.config["SESSION_LIFETIME_MINUTES"]))
    return token, expires_at


def find_valid(token):
    """The user row (plus session_expires_at) for a live session, or None if unknown/expired."""
    return execute(
        "SELECT u.*, s.expires_at AS session_expires_at FROM sessions s"
        " JOIN users u ON u.id = s.user_id"
        " WHERE s.token_hash = :h AND s.expires_at > :now",
        h=_hash(token), now=now_str(),
    ).mappings().fetchone()


def destroy(token):
    """End one session now (sign out). Unknown tokens are ignored."""
    if token:
        execute("DELETE FROM sessions WHERE token_hash = :h", h=_hash(token))
        commit()
        bac_log("session", "destroyed (sign out)")


def seconds_left(expires_at):
    """Whole seconds until expiry (never negative)."""
    end = datetime.strptime(expires_at, TIME_FORMAT).replace(tzinfo=timezone.utc)
    return max(0, int((end - datetime.now(timezone.utc)).total_seconds()))
