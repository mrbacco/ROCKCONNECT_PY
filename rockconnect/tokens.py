# File: tokens.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""One-time links sent by e-mail: confirm an address ("verify") or choose a new password ("reset").

The link carries a random token; only its sha256 is stored, so a leaked database cannot be used to
take over accounts. A token works once and expires (see TTL_MINUTES).
"""
import secrets

from .baclog import bac_log
from .db import commit, email_tokens, execute, insert
from .util import in_minutes, now_str, sha256

TTL_MINUTES = {"verify": 60 * 24, "reset": 60}


def create(user_id, purpose, new_email=None):
    """Make a token for the user and return it (it exists in clear only in the e-mail)."""
    # a newer link replaces an older one of the same kind
    execute("DELETE FROM email_tokens WHERE user_id = :u AND purpose = :p", u=user_id, p=purpose)
    token = secrets.token_urlsafe(32)
    insert(email_tokens, token_hash=sha256(token), user_id=user_id, purpose=purpose,
           new_email=new_email, created_at=now_str(), expires_at=in_minutes(TTL_MINUTES[purpose]))
    commit()
    bac_log("tokens", "%s token created for user id=%s" % (purpose, user_id))
    return token


def peek(token, purpose):
    """The token row if the link is valid (right kind, not expired), else None. Does not use it up."""
    if not token:
        return None
    return execute(
        "SELECT * FROM email_tokens WHERE token_hash = :h AND purpose = :p AND expires_at > :now",
        h=sha256(token), p=purpose, now=now_str()).mappings().fetchone()


def consume(token, purpose):
    """Like peek(), but the link is used up: a second click on it fails."""
    row = peek(token, purpose)
    if row is not None:
        execute("DELETE FROM email_tokens WHERE token_hash = :h", h=row["token_hash"])
        commit()
        bac_log("tokens", "%s token used by user id=%s" % (purpose, row["user_id"]))
    return row
