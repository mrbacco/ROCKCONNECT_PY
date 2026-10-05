# File: env.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Alembic environment: uses the connection handed over by migrate.py, or (for the alembic
command line) builds one from DATABASE_URL / the local instance/rockconnect.sqlite file."""
import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url

from rockconnect.db import metadata

config = context.config
target_metadata = metadata


def _cli_url():
    url = os.environ.get("DATABASE_URL")
    if url:
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        return make_url(url)
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return URL.create("sqlite", database=os.path.join(root, "instance", "rockconnect.sqlite"))


def _configure(connection):
    # render_as_batch: SQLite cannot ALTER most things, batch mode rebuilds the table instead
    context.configure(connection=connection, target_metadata=target_metadata,
                      render_as_batch=True, compare_type=True)


connection = config.attributes.get("connection")
if connection is not None:
    _configure(connection)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(_cli_url())
    with engine.connect() as conn:
        _configure(conn)
        with context.begin_transaction():
            context.run_migrations()
