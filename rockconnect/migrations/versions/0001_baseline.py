# File: 0001_baseline.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Baseline: the schema as it was before migrations existed.

Databases made by older releases already have these tables (they were created at start-up), so each
table is only created when it is missing. That makes this safe on a brand-new database and on an
existing one.

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("username", sa.String(80), nullable=False, unique=True),
            sa.Column("name", sa.String(120), nullable=False),
            sa.Column("email", sa.String(254), nullable=False, unique=True),
            sa.Column("password", sa.String(100), nullable=False),
            sa.Column("about", sa.Text, nullable=False),
        )
    if "conversations" not in existing:
        op.create_table(
            "conversations",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_low_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("user_high_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.String(19), nullable=False),
            sa.UniqueConstraint("user_low_id", "user_high_id", name="uq_conversation_pair"),
            sa.CheckConstraint("user_low_id < user_high_id", name="ck_conversation_order"),
        )
    if "messages" not in existing:
        op.create_table(
            "messages",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), nullable=False),
            sa.Column("sender_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("created_at", sa.String(19), nullable=False),
        )
        op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    if "sessions" not in existing:
        op.create_table(
            "sessions",
            sa.Column("token_hash", sa.String(64), primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.String(19), nullable=False),
            sa.Column("expires_at", sa.String(19), nullable=False),
        )
        op.create_index("ix_sessions_user_id", "sessions", ["user_id"])
        op.create_index("ix_sessions_expires_at", "sessions", ["expires_at"])
    if "posts" not in existing:
        op.create_table(
            "posts",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("image_filename", sa.String(64)),
            sa.Column("created_at", sa.String(19), nullable=False),
        )
        op.create_index("ix_posts_user_id", "posts", ["user_id"])
    if "comments" not in existing:
        op.create_table(
            "comments",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column("post_id", sa.Integer, sa.ForeignKey("posts.id"), nullable=False),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
            sa.Column("body", sa.Text, nullable=False),
            sa.Column("created_at", sa.String(19), nullable=False),
        )
        op.create_index("ix_comments_post_id", "comments", ["post_id"])
    if "likes" not in existing:
        op.create_table(
            "likes",
            sa.Column("post_id", sa.Integer, sa.ForeignKey("posts.id"), primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
            sa.Column("created_at", sa.String(19), nullable=False),
        )
    if "conversation_reads" not in existing:
        op.create_table(
            "conversation_reads",
            sa.Column("conversation_id", sa.Integer, sa.ForeignKey("conversations.id"), primary_key=True),
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
            sa.Column("last_read_id", sa.Integer, nullable=False),
        )


def downgrade():
    for table in ("conversation_reads", "likes", "comments", "posts", "sessions", "messages",
                  "conversations", "users"):
        op.drop_table(table)
