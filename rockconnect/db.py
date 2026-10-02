# File: db.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-02
"""Database layer (SQLAlchemy Core): local SQLite by default, remote DB via DATABASE_URL.

  local  (default) : sqlite:///<instance>/rockconnect.sqlite
  remote           : postgresql://user:pass@host:5432/dbname   (pip install psycopg2-binary)
                     mysql+pymysql://user:pass@host:3306/dbname (pip install pymysql)

The same code and SQL run on all of them; tables are created automatically on start-up.
"""
import os

import click
from flask import current_app, g
from sqlalchemy import (CheckConstraint, Column, ForeignKey, Integer, MetaData,
                        String, Table, Text, UniqueConstraint, create_engine,
                        event, text)
from sqlalchemy.engine import URL, make_url

from .baclog import bac_log

metadata = MetaData()

# --- tables (portable types so they work on SQLite, PostgreSQL and MySQL) ---------------
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("username", String(80), nullable=False, unique=True),
    Column("name", String(120), nullable=False),
    Column("email", String(254), nullable=False, unique=True),
    Column("password", String(100), nullable=False),  # bcrypt hash
    Column("about", Text, nullable=False),
)

# one conversation per pair of users; ids are stored low < high so the pair is unique
conversations = Table(
    "conversations", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_low_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("user_high_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("created_at", String(19), nullable=False),  # UTC "YYYY-MM-DD HH:MM:SS"
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

# --- feed: posts, comments, likes -------------------------------------------------------
posts = Table(
    "posts", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False, index=True),
    Column("body", Text, nullable=False),            # "" is allowed for photo-only posts
    Column("image_filename", String(64)),            # random name inside the uploads folder
    Column("created_at", String(19), nullable=False),
)

comments = Table(
    "comments", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("post_id", Integer, ForeignKey("posts.id"), nullable=False, index=True),
    Column("user_id", Integer, ForeignKey("users.id"), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", String(19), nullable=False),
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
    # idempotent: only creates the tables that do not exist yet
    metadata.create_all(get_engine())
    bac_log("db", "schema ready (tables: %s)" % ", ".join(metadata.tables))


@click.command("init-db")
def init_db_command():
    """Create the tables (safe to run repeatedly)."""
    init_db()
    click.echo("Initialised the database.")


def init_app(app):
    url = _resolve_url(app)
    engine = create_engine(url, pool_pre_ping=True)  # pre_ping: survive dropped remote connections
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
    with app.app_context():
        init_db()  # make first run work without a manual step
