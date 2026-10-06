# File: cli.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Command-line tools for the operator:  flask make-admin <username>,  flask seed-demo."""
import click
from flask.cli import with_appcontext

from .baclog import bac_log
from .db import commit, execute


def _set_role(username, role):
    row = execute("SELECT id FROM users WHERE username = :u", u=username).fetchone()
    if row is None:
        raise click.ClickException("No member called %r." % username)
    execute("UPDATE users SET role = :r WHERE id = :id", r=role, id=row[0])
    commit()
    bac_log("cli", "%s is now %s" % (username, role))
    click.echo("%s is now %s." % (username, role))


@click.command("make-admin")
@click.argument("username")
@with_appcontext
def make_admin(username):
    """Give a member access to the admin console at /admin."""
    _set_role(username, "admin")


@click.command("remove-admin")
@click.argument("username")
@with_appcontext
def remove_admin(username):
    """Take admin access away from a member."""
    _set_role(username, "member")


@click.command("make-moderator")
@click.argument("username")
@with_appcontext
def make_moderator(username):
    """Let a member work the review queue and the reports (but not suspend or erase anyone)."""
    _set_role(username, "moderator")


@click.command("remove-moderator")
@click.argument("username")
@with_appcontext
def remove_moderator(username):
    """Take moderator access away from a member."""
    _set_role(username, "member")


@click.command("seed-demo")
@click.option("--password", default="DemoPass-2026", show_default=True,
              help="Password for every demo account.")
@with_appcontext
def seed_demo(password):
    """Fill an empty site with demo bands, venues, fans, gigs, photos and chats (for demos and screenshots)."""
    from . import demo
    if not demo.seed(password):
        click.echo("Demo accounts already exist, nothing changed.")
        return
    click.echo("Demo data created. Sign in as 'the_hollow_kings' (band), 'the_basement_bar' (venue) or "
               "'riff_rita' (fan) with the password you chose.")


@click.command("import-events")
@click.option("--area", default=None, help="Only this area of IMPORT_AREAS.")
@click.option("--provider", default=None, help="Only this provider: ticketmaster, skiddle, songkick, predicthq or bandsintown.")
@click.option("--dry-run", is_flag=True, help="Fetch and count, but store nothing.")
@with_appcontext
def import_events(area, provider, dry_run):
    """Import upcoming concerts for the areas in IMPORT_AREAS and the connected Bandsintown pages (run it from cron).

    Not needed for the gigs near a member's search: those are fetched when they search (IMPORT_ON_DEMAND).
    """
    from . import importer
    try:
        results = importer.run(only=area, dry_run=dry_run, provider=provider)
    except importer.ImportFailed as exc:
        raise click.ClickException(str(exc))
    failed = False
    for r in results:
        label = "%s (%s)" % (r["area"], r.get("provider", "ticketmaster"))
        if r["error"]:
            failed = True
            click.echo("%-30s FAILED: %s" % (label, r["error"]))
        else:
            click.echo("%-30s %d found%s: %d new, %d updated, %d removed" % (
                label, r["fetched"], " (dry run)" if dry_run else "", r["new"], r["updated"], r["removed"]))
    if failed:
        raise SystemExit(1)   # so cron / monitoring notices


@click.command("prune-events")
@with_appcontext
def prune_events():
    """Delete imported events that are over or too old to keep (import-events does this too)."""
    from . import importer
    click.echo("%d imported event(s) removed." % importer.prune())


def setup_report(online=False):
    """Facts about this installation as (label, text, problem) rows; `problem` marks what stops gigs from showing."""
    from flask import current_app
    from . import geo, importer
    cfg = current_app.config
    count = lambda sql: execute(sql).scalar() or 0  # noqa: E731
    rows = []
    gigs = count("SELECT count(*) FROM posts WHERE event_at IS NOT NULL AND latitude IS NOT NULL")
    imported = count("SELECT count(*) FROM external_events")
    rows.append(("Members", str(count("SELECT count(*) FROM users")), False))
    rows.append(("Gigs by members", "%d with a map position (%d announced in total)" % (
        gigs, count("SELECT count(*) FROM posts WHERE event_at IS NOT NULL")), False))
    rows.append(("Imported events", str(imported), False))
    if cfg["GEOCODER"] == "none":
        rows.append(("Place lookups", "OFF (GEOCODER=none): typed towns cannot be found", True))
    elif geo.contact_is_placeholder(cfg["CONTACT_EMAIL"]) and "nominatim.openstreetmap.org" in cfg["GEOCODER_URL"]:
        rows.append(("Place lookups", "on, but CONTACT_EMAIL (%s) is a placeholder: OpenStreetMap will refuse "
                     "every request. Set your real address." % cfg["CONTACT_EMAIL"], True))
    else:
        rows.append(("Place lookups", "on (%s)" % cfg["GEOCODER"], False))

    enabled = importer.enabled_providers(cfg)
    for provider in importer.PROVIDERS.values():
        on = bool(cfg[provider.key_setting])
        if provider.name == "ticketmaster":
            rows.append(("Ticketmaster key", "set" if on else "NOT SET: no concerts can be imported", not enabled))
        else:
            rows.append((provider.label + " key", "set" if on else "not set (optional)", False))
    artists = len(importer.connected_artists())
    rows.append(("Bandsintown", "%d band(s) connected their own page" % artists, False))
    on_demand = cfg["IMPORT_ON_DEMAND"] and bool(enabled)
    rows.append(("On-demand import", ("on: any city is fetched when someone searches there (%d area(s) known)" %
                 count("SELECT count(*) FROM import_coverage")) if on_demand else
                 ("off (IMPORT_ON_DEMAND=0)" if enabled else "off: needs a provider key"), False))
    has_areas = bool(cfg["IMPORT_AREAS"])
    try:
        areas = ", ".join("%s (%d km)" % (a.name, a.radius_km) for a in importer.parse_areas(cfg["IMPORT_AREAS"]))
    except importer.ImportFailed as exc:
        areas, has_areas = "INVALID: %s" % exc, False
    if has_areas:
        rows.append(("Import areas", areas, False))
    elif areas:
        rows.append(("Import areas", areas, True))
    elif on_demand:
        rows.append(("Import areas", "none set (fine: areas are fetched on demand)", False))
    else:
        rows.append(("Import areas", "NOT SET: say where your scene is", True))

    if online:
        for provider in enabled:
            ok, message = importer.check(provider.name)
            rows.append(("Key check" if provider.name == "ticketmaster" else provider.label + " check", message, not ok))
        if cfg["GEOCODER"] != "none":
            try:
                found = geo._fetch("london")
                rows.append(("Place check", "the place service answered (London -> %s)" % (
                    "found" if found else "not found"), found is None))
            except geo.GeocoderUnavailable as exc:
                rows.append(("Place check", str(exc), True))
    if gigs + imported == 0 and not on_demand:
        rows.append(("Result", "there are NO gigs to find yet, so every 'near me' search is empty", True))
    return rows


@click.command("doctor")
@click.option("--online", is_flag=True, help="Also test every provider key (one API call each).")
@with_appcontext
def doctor(online):
    """Show why 'gigs near me' is empty and what to do about it."""
    rows = setup_report(online)
    for label, text, problem in rows:
        click.echo("%-18s %s%s" % (label, "[!] " if problem else "", text))
    click.echo("")
    if any(problem for _, _, problem in rows):
        click.echo("Next steps: see docs/EVENT-IMPORT.md. In short: put TICKETMASTER_API_KEY in the .env file "
                   "(free, worldwide), restart, and search for gigs in any city.")
    else:
        click.echo("Everything needed is in place.")


def init_app(app):
    for command in (make_admin, remove_admin, make_moderator, remove_moderator, seed_demo, import_events, prune_events, doctor):
        app.cli.add_command(command)
