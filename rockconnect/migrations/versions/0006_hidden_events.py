# File: 0006_hidden_events.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Imported events an admin hid (wrong data at the provider), so the importer does not bring them back.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "hidden_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("reason", sa.String(200), nullable=False, server_default=""),
        sa.Column("hidden_by", sa.String(80), nullable=False, server_default=""),
        sa.Column("created_at", sa.String(19), nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_hidden_event"),
    )


def downgrade():
    op.drop_table("hidden_events")
