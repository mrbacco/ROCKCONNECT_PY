# File: util.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Small shared helpers: timestamps, "5 min ago", safe redirects, image sniffing, asset urls, validation."""
import hashlib
import os
import re
from datetime import datetime, timedelta, timezone

from flask import current_app, request, url_for

# every timestamp is stored as UTC text in this format: sorts correctly, same on every database
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_str():
    return datetime.now(timezone.utc).strftime(TIME_FORMAT)


def in_minutes(minutes):
    """UTC timestamp text `minutes` from now (negative = in the past)."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime(TIME_FORMAT)


def sha256(text):
    """Hex digest. Tokens and rate-limit keys are stored hashed, never in clear."""
    return hashlib.sha256(text.encode()).hexdigest()


def client_ip():
    # behind a proxy TRUST_PROXY=1 makes werkzeug's ProxyFix put the real address here
    return request.remote_addr or "unknown"


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,30}$")


def valid_email(value):
    return bool(value) and len(value) <= 254 and bool(_EMAIL_RE.match(value))


def valid_username(value):
    return bool(_USERNAME_RE.match(value or ""))


def clean_website(value):
    """A safe http(s) link or None. Anything else (javascript:, data:, ...) is dropped: it is shown as a link."""
    value = (value or "").strip()
    if not value:
        return ""
    if not re.match(r"^https?://", value, re.I):
        value = "https://" + value
    if len(value) > 200 or re.search(r"[\s<>\"']", value) or not re.match(r"^https?://[^/\s]+\.[^/\s]+", value, re.I):
        return None
    return value


def external_url(endpoint, **values):
    """Absolute URL for e-mails: SITE_URL when configured, otherwise the address of the current request."""
    path = url_for(endpoint, **values)
    base = current_app.config.get("SITE_URL")
    return base + path if base else request.url_root.rstrip("/") + path


def timeago(value):
    """'just now', '5 min ago', '3 h ago', '2 d ago', else '02 Oct 2026' (Jinja filter)."""
    try:
        then = datetime.strptime(value, TIME_FORMAT).replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return value or ""
    secs = int((datetime.now(timezone.utc) - then).total_seconds())
    if secs < 45:
        return "just now"
    if secs < 3600:
        return "%d min ago" % max(1, secs // 60)
    if secs < 86400:
        return "%d h ago" % (secs // 3600)
    if secs < 7 * 86400:
        return "%d d ago" % (secs // 86400)
    return then.strftime("%d %b %Y")


def event_time(value):
    """'2026-10-12 20:30' -> 'Mon 12 Oct 2026, 20:30' (Jinja filter for gig dates; they are local times, no zone)."""
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M").strftime("%a %d %b %Y, %H:%M")
    except (TypeError, ValueError):
        return value or ""


def event_label(value, time_known=True):
    """Like event_time(), but without the clock time when only the date of the event is known."""
    if time_known:
        return event_time(value)
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M").strftime("%a %d %b %Y")
    except (TypeError, ValueError):
        return value or ""


def safe_next(target, default):
    """Only allow redirects to a path on this site (blocks open redirects like //evil.com)."""
    if target and target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return default


def detect_image_ext(head):
    """File extension from the first bytes of the file, or None if it is not a real image.

    We trust the content, never the uploaded filename or the browser's content-type.
    """
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return ".webp"
    return None


def asset(filename):
    """url_for('static') plus the file's modified time, so browsers reload it when it changes."""
    try:
        version = int(os.path.getmtime(os.path.join(current_app.static_folder, filename)))
    except OSError:
        version = 0
    return url_for("static", filename=filename, v=version)
