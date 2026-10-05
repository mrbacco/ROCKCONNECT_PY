# File: session_store.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Server-side login sessions.

Life cycle:  register -> sign in -> create() a session that lasts SESSION_LIFETIME_MINUTES
             -> every request find_valid() checks it and renew() pushes the end date forward (a "sliding"
             session, like the big social sites: come back within the window and you are still signed in)
             -> after a long absence, sign out, password change or suspension the user must sign in again.

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


hash_token = _hash  # public name, used by account.py to keep the current session when others are ended


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
    old = (now - timedelta(days=1)).strftime(TIME_FORMAT)
    execute("DELETE FROM sessions WHERE expires_at < :old", old=old)
    execute("DELETE FROM rate_hits WHERE created_at < :old", old=old)       # rate-limit counters older than a day
    execute("DELETE FROM email_tokens WHERE expires_at < :now", now=now.strftime(TIME_FORMAT))
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


def destroy_all(user_id):
    """End every session of a user (password changed, account banned or deleted). Caller commits."""
    execute("DELETE FROM sessions WHERE user_id = :u", u=user_id)
    bac_log("session", "all sessions of user id=%s destroyed" % user_id)


def renew(token, expires_at):
    """Slide the end date forward when the session was last extended more than SESSION_RENEW_MINUTES ago.

    Doing it at most that often (default once a day) keeps this to one tiny write per day, not one per click.
    Returns the new expiry text, or None when nothing was changed.
    """
    now = datetime.now(timezone.utc)
    end = datetime.strptime(expires_at, TIME_FORMAT).replace(tzinfo=timezone.utc)
    window = lifetime_minutes()
    renew_after = min(current_app.config["SESSION_RENEW_MINUTES"], max(1, window // 4))
    if end - now > timedelta(minutes=window - renew_after):
        return None  # extended recently enough
    new_end = (now + lifetime()).strftime(TIME_FORMAT)
    execute("UPDATE sessions SET expires_at = :e WHERE token_hash = :h", e=new_end, h=_hash(token))
    commit()
    bac_log("session", "renewed until %s UTC" % new_end)
    return new_end
