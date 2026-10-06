# File: ical.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Add gigs to a calendar: a standard .ics file (RFC 5545) that Google Calendar, Apple Calendar and Outlook all read.

    GET /gigs/<source>/<ref>.ics    one gig
    GET /gigs/mine.ics              all my upcoming plans (going = confirmed, interested = tentative)

Times are the venue's local time and are written as "floating" times (no time zone): the calendar shows the gig at that
wall-clock time, which is what a gig listing means. A gig with only a date (no start time known) becomes an all-day event.
The files are made for the signed-in member, on request: there is no public subscription address, so nothing about
anyone's plans can leak through a link.
"""
import re
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, abort, current_app, g, request

from . import social
from .auth import login_required

bp = Blueprint("ical", __name__)

DEFAULT_HOURS = 3        # providers do not say when a gig ends


def escape(text):
    """Text value of an iCalendar property: backslash, semicolon, comma and line breaks are escaped."""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or ""))
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def fold(line):
    """Lines longer than 75 bytes continue on the next line, which starts with a space (counted in bytes, UTF-8 safe)."""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    parts, current = [], b""
    for char in line:
        piece = char.encode("utf-8")
        if len(current) + len(piece) > (75 if not parts else 74):
            parts.append(current)
            current = b""
        current += piece
    parts.append(current)
    return "\r\n ".join(part.decode("utf-8") for part in parts)


def _stamp(event_at):
    return datetime.strptime(event_at, "%Y-%m-%d %H:%M")


def vevent(gig, status="CONFIRMED", base_url=""):
    """The lines of one VEVENT. `gig` has source, ref, title, venue, city, event_at, latitude, longitude, url, ticket_url."""
    start = _stamp(gig["event_at"])
    lines = ["BEGIN:VEVENT", "UID:gig-%s-%s@%s" % (re.sub(r"[^A-Za-z0-9_.-]", "", gig["source"]),
                                                  re.sub(r"[^A-Za-z0-9_.-]", "", str(gig["ref"])), _host()),
             "DTSTAMP:" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")]
    all_day = gig["source"] != "community" and gig["event_at"].endswith(" 00:00")
    if all_day:
        lines += ["DTSTART;VALUE=DATE:" + start.strftime("%Y%m%d"),
                  "DTEND;VALUE=DATE:" + (start + timedelta(days=1)).strftime("%Y%m%d")]
    else:
        lines += ["DTSTART:" + start.strftime("%Y%m%dT%H%M%S"),
                  "DTEND:" + (start + timedelta(hours=DEFAULT_HOURS)).strftime("%Y%m%dT%H%M%S")]
    lines.append("SUMMARY:" + escape(gig["title"]))
    place = ", ".join(p for p in (gig.get("venue"), gig.get("city")) if p)
    if place:
        lines.append("LOCATION:" + escape(place))
    if gig.get("latitude") is not None and gig.get("longitude") is not None:
        lines.append("GEO:%.5f;%.5f" % (gig["latitude"], gig["longitude"]))
    notes = []
    if gig.get("ticket_url"):
        notes.append("Tickets: " + gig["ticket_url"])
    if gig.get("url"):
        notes.append("Who is going: " + base_url + gig["url"])
    if notes:
        lines.append("DESCRIPTION:" + escape("\n".join(notes)))
    if gig.get("url"):
        lines.append("URL:" + base_url + gig["url"])
    lines += ["STATUS:" + status, "END:VEVENT"]
    return lines


def calendar_text(events, name):
    """A complete calendar. `events` is a list of (gig, status)."""
    base = _base_url()
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//%s//gigs//EN" % escape(current_app.config["SITE_NAME"]),
             "CALSCALE:GREGORIAN", "METHOD:PUBLISH", "X-WR-CALNAME:" + escape(name)]
    for gig, status in events:
        lines += vevent(gig, status, base)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def _base_url():
    return current_app.config["SITE_URL"] or request.url_root.rstrip("/")


def _host():
    base = _base_url()
    return re.sub(r"^https?://", "", base).split("/")[0].split(":")[0] or "localhost"


def _response(text, filename):
    response = Response(text, mimetype="text/calendar")
    response.headers["Content-Disposition"] = 'attachment; filename="%s"' % filename
    response.headers["Cache-Control"] = "private, no-store"
    return response


def _slug(text):
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-")[:40] or "gig"


@bp.route("/gigs/mine.ics")
@login_required
def mine_file():
    events = []
    for plan in social.my_plans(g.user["id"]):
        gig = {"source": plan["source"], "ref": plan["event_ref"], "title": plan["title"], "venue": plan["venue"],
               "city": plan["city"], "event_at": plan["event_at"], "latitude": plan["latitude"],
               "longitude": plan["longitude"], "url": plan["url"], "ticket_url": None}
        events.append((gig, "CONFIRMED" if plan["status"] == "going" else "TENTATIVE"))
    return _response(calendar_text(events, "My gigs"), "my-gigs.ics")


@bp.route("/gigs/<source>/<ref>.ics")
@login_required
def gig_file(source, ref):
    gig = social.gig_info(source, ref, g.user["id"])
    if gig is None:
        abort(404)
    mine = social.counts_for([gig], g.user["id"])[(gig["source"], gig["ref"])]["mine"]
    status = "TENTATIVE" if mine == "interested" else "CONFIRMED"
    return _response(calendar_text([(gig, status)], gig["title"]), "%s.ics" % _slug(gig["title"]))
