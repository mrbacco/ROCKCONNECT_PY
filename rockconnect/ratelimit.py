# File: ratelimit.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Rate limiting stored in the database, so it is shared by every worker process and survives restarts.

Each counted event is one row in `rate_hits` (bucket = hash of "<limit name>:<who>"). The limits are in
settings.DEFAULT_RATE_LIMITS: name -> (events, seconds). Two ways to use it:

    allow("post_user", user_id)        count this event and refuse if the limit is already reached
    blocked("signin_user", username)   only ask (used for failed sign-ins, which are recorded with hit())
    hit("signin_user", username)       record one event

Refusing raises TooManyRequests, which __init__.py turns into a friendly 429 page (or JSON for scripts).
"""
import secrets
from datetime import datetime, timedelta, timezone

from flask import current_app
from werkzeug.exceptions import TooManyRequests

from .baclog import bac_log
from .db import commit, execute
from .util import TIME_FORMAT, now_str, sha256


def _limit(name):
    return current_app.config["RATE_LIMITS"][name]


def _key(name, who):
    # hashed: the table never holds e-mail addresses or usernames, and the key has a fixed length
    return sha256("%s:%s" % (name, str(who).lower()))[:64]


def _enabled():
    return current_app.config.get("RATE_LIMITS_ENABLED", True)


def blocked(name, who):
    """True when `who` already used up the `name` limit in the current window."""
    if not _enabled():
        return False
    count, seconds = _limit(name)
    since = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).strftime(TIME_FORMAT)
    used = execute("SELECT count(*) FROM rate_hits WHERE bucket = :k AND created_at >= :since",
                   k=_key(name, who), since=since).scalar() or 0
    return used >= count


def hit(name, who):
    """Record one event (and now and then forget events older than a day)."""
    if not _enabled():
        return
    execute("INSERT INTO rate_hits (bucket, created_at) VALUES (:k, :now)", k=_key(name, who), now=now_str())
    if secrets.randbelow(100) == 0:
        old = (datetime.now(timezone.utc) - timedelta(days=1)).strftime(TIME_FORMAT)
        execute("DELETE FROM rate_hits WHERE created_at < :old", old=old)
    commit()


def refuse(name, who):
    _, seconds = _limit(name)
    bac_log("ratelimit", "BLOCKED %s (limit %s per %ss)" % (name, _limit(name)[0], seconds))
    raise TooManyRequests(
        description="Too many attempts. Please wait a while and try again.", retry_after=min(seconds, 900))


def check(name, who):
    """Raise 429 if the limit for `name` is already used up (does not count this call)."""
    if blocked(name, who):
        refuse(name, who)


def allow(name, who):
    """Count this event; raise 429 if the limit was already used up."""
    check(name, who)
    hit(name, who)
