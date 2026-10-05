# File: 0004_external_events.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Gigs imported from outside services (Ticketmaster), kept short-term and apart from member posts.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "external_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("venue", sa.String(160), nullable=False, server_default=""),
        sa.Column("city", sa.String(120), nullable=False, server_default=""),
        sa.Column("event_at", sa.String(16), nullable=False),
        sa.Column("time_known", sa.Integer, nullable=False, server_default="1"),
        sa.Column("latitude", sa.Float, nullable=False),
        sa.Column("longitude", sa.Float, nullable=False),
        sa.Column("ticket_url", sa.String(600)),
        sa.Column("genre", sa.String(60)),
        sa.Column("area", sa.String(60), nullable=False, server_default=""),
        sa.Column("imported_at", sa.String(19), nullable=False),
        sa.Column("seen_at", sa.String(19), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_external_event"),
    )
    op.create_index("ix_external_events_event_at", "external_events", ["event_at"])
    op.create_index("ix_external_events_latitude", "external_events", ["latitude"])


def downgrade():
    op.drop_table("external_events")
