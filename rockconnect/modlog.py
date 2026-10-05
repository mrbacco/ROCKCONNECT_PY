# File: modlog.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Audit trail: every moderation action (ban, content removal, ...) is written to `mod_log`."""
from flask import g

from .baclog import bac_log
from .db import commit, insert, mod_log
from .util import now_str


def record(action, target="", detail=""):
    """Log what the signed-in admin just did. Call it before commit() or commit afterwards."""
    actor = g.user
    insert(mod_log, actor_id=actor["id"] if actor else None,
           actor_name=actor["username"] if actor else "system",
           action=action, target=str(target)[:60], detail=str(detail)[:255], created_at=now_str())
    commit()
    bac_log("modlog", "%s target=%s" % (action, target))
