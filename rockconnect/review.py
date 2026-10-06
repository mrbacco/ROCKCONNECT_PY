# File: review.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Moderation BEFORE anyone sees the content: what gets held, and what a moderator does with it.

Three things send a post, comment or gig comment to the review queue (admin console -> Review):
  1. the blocked-words list (admins edit it; a match is checked case- and accent-insensitively, as whole words/phrases);
  2. the new-member rules: a member's first posts wait until NEW_MEMBER_HOLD_POSTS of them were approved, and in an
     account's first NEW_MEMBER_HOLD_HOURS anything with a link or a photo waits (the usual spam pattern);
  3. reports: when REPORTS_AUTOHIDE different, established members report the same item it is hidden at once.
Held and hidden items are shown only to their author (with a notice) until a moderator approves them. Admins and
moderators are never held. Private chat messages are not screened (they are private, see the privacy policy).

The routes are in admin.py; this module is the logic, so the feed, the gig discussion and the report page can call it.
"""
import functools
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from flask import current_app

from . import modlog, wordlists
from .baclog import bac_log
from .db import commit, execute, insert, review_items
from .util import now_str

# what can be held: target type -> (table, name shown to moderators)
TARGETS = {"post": ("posts", "post"), "comment": ("comments", "comment"), "gigcomment": ("gig_comments", "gig comment")}
MAX_WORDS = 3000
MAX_WORD_LENGTH = 80
TRUSTED_REPORTER_HOURS = 24      # reports of accounts younger than this do not count towards hiding (one person, many accounts)
LINK = re.compile(r"(https?://|www\.)", re.I)


def is_staff(user):
    return bool(user) and user["role"] in ("admin", "moderator")


# ------------------------------------------------------------------ the filter
# scripts whose accent-like marks are part of the letters (the voiced-sound marks of Japanese kana, Hindi vowel signs, Thai tone
# marks): taking them off would turn different words into the same one
KEEP_MARKS = ("HIRAGANA", "KATAKANA", "DEVANAGARI", "THAI", "HANGUL")


def fold(text):
    """Lower-case, accents removed, whitespace collapsed: 'Café   BAR' -> 'cafe bar'."""
    kept, base = [], ""
    for c in unicodedata.normalize("NFKD", str(text or "")):
        if unicodedata.combining(c):
            if not (base and unicodedata.name(base, "").split(" ")[0] in KEEP_MARKS):
                continue
        else:
            base = c
        kept.append(c)
    return re.sub(r"\s+", " ", "".join(kept).casefold()).strip()


# digits and symbols people use instead of letters: "p0rca tr0ia" is read as "porca troia" (as well as literally)
LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s"})


@functools.lru_cache(maxsize=2048)
def _pattern(word):
    """A regular expression for one listed word or phrase: whole words only, any letter may be repeated ("porcaaa"),
    and the words of a phrase may be separated by anything that is not a letter ("porca-troia", "porca. troia")."""
    parts = []
    for token in word.split():
        parts.append("".join(re.escape(c) + "+" for c in token))
    body = r"[\W_]+".join(parts)
    if wordlists.is_spaceless(word):       # Chinese, Japanese, Korean, Thai: no spaces between words, so look for it anywhere
        return re.compile(body)
    return re.compile(r"(?<!\w)" + body + r"(?!\w)")


def blocked_word_in(text):
    """The first listed word or phrase found in the text, or None. Works in any language (the text is compared as
    Unicode); see _pattern and LEET for the spelling tricks that are seen through."""
    haystack = fold(text)
    if not haystack:
        return None
    versions = [haystack] + ([haystack.translate(LEET)] if haystack.translate(LEET) != haystack else [])
    for (word,) in execute("SELECT word FROM blocked_words ORDER BY id").fetchall():
        pattern = _pattern(word)
        if any(pattern.search(version) for version in versions):
            return word
    return None


def seed_default_lists():
    """First start of a site: add the starter lists named in DEFAULT_WORD_LISTS, so that swearing is filtered without anyone
    having to find the admin page. Done once (a line in the moderation log is the marker), so words an admin removes later
    do not come back at the next start. Unknown language codes are ignored."""
    codes = [c.strip() for c in str(current_app.config.get("DEFAULT_WORD_LISTS", "")).split(",")
             if wordlists.has(c.strip())]
    if not codes or execute("SELECT 1 FROM mod_log WHERE action = 'seed_word_lists'").fetchone():
        return 0
    added = sum(add_starter_list(code, None) for code in codes)
    execute("INSERT INTO mod_log (actor_id, actor_name, action, target, detail, created_at)"
            " VALUES (NULL, 'system', 'seed_word_lists', :t, :d, :now)", t=",".join(codes)[:60], d="%d words" % added, now=now_str())
    commit()
    bac_log("review", "starter blocked-word lists added on first start: %s (%d words)" % (",".join(codes), added))
    return added


def add_starter_list(code, actor_id):
    """Add a language's starter list to the blocked words (entries already there, or beyond the size limit, are skipped).
    Returns how many were added. Does not commit."""
    added = 0
    have = execute("SELECT count(*) FROM blocked_words").scalar() or 0
    for entry in wordlists.entries(code):
        word = clean_word(entry)
        if len(word) < 2 or have + added >= MAX_WORDS:
            continue
        if execute("SELECT 1 FROM blocked_words WHERE word = :w", w=word).fetchone():
            continue
        execute("INSERT INTO blocked_words (word, created_by, created_at) VALUES (:w, :u, :now)", w=word, u=actor_id, now=now_str())
        added += 1
    return added


def _account_age_hours(user):
    try:
        created = datetime.strptime(user["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return 10 ** 6
    return (datetime.now(timezone.utc) - created).total_seconds() / 3600.0


def screen(user, text, kind="post", has_image=False):
    """Why this content must wait for a moderator (a short sentence for the moderators), or None when it may go straight up."""
    if is_staff(user):
        return None
    word = blocked_word_in(text)
    if word:
        return "Blocked word: %s" % word
    cfg = current_app.config
    if (has_image or LINK.search(text or "")) and _account_age_hours(user) < cfg["NEW_MEMBER_HOLD_HOURS"]:
        return "New member (under %dh): %s" % (cfg["NEW_MEMBER_HOLD_HOURS"], "photo" if has_image else "link")
    if kind == "post" and cfg["NEW_MEMBER_HOLD_POSTS"]:
        approved = execute("SELECT count(*) FROM posts WHERE user_id = :u AND mod_state = 'ok'", u=user["id"]).scalar() or 0
        if approved < cfg["NEW_MEMBER_HOLD_POSTS"]:
            return "New member: post %d of the first %d" % (approved + 1, cfg["NEW_MEMBER_HOLD_POSTS"])
    return None


# ------------------------------------------------------------------ the queue
def enqueue(target_type, target_id, author_id, reason, text):
    """Add one open item (never two for the same content). Does not commit."""
    if execute("SELECT 1 FROM review_items WHERE target_type = :t AND target_id = :i AND status = 'open'",
               t=target_type, i=target_id).fetchone():
        return
    insert(review_items, target_type=target_type, target_id=target_id, author_id=author_id, reason=reason[:160],
           snapshot=(text or "")[:1000], status="open", created_at=now_str())
    bac_log("review", "%s %s waits for review (%s)" % (target_type, target_id, reason))


def _set_state(target_type, target_id, state):
    table = TARGETS[target_type][0]
    execute("UPDATE %s SET mod_state = :s WHERE id = :i" % table, s=state, i=target_id)


def exists(target_type, target_id):
    table = TARGETS[target_type][0]
    return execute("SELECT 1 FROM %s WHERE id = :i" % table, i=target_id).fetchone() is not None


def open_count():
    """How many items wait for a moderator: review items plus open reports."""
    return ((execute("SELECT count(*) FROM review_items WHERE status = 'open'").scalar() or 0)
            + (execute("SELECT count(*) FROM reports WHERE status = 'open'").scalar() or 0))


def open_items(limit=100):
    """The items to look at, oldest first. Items whose content no longer exists (the author deleted it) are closed here."""
    items = []
    for row in execute("SELECT r.*, u.username AS author FROM review_items r LEFT JOIN users u ON u.id = r.author_id"
                       " WHERE r.status = 'open' ORDER BY r.id LIMIT :n", n=limit).mappings().fetchall():
        if not exists(row["target_type"], row["target_id"]):
            close(row["id"], "gone", None)
            continue
        item = dict(row)
        if row["target_type"] == "post":
            extra = execute("SELECT image_filename, event_at, event_place FROM posts WHERE id = :i", i=row["target_id"]).mappings().fetchone()
            item.update(extra or {})
        items.append(item)
    commit()
    return items


def close(item_id, resolution, actor_id):
    execute("UPDATE review_items SET status = 'closed', resolution = :r, resolved_by = :a, resolved_at = :now WHERE id = :i",
            r=resolution, a=actor_id, now=now_str(), i=item_id)


def close_open_items(target_type, target_id, resolution, actor_id):
    execute("UPDATE review_items SET status = 'closed', resolution = :r, resolved_by = :a, resolved_at = :now"
            " WHERE status = 'open' AND target_type = :t AND target_id = :i",
            r=resolution, a=actor_id, now=now_str(), t=target_type, i=target_id)


def close_reports(target_type, target_id, resolution, actor_id):
    execute("UPDATE reports SET status = 'closed', resolution = :r, resolved_by = :a, resolved_at = :now"
            " WHERE status = 'open' AND target_type = :t AND target_id = :i",
            r=resolution, a=actor_id, now=now_str(), t=target_type, i=target_id)


def approve(target_type, target_id, actor):
    """Let the content go up. Reports about it are closed as dismissed: a moderator looked and it is fine."""
    if exists(target_type, target_id):
        _set_state(target_type, target_id, "ok")
    close_open_items(target_type, target_id, "approved", actor["id"])
    close_reports(target_type, target_id, "dismissed", actor["id"])
    commit()
    modlog.record("approve_" + target_type, "%s %d" % (target_type, target_id))


def restore_if_hidden(target_type, target_id, actor):
    """A report was dismissed: content that the reports had hidden comes back."""
    if target_type in TARGETS and exists(target_type, target_id):
        table = TARGETS[target_type][0]
        state = execute("SELECT mod_state FROM %s WHERE id = :i" % table, i=target_id).scalar()
        if state == "hidden":
            _set_state(target_type, target_id, "ok")
            close_open_items(target_type, target_id, "approved", actor["id"])
            commit()


def remove(target_type, target_id, actor, resolution="removed"):
    """Delete the content for good and close everything about it. Returns the author's id (or None)."""
    from .feed import delete_post_rows, remove_image
    author_id, photo = None, None
    if target_type == "post":
        row = execute("SELECT id, user_id, image_filename FROM posts WHERE id = :i", i=target_id).mappings().fetchone()
        if row:
            author_id, photo = row["user_id"], row["image_filename"]
            delete_post_rows(row)
    elif exists(target_type, target_id):
        table = TARGETS[target_type][0]
        author_id = execute("SELECT user_id FROM %s WHERE id = :i" % table, i=target_id).scalar()
        execute("DELETE FROM %s WHERE id = :i" % table, i=target_id)
    close_open_items(target_type, target_id, resolution, actor["id"])
    close_reports(target_type, target_id, "removed", actor["id"])
    commit()
    if photo:
        remove_image(photo)
    modlog.record("remove_" + {"gigcomment": "gig_comment"}.get(target_type, target_type), "%s %d" % (target_type, target_id),
                  "review")
    return author_id


# ------------------------------------------------------------------ reports hide content
def maybe_autohide(target_type, target_id):
    """After a report: hide the content when enough different, established members reported it. Returns True if hidden."""
    threshold = current_app.config["REPORTS_AUTOHIDE"]
    if not threshold or target_type not in TARGETS or not exists(target_type, target_id):
        return False
    table = TARGETS[target_type][0]
    row = execute("SELECT mod_state, user_id, body FROM %s WHERE id = :i" % table, i=target_id).fetchone()
    if row is None or row[0] != "ok":
        return False
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=TRUSTED_REPORTER_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    reporters = execute(
        "SELECT count(DISTINCT r.reporter_id) FROM reports r JOIN users u ON u.id = r.reporter_id"
        " WHERE r.status = 'open' AND r.target_type = :t AND r.target_id = :i AND u.created_at < :cutoff",
        t=target_type, i=target_id, cutoff=cutoff).scalar() or 0
    if reporters < threshold:
        return False
    _set_state(target_type, target_id, "hidden")
    enqueue(target_type, target_id, row[1], "Reported by %d members" % reporters, row[2])
    commit()
    bac_log("review", "%s %s hidden after %d reports" % (target_type, target_id, reporters))
    return True


# ------------------------------------------------------------------ the blocked-words list
def clean_word(text):
    return fold(text)[:MAX_WORD_LENGTH]
