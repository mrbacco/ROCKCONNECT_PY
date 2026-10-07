# File: 0011_pending_signups.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Confirm-first sign-up: an account is only created when the link sent to its email address is opened.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "pending_signups",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("username", sa.String(30), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password", sa.String(100), nullable=False),
        sa.Column("about", sa.Text, nullable=False, server_default=""),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("birth_date", sa.String(10), nullable=False),
        sa.Column("terms_accepted_at", sa.String(19), nullable=False),
        sa.Column("created_at", sa.String(19), nullable=False),
        sa.Column("expires_at", sa.String(19), nullable=False),
    )
    op.create_index("ix_pending_signups_email", "pending_signups", ["email"])
    op.create_index("ix_pending_signups_expires_at", "pending_signups", ["expires_at"])


def downgrade():
    op.drop_table("pending_signups")
