# File: db.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Database layer (SQLAlchemy Core): local SQLite by default, remote DB via DATABASE_URL.

  local  (default) : sqlite:///<instance>/rockconnect.sqlite
  remote           : postgresql://user:pass@host:5432/dbname   (pip install psycopg2-binary)
                     mysql+pymysql://user:pass@host:3306/dbname (pip install pymysql)

The same code and SQL run on all of them. The schema is managed by Alembic migrations
(rockconnect/migrations): on start-up the database is brought to the latest version automatically,
so upgrading to a new release never needs a manual SQL step and never loses data.
"""
import os

import click
from flask import current_app, g
from sqlalchemy import (CheckConstraint, Column, Float, ForeignKey, Integer, MetaData,
                        String, Table, Text, UniqueConstraint, create_engine,
                        event, text)
from sqlalchemy.engine import URL, make_url

from .baclog import bac_log

metadata = MetaData()

# what kind of member an account is: the product is a community for bands, venues and their fans
KINDS = ("fan", "band", "venue")
ROLES = ("member", "admin")

# --- tables (portable types so they work on SQLite, PostgreSQL and MySQL) ---------------
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(80), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("email", String(254), nullable=False, unique=True),
    Column("password", String(100), nullable=False),  # bcrypt hash
    Column("about", Text, nullable=False),
    Column("kind", String(10), nullable=False, server_default="fan"),       # fan | band | venue
    Column("role", String(10), nullable=False, server_default="member"),    # member | admin
    Column("status", String(10), nullable=False, server_default="active"),  # active | banned
    Column("ban_reason", String(255)),
    Column("email_verified", Integer, nullable=False, server_default="0"),  # 0 / 1
    Column("location", String(120)),                                        # city / country
    Column("website", String(200)),                                         # https link
    Column("terms_accepted_at", String(19)),                                # UTC, proof of consent
    Column("created_at", String(19)),                                       # UTC
    # a band can connect its OWN Bandsintown artist page (their terms: for artists, one app id per artist)
    Column("bandsintown_artist", String(120)),
    Column("bandsintown_app_id", String(64)),                               # secret: never shown, logged or exported
    Column("hide_plans", Integer, nullable=False, server_default="0"),      # 1 = never show which gigs I am going to
    Column("is_private", Integer, nullable=False, server_default="0"),      # 1 = posts and plans only for members I accepted
    Column("birth_date", String(10)),                                       # "YYYY-MM-DD", only used to check the age: never shown
    Column("notify_friends_going", Integer, nullable=False, server_default="1"),   # tell me when people I follow go to a gig
)

# one conversation per pair of users; ids are stored low < high so the pair is unique
conversations = Table(
    "conversations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_low_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("user_high_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("created_at", String(19), nullable=False),  # UTC "YYYY-MM-DD HH:MM:SS"
    # a first message to someone new is a REQUEST the other person accepts (or declines) before it becomes a chat
    Column("status", String(10), nullable=False, server_default="accepted"),   # accepted | pending | declined
    Column("initiator_id", Integer),                    # who sent the request (NULL for chats from before requests)
    UniqueConstraint("user_low_id", "user_high_id", name="uq_conversation_pair"),
    CheckConstraint("user_low_id < user_high_id", name="ck_conversation_order"),
)

messages = Table(
    "messages", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("conversation_id", Integer, ForeignKey("conversations.id"), nullable=False, index=True),
    Column("sender_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", String(19), nullable=False),  # UTC "YYYY-MM-DD HH:MM:SS"
)

# --- login sessions (server side): the browser only holds a random token, we hold the expiry ---
sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),   # sha256 of the token: a leaked table is useless
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("created_at", String(19), nullable=False),
    Column("expires_at", String(19), nullable=False, index=True),  # UTC "YYYY-MM-DD HH:MM:SS"
)

# --- feed: posts, comments, likes -------------------------------------------------------
posts = Table(
    "posts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("body", Text, nullable=False),            # "" is allowed for photo-only posts
    Column("image_filename", String(64), index=True),   # random name inside the uploads folder
    Column("created_at", String(19), nullable=False),
    Column("event_at", String(16), index=True),      # "YYYY-MM-DD HH:MM" when the post announces a gig
    Column("event_place", String(120)),              # where the gig is
    Column("latitude", Float, index=True),           # where the gig is on the map (for "gigs near me")
    Column("longitude", Float),
    Column("genre", String(30)),                     # one of taxonomy.GENRES, for gigs announced by members
    Column("mod_state", String(8), nullable=False, server_default="ok"),   # ok | held (waiting for a check) | hidden (reported)
)

comments = Table(
    "comments", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("post_id", Integer, ForeignKey("posts.id"), nullable=False, index=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", String(19), nullable=False),
    Column("mod_state", String(8), nullable=False, server_default="ok"),   # ok | held | hidden
)

# composite primary key = one like per user per post, enforced by the database
likes = Table(
    "likes", metadata,
    Column("post_id", Integer, ForeignKey("posts.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("created_at", String(19), nullable=False),
)

# --- chat: how far each user has read in each conversation (drives the unread badge) -----
conversation_reads = Table(
    "conversation_reads", metadata,
    Column("conversation_id", Integer, ForeignKey("conversations.id"), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("last_read_id", Integer, nullable=False),  # id of the last message seen
)

# --- e-mail links: confirm an address / reset a password. Only a hash of the token is stored ---
email_tokens = Table(
    "email_tokens", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("purpose", String(10), nullable=False),        # verify | reset
    Column("new_email", String(254)),                     # set when the link confirms a change of address
    Column("created_at", String(19), nullable=False),
    Column("expires_at", String(19), nullable=False),
)

# --- rate limiting: one row per counted event (the key is a hash, see ratelimit.py) ------
rate_hits = Table(
    "rate_hits", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("bucket", String(64), nullable=False, index=True),  # hash of "<limit name>:<who>" (not called key: reserved in MySQL)
    Column("created_at", String(19), nullable=False),
)

# --- moderation ---------------------------------------------------------------------------
# a member reports a post, comment or profile; admins work through the queue
reports = Table(
    "reports", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("reporter_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("target_type", String(10), nullable=False),    # post | comment | user
    Column("target_id", Integer, nullable=False),
    Column("target_user_id", Integer, index=True),        # who wrote it (no FK: survives account deletion)
    Column("reason", String(20), nullable=False),         # spam | abuse | illegal | other
    Column("details", String(500), nullable=False, server_default=""),
    Column("snapshot", Text, nullable=False, server_default=""),  # copy of the reported text
    Column("status", String(10), nullable=False, server_default="open", index=True),  # open | closed
    Column("resolution", String(20)),                     # dismissed | removed | removed_banned
    Column("resolved_by", Integer),
    Column("resolved_at", String(19)),
    Column("created_at", String(19), nullable=False),
)

# who blocked whom: no messages between them and no posts or comments from each other
blocks = Table(
    "blocks", metadata,
    Column("blocker_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("blocked_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("created_at", String(19), nullable=False),
)

# audit trail of every admin action (no FK on the actor: the log outlives accounts)
mod_log = Table(
    "mod_log", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("actor_id", Integer),
    Column("actor_name", String(80), nullable=False),
    Column("action", String(30), nullable=False),
    Column("target", String(60), nullable=False, server_default=""),
    Column("detail", String(255), nullable=False, server_default=""),
    Column("created_at", String(19), nullable=False),
)

# --- gigs imported from outside (Ticketmaster, ...). Kept apart from member posts: they are not in the
# feed, have no comments, and are deleted again when stale (the providers only allow short-term storage) ---
external_events = Table(
    "external_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(20), nullable=False),            # ticketmaster
    Column("external_id", String(64), nullable=False),       # the provider's own id
    Column("title", String(255), nullable=False),
    Column("venue", String(160), nullable=False, server_default=""),
    Column("city", String(120), nullable=False, server_default=""),
    Column("event_at", String(16), nullable=False, index=True),    # "YYYY-MM-DD HH:MM", local time at the venue
    Column("time_known", Integer, nullable=False, server_default="1"),  # 0 = only the date is known
    Column("latitude", Float, nullable=False, index=True),
    Column("longitude", Float, nullable=False),
    Column("ticket_url", String(600)),                       # where to buy tickets (the provider's page)
    Column("genre", String(60)),
    Column("area", String(60), nullable=False, server_default=""),  # which area / search cell / artist fetched it
    Column("user_id", Integer, index=True),                  # the band, for events imported from its own artist page
    Column("imported_at", String(19), nullable=False),
    Column("seen_at", String(19), nullable=False),           # last time the provider still listed it
    UniqueConstraint("source", "external_id", name="uq_external_event"),
)

# --- imported events an admin hid because the provider's data is wrong (e.g. a venue pinned in the wrong city).
# The importer skips them, so they do not come back at the next refresh ---
hidden_events = Table(
    "hidden_events", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source", String(20), nullable=False),
    Column("external_id", String(64), nullable=False),
    Column("title", String(255), nullable=False, server_default=""),   # to recognise it in the list
    Column("reason", String(200), nullable=False, server_default=""),
    Column("hidden_by", String(80), nullable=False, server_default=""),
    Column("created_at", String(19), nullable=False),
    UniqueConstraint("source", "external_id", name="uq_hidden_event"),
)

# --- social: who is going to which gig, and what people play / like / look for ---
# a snapshot of the gig is kept with the RSVP because imported events are deleted when stale (providers only allow
# short-term storage); the snapshots of imported gigs are removed a day after the gig (see social.prune_attendances)
attendances = Table(
    "attendances", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("source", String(20), nullable=False),             # community | ticketmaster | skiddle | ...
    Column("event_ref", String(64), nullable=False),          # post id (community) or the provider's own id
    Column("status", String(10), nullable=False),             # going | interested
    Column("visible", Integer, nullable=False, server_default="1"),   # 0 = counted, but not listed by name
    Column("title", String(255), nullable=False),
    Column("venue", String(160), nullable=False, server_default=""),
    Column("city", String(120), nullable=False, server_default=""),
    Column("event_at", String(16), nullable=False, index=True),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("genre", String(30)),
    Column("created_at", String(19), nullable=False),
    UniqueConstraint("user_id", "source", "event_ref", name="uq_attendance"),
)

user_instruments = Table(
    "user_instruments", metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("instrument", String(30), primary_key=True, index=True),   # one of taxonomy.INSTRUMENTS
    Column("level", String(12)),                                      # one of taxonomy.LEVELS, or NULL = not said
)

user_genres = Table(
    "user_genres", metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("genre", String(30), primary_key=True, index=True),        # one of taxonomy.GENRES
)

user_goals = Table(
    "user_goals", metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("goal", String(30), primary_key=True, index=True),         # one of taxonomy.GOALS
)

# --- follows: A follows B (no approval needed, like the plans themselves they are visible to members) ---
follows = Table(
    "follows", metadata,
    Column("follower_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("followed_id", Integer, ForeignKey("users.id"), primary_key=True, index=True),
    Column("created_at", String(19), nullable=False),
)

# --- notifications shown in the app (the bell); a push channel for the Android app can read the same rows ---
notifications = Table(
    "notifications", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("kind", String(20), nullable=False),               # follow | friend_going
    Column("text", String(255), nullable=False),
    Column("url", String(255)),
    Column("dedupe_key", String(120), nullable=False),        # the same event never notifies the same person twice
    Column("created_at", String(19), nullable=False),
    Column("read_at", String(19)),
    UniqueConstraint("user_id", "dedupe_key", name="uq_notification"),
)

# --- the discussion under a gig. Like an RSVP it keeps a snapshot of the gig, so the thread can be found from every
# listing of the same concert and survives until the gig is long over ---
gig_comments = Table(
    "gig_comments", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("source", String(20), nullable=False),
    Column("event_ref", String(64), nullable=False),
    Column("title", String(255), nullable=False),
    Column("event_at", String(16), nullable=False, index=True),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("body", Text, nullable=False),
    Column("created_at", String(19), nullable=False),
    Column("mod_state", String(8), nullable=False, server_default="ok"),   # ok | held | hidden
)

# --- which parts of the world were fetched from which provider, and when (on-demand import) ---
import_coverage = Table(
    "import_coverage", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("provider", String(20), nullable=False),
    Column("cell", String(24), nullable=False),               # centre of a ~28 km grid square, "51.50,-0.25"
    Column("radius_km", Integer, nullable=False),             # how far around the centre was fetched
    Column("status", String(10), nullable=False),             # running | ok | partial | error
    Column("fetched_at", String(19), nullable=False),
    UniqueConstraint("provider", "cell", name="uq_import_coverage"),
)

# --- remembered answers of the place-name -> coordinates lookup (see geo.py) -----------------
geocache = Table(
    "geocache", metadata,
    Column("place", String(120), primary_key=True),     # lower-case, single spaces
    Column("latitude", Float),                          # NULL = the lookup found nothing
    Column("longitude", Float),
    Column("created_at", String(19), nullable=False),
)


def _resolve_url(app):
    """DATABASE_URL from config, then the environment, else the local SQLite file."""
    url = app.config.get("DATABASE_URL") or os.environ.get("DATABASE_URL")
    if not url:
        path = os.path.join(app.instance_path, "rockconnect.sqlite")
        return URL.create("sqlite", database=path)
    if isinstance(url, str) and url.startswith("postgres://"):
        # some hosts hand out the old scheme name; SQLAlchemy only knows "postgresql"
        url = "postgresql://" + url[len("postgres://"):]
    return make_url(url)


def get_engine():
    return current_app.extensions["bac_engine"]


def get_db():
    # one connection per request, stored on flask.g and reused until teardown
    if "db" not in g:
        g.db = get_engine().connect()
        bac_log("db", "connection opened")
    return g.db


def close_db(e=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()  # anything not committed is rolled back here
        bac_log("db", "connection closed")


def execute(sql, **params):
    """Run raw SQL with :named parameters on the request's connection."""
    return get_db().execute(text(sql), params)


def insert(table, **values):
    """Insert one row and return its new id (works on SQLite, PostgreSQL and MySQL alike)."""
    return get_db().execute(table.insert().values(**values)).inserted_primary_key[0]


def commit():
    get_db().commit()


def rollback():
    get_db().rollback()


def init_db():
    # idempotent: applies every Alembic migration that has not run yet (see migrate.py)
    from .migrate import upgrade
    upgrade(get_engine())
    bac_log("db", "schema ready (tables: %s)" % ", ".join(metadata.tables))
    from . import review      # imported here: review.py imports this module
    review.seed_default_lists()


@click.command("db-upgrade")
def init_db_command():
    """Bring the database to the latest schema (safe to run repeatedly)."""
    init_db()
    click.echo("Database schema is up to date.")


def init_app(app):
    url = _resolve_url(app)
    # pre_ping: survive dropped remote connections. SQLite: wait up to 30 s for another writer (the background import)
    options = {"connect_args": {"timeout": 30}} if url.get_backend_name() == "sqlite" else {}
    engine = create_engine(url, pool_pre_ping=True, **options)
    if url.get_backend_name() == "sqlite":
        @event.listens_for(engine, "connect")
        def _sqlite_fk(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA foreign_keys=ON")  # SQLite ignores FKs unless asked
    app.extensions["bac_engine"] = engine
    # never log the password: render_as_string(hide_password=True) masks it
    bac_log("db", "using %s database: %s" % (
        "LOCAL" if url.get_backend_name() == "sqlite" else "REMOTE",
        url.render_as_string(hide_password=True)))
    app.teardown_appcontext(close_db)
    app.cli.add_command(init_db_command)
    if app.config.get("AUTO_MIGRATE", True):
        # first run (and every upgrade) works without a manual step. Production setups that start
        # several workers run `flask db-upgrade` once before them and set AUTO_MIGRATE=0.
        with app.app_context():
            init_db()

# content waiting for a moderator: held by the filter / the new-member rules, or hidden after reports
review_items = Table(
    "review_items", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("target_type", String(12), nullable=False),    # post | comment | gigcomment
    Column("target_id", Integer, nullable=False),
    Column("author_id", Integer, ForeignKey("users.id"), index=True),
    Column("reason", String(160), nullable=False),        # why it is waiting (shown to moderators only)
    Column("snapshot", Text, nullable=False, server_default=""),
    Column("status", String(10), nullable=False, server_default="open", index=True),   # open | closed
    Column("resolution", String(12)),                     # approved | removed | removed_banned | gone
    Column("resolved_by", Integer),
    Column("resolved_at", String(19)),
    Column("created_at", String(19), nullable=False),
)

# words and phrases that send a post or comment to the review queue (edited by admins)
blocked_words = Table(
    "blocked_words", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("word", String(80), nullable=False, unique=True),
    Column("created_by", Integer),
    Column("created_at", String(19), nullable=False),
)

# a member asked to follow a PRIVATE member and waits for the answer (accepting moves it to `follows`)
follow_requests = Table(
    "follow_requests", metadata,
    Column("follower_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("followed_id", Integer, ForeignKey("users.id"), primary_key=True, index=True),
    Column("created_at", String(19), nullable=False),
)

# a sign-up that waits for its confirmation link: NOT an account yet (nobody can sign in with it, nobody can see it).
# The link creates the account; unconfirmed ones are deleted after 24 hours.
pending_signups = Table(
    "pending_signups", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("username", String(30), nullable=False),
    Column("name", String(120), nullable=False),
    Column("email", String(254), nullable=False, index=True),
    Column("password", String(100), nullable=False),         # already hashed (bcrypt)
    Column("about", Text, nullable=False, server_default=""),
    Column("kind", String(10), nullable=False),
    Column("birth_date", String(10), nullable=False),
    Column("terms_accepted_at", String(19), nullable=False),
    Column("created_at", String(19), nullable=False),
    Column("expires_at", String(19), nullable=False, index=True),
)
