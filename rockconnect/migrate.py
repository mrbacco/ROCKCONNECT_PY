# File: migrate.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Run the Alembic migrations from code (used on start-up and by `flask db-upgrade`).

Developers create new migrations with the alembic.ini in the project root:
    alembic revision --autogenerate -m "describe the change"
"""
import os

from alembic import command
from alembic.config import Config

from .baclog import bac_log

MIGRATIONS_DIR = os.path.join(os.path.dirname(__file__), "migrations")


def alembic_config():
    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    return cfg


def upgrade(engine, revision="head"):
    """Apply every migration up to `revision`. Databases created by older releases (before
    Alembic existed) are recognised by migration 0001, which only creates what is missing."""
    cfg = alembic_config()
    with engine.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)
    bac_log("migrate", "database is at revision %s" % revision)
