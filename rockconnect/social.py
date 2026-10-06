# File: social.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""The social layer: say you are going to a gig, see who else is, and find people by what they play and like.

Pages:  POST /gigs/attendance  (I'm going / interested / not going)      GET /gigs/mine  (my plans)
API:    POST /api/v1/gigs/<source>/<ref>/attendance      GET /api/v1/gigs/<source>/<ref>/attendees
        GET  /api/v1/people      GET /api/v1/me/gigs      GET /api/v1/lists (public)

A gig is identified by (source, ref): source "community" with the post id, or a provider ("ticketmaster", ...)
with the provider's own event id. The same concert is often listed by two sources; people who said they are going
on different listings of it still see each other (gigmatch.same_gig decides).

Privacy rules, all enforced here:
  * suspended members, blocked members (either way) and members who hid all their plans never appear in lists
    or counts, and a single RSVP can be private (counted, but not listed by name);
  * an RSVP keeps only a small snapshot of the gig, deleted a day after the gig for imported events.
"""
from datetime import datetime, timedelta, timezone

from flask import Blueprint, flash, g, jsonify, redirect, render_template, request, url_for

from . import ages, gigmatch, notifications, ratelimit, taxonomy
from .api import ApiError, api_login_required
from .auth import login_required
from .baclog import bac_log
from .db import attendances, commit, execute, insert
from .feed import POST_SELECT, VISIBLE_AUTHOR
from .util import event_label, event_time, now_str, safe_next

bp = Blueprint("social", __name__)

GOING, INTERESTED = "going", "interested"
STATUSES = (GOING, INTERESTED)
PEOPLE_LIMIT = 100
GRACE_HOURS = 12          # a gig can still be joined until the morning after it starts


class SocialError(Exception):
    """A request that makes no sense (unknown gig, gig is over...): shown to the member / returned as JSON."""

    def __init__(self, message, code="bad_request", status=400):
        super().__init__(message)
        self.message, self.code, self.status = message, code, status


# ------------------------------------------------------------------ finding a gig
def gig_info(source, ref, viewer_id):
    """The gig as a dictionary (also the snapshot stored with an RSVP), or None when it does not exist / is hidden."""
    ref = str(ref)
    if source == "community":
        if not ref.isdigit():
            return None
        row = execute(POST_SELECT + " AND p.mod_state = 'ok' AND p.id = :pid AND p.event_at IS NOT NULL", me=viewer_id,
                      pid=int(ref)).mappings().fetchone()
        if row is None:
            return None
        return {"source": "community", "ref": ref, "title": row["author_name"], "venue": row["event_place"] or "",
                "city": "", "event_at": row["event_at"], "latitude": row["latitude"], "longitude": row["longitude"],
                "genre": row["genre"], "url": url_for("feed.single_post", post_id=row["id"]), "ticket_url": None}
    row = execute("SELECT * FROM external_events WHERE source = :s AND external_id = :e", s=source, e=ref
                  ).mappings().fetchone()
    if row is None:
        return None
    return {"source": row["source"], "ref": row["external_id"], "title": row["title"], "venue": row["venue"],
            "city": row["city"], "event_at": row["event_at"], "latitude": row["latitude"],
            "longitude": row["longitude"], "genre": taxonomy.genre_key(row["genre"]),
            "url": url_for("events.event_page", event_id=row["id"]), "ticket_url": row["ticket_url"]}


def followed_ids(user_id):
    """The ids of the members this member follows."""
    return {r[0] for r in execute("SELECT followed_id FROM follows WHERE follower_id = :u", u=user_id)}


def _viewer_row(viewer_id):
    """The signed-in member as a row with birth_date (g.user, or a lookup when another id is asked about)."""
    if g.user is not None and g.user["id"] == viewer_id:
        return g.user
    return {"birth_date": execute("SELECT birth_date FROM users WHERE id = :id", id=viewer_id).scalar()}


def is_over(event_at):
    limit = (datetime.now(timezone.utc) - timedelta(hours=GRACE_HOURS)).strftime("%Y-%m-%d %H:%M")
    return event_at < limit


def _matches(row, gig):
    """Does this RSVP row belong to this gig (the same listing, or another listing of the same concert)?"""
    if row["source"] == gig["source"] and row["event_ref"] == gig["ref"]:
        return True
    return gigmatch.same_gig(
        {"source": row["source"], "title": row["title"], "event_at": row["event_at"],
         "latitude": row["latitude"], "longitude": row["longitude"]}, gig)


def _near(gig):
    """SQL for 'could be this gig': the same listing, or on the same map spot (the exact test is _matches)."""
    sql = " (a.source = :g_source AND a.event_ref = :g_ref"
    params = {"g_source": gig["source"], "g_ref": gig["ref"]}
    if gig.get("latitude") is not None:
        sql += (" OR (a.latitude BETWEEN :g_lat1 AND :g_lat2 AND a.longitude BETWEEN :g_lon1 AND :g_lon2))")
        params.update(g_lat1=gig["latitude"] - 0.003, g_lat2=gig["latitude"] + 0.003,
                      g_lon1=gig["longitude"] - 0.005, g_lon2=gig["longitude"] + 0.005)
    else:
        sql += ")"
    return sql, params


# ------------------------------------------------------------------ going / interested
def set_attendance(user_id, gig, status, visible=None):
    """Record (or remove) my plan for a gig. `status` is going, interested or None. Returns the stored status."""
    day = gig["event_at"][:10]
    mine = [r for r in execute(
        "SELECT * FROM attendances a WHERE a.user_id = :u AND substr(a.event_at, 1, 10) = :day",
        u=user_id, day=day).mappings() if _matches(r, gig)]
    if status is None:
        for row in mine:
            execute("DELETE FROM attendances WHERE id = :id", id=row["id"])
        commit()
        return None
    if is_over(gig["event_at"]):
        raise SocialError("That gig is over.", "gig_over")
    final_visible = 1 if visible is None else int(bool(visible))
    if mine:
        keep = mine[0]
        for extra in mine[1:]:                       # said it twice on two listings of one concert: keep one
            execute("DELETE FROM attendances WHERE id = :id", id=extra["id"])
        final_visible = keep["visible"] if visible is None else int(bool(visible))
        execute("UPDATE attendances SET status = :s, visible = :v WHERE id = :id", s=status, v=final_visible, id=keep["id"])
    else:
        insert(attendances, user_id=user_id, source=gig["source"], event_ref=gig["ref"], status=status,
               visible=1 if visible is None else int(bool(visible)), title=gig["title"][:255],
               venue=(gig["venue"] or "")[:160], city=(gig["city"] or "")[:120], event_at=gig["event_at"],
               latitude=gig["latitude"], longitude=gig["longitude"], genre=gig["genre"], created_at=now_str())
    commit()
    bac_log("social", "user id=%s is %s for a gig from %s" % (user_id, status, gig["source"]))
    if status == GOING and final_visible:            # a private plan tells nobody
        actor = g.user if g.user is not None and g.user["id"] == user_id else \
            execute("SELECT * FROM users WHERE id = :id", id=user_id).mappings().fetchone()
        notifications.friend_going(actor, gig)
    return status


def counts_for(gigs, viewer_id):
    """{(source, ref): {"going": n, "interested": n, "mine": status or None, "visible": bool}} for these gigs.

    Counted: everybody active who is not blocked either way and has not hidden all their plans (a private single
    RSVP still counts, it is just not listed by name)."""
    gigs = [x for x in gigs if x.get("event_at")]
    if not gigs:
        return {}
    days = sorted({x["event_at"][:10] for x in gigs})
    side, side_params = ages.side_clause(_viewer_row(viewer_id))
    rows = execute("SELECT a.* FROM attendances a JOIN users u ON u.id = a.user_id WHERE u.hide_plans = 0 AND "
                   "substr(a.event_at, 1, 10) BETWEEN :lo AND :hi AND " + VISIBLE_AUTHOR + side,
                   me=viewer_id, lo=days[0], hi=days[-1], **side_params).mappings().fetchall()
    mine_rows = execute("SELECT * FROM attendances WHERE user_id = :u AND substr(event_at, 1, 10) BETWEEN :lo AND :hi",
                        u=viewer_id, lo=days[0], hi=days[-1]).mappings().fetchall()
    result = {}
    followed = followed_ids(viewer_id)
    for gig in gigs:
        entry = {"going": 0, "interested": 0, "mine": None, "visible": True, "friends_going": 0}
        for row in rows:
            if row["user_id"] != viewer_id and _matches(row, gig):
                entry[row["status"]] += 1
                if row["status"] == GOING and row["visible"] and row["user_id"] in followed:
                    entry["friends_going"] += 1
        for row in mine_rows:
            if _matches(row, gig):
                entry["mine"], entry["visible"] = row["status"], bool(row["visible"])
                entry[row["status"]] += 1
                break
        result[(gig["source"], gig["ref"])] = entry
    return result


def post_attendance(posts, viewer_id):
    """Counts for the member-announced gigs among `posts`, keyed by the post id as text (for the templates)."""
    gigs = [{"source": "community", "ref": str(p["id"]), "title": p["author_name"], "event_at": p["event_at"],
             "latitude": p["latitude"], "longitude": p["longitude"]} for p in posts if p["event_at"]]
    return {ref: entry for (source, ref), entry in counts_for(gigs, viewer_id).items()}


# ------------------------------------------------------------------ people
def _tags_for(user_ids):
    """{user_id: {"instruments": [...], "instrument_levels": {instrument: level or None}, "genres": [...],
    "goals": [...]}} (keys of the lists in taxonomy.py)."""
    tags = {uid: {"instruments": [], "instrument_levels": {}, "genres": [], "goals": []} for uid in user_ids}
    if not tags:
        return tags
    marks = {"i%d" % n: uid for n, uid in enumerate(tags)}
    where = " WHERE user_id IN (%s) ORDER BY 2" % ", ".join(":" + k for k in marks)
    for uid, instrument, level in execute("SELECT user_id, instrument, level FROM user_instruments" + where, **marks):
        tags[uid]["instruments"].append(instrument)
        tags[uid]["instrument_levels"][instrument] = level
    for table, column, name in (("user_genres", "genre", "genres"), ("user_goals", "goal", "goals")):
        for uid, value in execute("SELECT user_id, %s FROM %s" % (column, table) + where, **marks):
            tags[uid][name].append(value)
    return tags


def user_tags(user_id):
    return _tags_for([user_id])[user_id]


def save_tags(user_id, instruments, genres, goals):
    """Replace a member's instruments, genres and goals with the (validated) lists given.

    `instruments` is a list of keys, or a dict {key: level} (level one of taxonomy.LEVELS, or empty = not said)."""
    levels = dict(instruments) if isinstance(instruments, dict) else {key: None for key in instruments or []}
    execute("DELETE FROM user_instruments WHERE user_id = :u", u=user_id)
    for key in taxonomy.only_valid(list(levels), taxonomy.INSTRUMENTS):
        level = levels[key] if levels[key] in taxonomy.LEVELS else None
        execute("INSERT INTO user_instruments (user_id, instrument, level) VALUES (:u, :v, :l)", u=user_id, v=key, l=level)
    for table, column, values, allowed in (
            ("user_genres", "genre", genres, taxonomy.GENRES), ("user_goals", "goal", goals, taxonomy.GOALS)):
        execute("DELETE FROM %s WHERE user_id = :u" % table, u=user_id)
        for value in taxonomy.only_valid(values, allowed):
            execute("INSERT INTO %s (user_id, %s) VALUES (:u, :v)" % (table, column), u=user_id, v=value)
    commit()


_FILTERS = (("instrument", "user_instruments", "instrument", taxonomy.INSTRUMENTS),
            ("genre", "user_genres", "genre", taxonomy.GENRES),
            ("goal", "user_goals", "goal", taxonomy.GOALS))


def _filter_sql(filters):
    """EXISTS clauses for the instrument / genre / goal filters (validated: unknown values filter nothing out).

    `level` means "at least this good"; together with `instrument` it applies to that instrument, alone to any of them."""
    sql, params = "", {}
    filters = filters or {}
    level = filters.get("level")
    for name, table, column, allowed in _FILTERS:
        value = filters.get(name)
        wants_level = name == "instrument" and level in taxonomy.LEVELS
        if value not in allowed and not wants_level:
            continue
        clause = "t_%s.user_id = u.id" % name
        if value in allowed:
            clause += " AND t_%s.%s = :f_%s" % (name, column, name)
            params["f_" + name] = value
        if wants_level:
            good = taxonomy.LEVEL_ORDER[taxonomy.LEVEL_ORDER.index(level):]
            clause += " AND t_instrument.level IN (%s)" % ", ".join(":lv%d" % n for n in range(len(good)))
            params.update({"lv%d" % n: key for n, key in enumerate(good)})
        sql += " AND EXISTS (SELECT 1 FROM %s t_%s WHERE %s)" % (table, name, clause)
    return sql, params


def person(row, tags, status=None):
    """A member as shown in lists and returned by the API (never the e-mail or anything private)."""
    return {"id": row["id"], "username": row["username"], "name": row["name"], "kind": row["kind"],
            "location": row["location"], "instruments": tags["instruments"],
            "instrument_levels": tags["instrument_levels"], "genres": tags["genres"], "goals": tags["goals"],
            "status": status, "following": False}


def search_people(viewer, filters=None, q="", kind="", include_suspended=False, limit=PEOPLE_LIMIT, offset=0,
                  relation=""):
    """Members matching the filters. Blocked members (either way) are left out.
    `relation` = "following" (members I follow) or "followers" (members who follow me)."""
    sql = ("SELECT u.id, u.username, u.name, u.kind, u.location, u.status FROM users u WHERE 1 = 1"
           " AND u.id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = :me)"
           " AND u.id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = :me)")
    params = {"me": viewer["id"], "lim": limit, "off": offset}
    if not include_suspended:
        sql += " AND u.status = 'active'"
    if q:
        sql += " AND (lower(u.username) LIKE :p OR lower(u.name) LIKE :p OR lower(COALESCE(u.location, '')) LIKE :p)"
        params["p"] = "%" + q.lower() + "%"
    if kind in ("fan", "band", "venue"):
        sql += " AND u.kind = :kind"
        params["kind"] = kind
    if relation == "following":
        sql += " AND u.id IN (SELECT followed_id FROM follows WHERE follower_id = :me)"
    elif relation == "followers":
        sql += " AND u.id IN (SELECT follower_id FROM follows WHERE followed_id = :me)"
    extra, extra_params = _filter_sql(filters)
    side, side_params = ages.side_clause(viewer)
    rows = execute(sql + extra + side + " ORDER BY u.username LIMIT :lim OFFSET :off",
                   **params, **extra_params, **side_params).mappings().fetchall()
    tags = _tags_for([r["id"] for r in rows])
    followed = followed_ids(viewer["id"])
    return [dict(person(r, tags[r["id"]]), following=r["id"] in followed) for r in rows]


def attendees(gig, viewer_id, filters=None, status=None, limit=PEOPLE_LIMIT):
    """Who else is going to (or interested in) this concert, listed by name: only public RSVPs of members who did
    not hide their plans. Filters: instrument, genre, goal."""
    near, near_params = _near(gig)
    extra, extra_params = _filter_sql(filters)
    side, side_params = ages.side_clause(_viewer_row(viewer_id))
    sql = ("SELECT a.status AS rsvp, a.source, a.event_ref, a.title, a.event_at, a.latitude, a.longitude,"
           " u.id, u.username, u.name, u.kind, u.location FROM attendances a JOIN users u ON u.id = a.user_id"
           " WHERE substr(a.event_at, 1, 10) = :day AND a.visible = 1 AND u.hide_plans = 0 AND u.id <> :me"
           " AND " + VISIBLE_AUTHOR + side + " AND" + near)
    params = {"me": viewer_id, "day": gig["event_at"][:10], **near_params, **extra_params, **side_params}
    if status in STATUSES:
        sql += " AND a.status = :status"
        params["status"] = status
    found = {}
    for row in execute(sql + extra + " ORDER BY a.id", **params).mappings():
        if not _matches({"source": row["source"], "event_ref": row["event_ref"], "title": row["title"],
                         "event_at": row["event_at"], "latitude": row["latitude"], "longitude": row["longitude"]}, gig):
            continue
        if row["id"] not in found or row["rsvp"] == GOING:      # going beats interested if both listings were used
            found[row["id"]] = row
    followed = followed_ids(viewer_id)
    rows = sorted(found.values(), key=lambda r: r["id"] not in followed)[:limit]       # people I follow come first
    tags = _tags_for([r["id"] for r in rows])
    return [dict(person(r, tags[r["id"]], r["rsvp"]), following=r["id"] in followed) for r in rows]


def my_plans(user_id, viewer_id=None):
    """Upcoming gigs a member is going to / interested in. When looking at someone else's plans (`viewer_id`), only
    the public ones of a member who did not hide them."""
    sql = ("SELECT a.* FROM attendances a JOIN users u ON u.id = a.user_id WHERE a.user_id = :u AND a.event_at >= :since"
           " AND u.status = 'active'")
    params = {"u": user_id, "since": (datetime.now(timezone.utc) - timedelta(hours=GRACE_HOURS)).strftime("%Y-%m-%d %H:%M")}
    if viewer_id is not None and viewer_id != user_id:
        sql += " AND a.visible = 1 AND u.hide_plans = 0"
    plans = []
    for row in execute(sql + " ORDER BY a.event_at", **params).mappings():
        item = dict(row, label=event_time(row["event_at"]), url=None)
        if row["source"] == "community":
            item["url"] = url_for("feed.single_post", post_id=int(row["event_ref"])) if row["event_ref"].isdigit() else None
        else:
            found = execute("SELECT id FROM external_events WHERE source = :s AND external_id = :e",
                            s=row["source"], e=row["event_ref"]).fetchone()
            item["url"] = url_for("events.event_page", event_id=found[0]) if found else None
        plans.append(item)
    return plans


def prune_attendances():
    """Delete the snapshots of imported gigs a day after the gig, and of members' own gigs after a month."""
    now = datetime.now(timezone.utc)
    removed = execute("DELETE FROM attendances WHERE source <> 'community' AND event_at < :d",
                      d=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")).rowcount or 0
    removed += execute("DELETE FROM attendances WHERE source = 'community' AND event_at < :d",
                       d=(now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M")).rowcount or 0
    removed += execute("DELETE FROM gig_comments WHERE source <> 'community' AND event_at < :d",
                       d=(now - timedelta(days=1)).strftime("%Y-%m-%d %H:%M")).rowcount or 0
    removed += execute("DELETE FROM gig_comments WHERE source = 'community' AND event_at < :d",
                       d=(now - timedelta(days=30)).strftime("%Y-%m-%d %H:%M")).rowcount or 0
    commit()
    notifications.prune()
    return removed


# ------------------------------------------------------------------ the panel on a gig's page
def panel(gig, viewer_id, args):
    """Everything the 'who is going' box needs: my plan, the counts, and the people (filtered by the query string)."""
    filters = {name: args.get(name, "") for name, *_ in _FILTERS}
    filters["level"] = args.get("level", "") if args.get("level") in taxonomy.LEVELS else ""
    status = args.get("rsvp") if args.get("rsvp") in STATUSES else None
    from . import gigtalk     # imported here: gigtalk.py itself imports this module
    counts = counts_for([gig], viewer_id)[(gig["source"], gig["ref"])]
    return {"gig": gig, "counts": counts, "people": attendees(gig, viewer_id, filters, status), "filters": filters,
            "comments": gigtalk.thread(gig, g.user),
            "rsvp_filter": status or "", "instruments": taxonomy.INSTRUMENTS, "genres": taxonomy.GENRES,
            "goals": taxonomy.GOALS, "levels": taxonomy.LEVELS, "over": is_over(gig["event_at"])}


# ------------------------------------------------------------------ pages
def _respond_attendance(gig, status):
    entry = counts_for([gig], g.user["id"])[(gig["source"], gig["ref"])]
    return {"source": gig["source"], "ref": gig["ref"], "status": status, "visible": entry["visible"],
            "going_count": entry["going"], "interested_count": entry["interested"]}


def _change_attendance(source, ref, form):
    """Shared by the page and the API: validate, save, and return the new state."""
    ratelimit.allow("attend_user", g.user["id"])
    status = form.get("status") or None
    if status == "none":
        status = None
    visible_raw = form.get("visible")
    if "status" not in form and visible_raw in (None, ""):
        raise SocialError("Send status (going, interested or none) and/or visible.", "missing_status")
    visible = None if visible_raw in (None, "") else visible_raw not in ("0", "false", "no")
    if "status" in form and status is not None and status not in STATUSES:
        raise SocialError("status must be going, interested or none.", "bad_status")
    gig = gig_info(source, ref, g.user["id"])
    if gig is None:
        raise SocialError("That gig does not exist (any more).", "gig_not_found", 404)
    if visible_raw not in (None, "") and "status" not in form:     # only the privacy switch was sent: keep the status
        current = counts_for([gig], g.user["id"])[(gig["source"], gig["ref"])]["mine"]
        status = current
    new = set_attendance(g.user["id"], gig, status, visible)
    return gig, new


@bp.route("/gigs/attendance", methods=("POST",))
@login_required
def attendance():
    nxt = safe_next(request.form.get("next"), url_for("feed.gigs"))
    try:
        gig, new = _change_attendance(request.form.get("source", ""), request.form.get("ref", ""), request.form)
    except SocialError as exc:
        if request.headers.get("X-Requested-With") == "fetch":
            return jsonify(error=exc.message, code=exc.code), exc.status
        flash(exc.message, "warning")
        return redirect(nxt)
    if request.headers.get("X-Requested-With") == "fetch":
        return jsonify(**_respond_attendance(gig, new))
    flash({"going": "You are going. Other members can see it.", "interested": "Marked as interested.",
           None: "Removed from your plans."}[new], "success")
    return redirect(nxt)


@bp.route("/gigs/mine")
@login_required
def mine():
    return render_template("my_gigs.html", plans=my_plans(g.user["id"]))


# ------------------------------------------------------------------ API v1
@bp.route("/api/v1/gigs/<source>/<ref>/attendance", methods=("POST",))
@api_login_required
def api_attendance(source, ref):
    try:
        gig, new = _change_attendance(source, ref, request.form if request.form else (request.get_json(silent=True) or {}))
    except SocialError as exc:
        raise ApiError(exc.message, exc.code, exc.status)
    return jsonify(**_respond_attendance(gig, new))


@bp.route("/api/v1/gigs/<source>/<ref>/attendees")
@api_login_required
def api_attendees(source, ref):
    ratelimit.allow("people_user", g.user["id"])
    gig = gig_info(source, ref, g.user["id"])
    if gig is None:
        raise ApiError("That gig does not exist (any more).", "gig_not_found", 404)
    filters = _api_filters()
    people = attendees(gig, g.user["id"], filters, request.args.get("rsvp"))
    counts = counts_for([gig], g.user["id"])[(source, str(ref))]
    return jsonify(gig={k: gig[k] for k in ("source", "ref", "title", "venue", "city", "event_at")},
                   going_count=counts["going"], interested_count=counts["interested"],
                   my_status=counts["mine"], total=len(people), people=people)


def _api_filters():
    """The instrument / genre / goal / level filters of an API request, validated."""
    filters = {name: request.args.get(name, "") for name, *_ in _FILTERS}
    filters["level"] = request.args.get("level", "")
    _check_filters(filters)
    if filters["level"] and filters["level"] not in taxonomy.LEVELS:
        raise ApiError("level must be one of the keys of /api/v1/lists.", "bad_level")
    return filters


def _check_filters(filters):
    for name, _table, _column, allowed in _FILTERS:
        if filters.get(name) and filters[name] not in allowed:
            raise ApiError("%s is not one of the known values (see /api/v1/lists)." % name, "bad_" + name)


@bp.route("/api/v1/people")
@api_login_required
def api_people():
    ratelimit.allow("people_user", g.user["id"])
    filters = _api_filters()
    limit = max(1, min(PEOPLE_LIMIT, request.args.get("limit", 50, type=int)))
    offset = max(0, request.args.get("offset", 0, type=int))
    people = search_people(g.user, filters, request.args.get("q", "").strip(), request.args.get("kind", ""),
                           limit=limit, offset=offset)
    return jsonify(count=len(people), limit=limit, offset=offset, people=people)


@bp.route("/api/v1/me/gigs")
@api_login_required
def api_my_gigs():
    plans = my_plans(g.user["id"])
    return jsonify(total=len(plans), gigs=[{
        "source": p["source"], "ref": p["event_ref"], "status": p["status"], "visible": bool(p["visible"]),
        "title": p["title"], "venue": p["venue"], "city": p["city"], "event_at": p["event_at"],
        "event_label": event_label(p["event_at"]), "url": p["url"]} for p in plans])


@bp.route("/api/v1/lists")
def api_lists():
    """The fixed lists (keys and labels) for the profile and filter forms. Public."""
    response = jsonify(instruments=taxonomy.INSTRUMENTS, levels=taxonomy.LEVELS, genres=taxonomy.GENRES,
                       goals=taxonomy.GOALS, statuses=list(STATUSES))
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response
