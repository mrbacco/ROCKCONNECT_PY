# File: util.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Small shared helpers: timestamps, "5 min ago", safe redirects, image sniffing, asset urls."""
import os
from datetime import datetime, timezone

from flask import current_app, url_for

# every timestamp is stored as UTC text in this format: sorts correctly, same on every database
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


def now_str():
    return datetime.now(timezone.utc).strftime(TIME_FORMAT)


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
