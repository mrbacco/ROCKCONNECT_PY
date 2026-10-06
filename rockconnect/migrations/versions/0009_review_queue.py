# File: 0009_review_queue.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Content moderation before publication: held / hidden posts and comments, the review queue and the blocked-words list.

Existing content stays visible (mod_state 'ok').

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    for table in ("posts", "comments", "gig_comments"):
        op.add_column(table, sa.Column("mod_state", sa.String(8), nullable=False, server_default="ok"))

    op.create_table(
        "review_items",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("target_type", sa.String(12), nullable=False),
        sa.Column("target_id", sa.Integer, nullable=False),
        sa.Column("author_id", sa.Integer, sa.ForeignKey("users.id")),
        sa.Column("reason", sa.String(160), nullable=False),
        sa.Column("snapshot", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("resolution", sa.String(12)),
        sa.Column("resolved_by", sa.Integer),
        sa.Column("resolved_at", sa.String(19)),
        sa.Column("created_at", sa.String(19), nullable=False),
    )
    op.create_index("ix_review_items_author_id", "review_items", ["author_id"])
    op.create_index("ix_review_items_status", "review_items", ["status"])

    op.create_table(
        "blocked_words",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("word", sa.String(80), nullable=False, unique=True),
        sa.Column("created_by", sa.Integer),
        sa.Column("created_at", sa.String(19), nullable=False),
    )


def downgrade():
    op.drop_table("blocked_words")
    op.drop_table("review_items")
    for table in ("gig_comments", "comments", "posts"):
        with op.batch_alter_table(table) as batch:
            batch.drop_column("mod_state")
