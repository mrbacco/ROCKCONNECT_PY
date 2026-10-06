# File: events.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""The page of one imported event (from 'Gigs near you'): details, a map link and the link to buy tickets.

Admins can hide an event whose data is wrong at the provider (for example a venue pinned in the wrong city). It is
deleted and remembered, so the importer does not bring it back (see importer._hidden_ids and /admin/hidden).
"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for
from sqlalchemy.exc import IntegrityError

from . import importer, modlog, social
from .auth import login_required
from .baclog import bac_log
from .db import commit, execute, hidden_events, insert, rollback
from .util import now_str

bp = Blueprint("events", __name__)


@bp.route("/events/<int:event_id>")
@login_required
def event_page(event_id):
    event = execute("SELECT * FROM external_events WHERE id = :id", id=event_id).mappings().fetchone()
    if event is None:
        abort(404)  # unknown, or already removed by the clean-up of old events
    gig = social.gig_info(event["source"], event["external_id"], g.user["id"])
    return render_template("event.html", e=event, source_label=importer.LABELS.get(event["source"], event["source"]),
                           panel=social.panel(gig, g.user["id"], request.args) if gig else None)


@bp.route("/events/<int:event_id>/hide", methods=("POST",))
@login_required
def hide_event(event_id):
    if g.user["role"] != "admin":
        abort(404)
    event = execute("SELECT id, source, external_id, title, user_id FROM external_events WHERE id = :id",
                    id=event_id).mappings().fetchone()
    if event is None:
        abort(404)
    if event["user_id"]:
        flash("That date belongs to a band's own Bandsintown page: the band manages it.", "warning")
        return redirect(url_for("events.event_page", event_id=event_id))
    reason = request.form.get("reason", "").strip()[:200] or "wrong data at the provider"
    try:
        insert(hidden_events, source=event["source"], external_id=event["external_id"], title=event["title"][:255],
               reason=reason, hidden_by=g.user["username"], created_at=now_str())
    except IntegrityError:
        rollback()   # already hidden once: still remove this copy
    execute("DELETE FROM external_events WHERE id = :id", id=event_id)
    commit()
    modlog.record("hide_event", "%s %s" % (event["source"], event["external_id"]), reason)
    bac_log("events", "event id=%s hidden by an admin" % event_id)
    flash("Hidden: it will not come back when events are refreshed.", "success")
    return redirect(url_for("feed.gigs"))
