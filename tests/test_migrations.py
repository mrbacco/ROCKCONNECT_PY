# File: test_migrations.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Alembic migrations: a new database, an old database from before migrations, and no drift from the models."""
import os

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from rockconnect.db import metadata
from rockconnect.migrate import upgrade


def engine_for(tmp_path):
    return create_engine("sqlite:///" + os.path.join(str(tmp_path), "m.sqlite"))


def test_fresh_database_gets_every_table(tmp_path):
    engine = engine_for(tmp_path)
    upgrade(engine)
    tables = set(inspect(engine).get_table_names())
    assert set(metadata.tables) <= tables and "alembic_version" in tables


def test_migrated_schema_matches_the_models(tmp_path):
    """If someone edits db.py without writing a migration, this fails and says what is missing."""
    engine = engine_for(tmp_path)
    upgrade(engine)
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), metadata)
    assert diff == [], "db.py and the migrations disagree: %r" % (diff,)


def test_running_twice_is_harmless(tmp_path):
    engine = engine_for(tmp_path)
    upgrade(engine)
    upgrade(engine)


def test_old_database_without_migration_history_keeps_its_members(tmp_path):
    """A site created before Alembic existed: tables are there, no alembic_version table."""
    engine = engine_for(tmp_path)
    upgrade(engine, "0001")                       # the old schema
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO users (username, name, email, password, about)"
                          " VALUES ('old', 'Old Timer', 'old@example.com', 'hash', 'hello')"))
        conn.execute(text("INSERT INTO posts (user_id, body, created_at) VALUES (1, 'first!', '2019-01-01 00:00:00')"))
        conn.execute(text("DROP TABLE alembic_version"))   # as if migrations never ran here
    upgrade(engine)
    with engine.connect() as conn:
        user = conn.execute(text("SELECT kind, role, status, email_verified, created_at FROM users")).fetchone()
        post = conn.execute(text("SELECT body, event_at FROM posts")).fetchone()
    assert user[:4] == ("fan", "member", "active", 1)    # existing members are not locked out
    assert user[4]                                        # got a "member since" date
    assert tuple(post) == ("first!", None)


def test_downgrade_then_upgrade(tmp_path):
    from alembic import command
    from rockconnect.migrate import alembic_config
    engine = engine_for(tmp_path)
    upgrade(engine)
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "0001")
    assert "reports" not in inspect(engine).get_table_names()
    upgrade(engine)
    assert "reports" in inspect(engine).get_table_names()
