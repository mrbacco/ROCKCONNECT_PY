# File: 0008_follows_notifications_comments_age.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Date of birth (age check), instrument skill levels, follows, notifications and the discussion under a gig.

Existing members have no date of birth yet: they are asked for it once, the next time they sign in.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("birth_date", sa.String(10)))
    op.add_column("users", sa.Column("notify_friends_going", sa.Integer, nullable=False, server_default="1"))
    op.add_column("user_instruments", sa.Column("level", sa.String(12)))

    op.create_table(
        "follows",
        sa.Column("follower_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("followed_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", sa.String(19), nullable=False),
    )
    op.create_index("ix_follows_followed_id", "follows", ["followed_id"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("text", sa.String(255), nullable=False),
        sa.Column("url", sa.String(255)),
        sa.Column("dedupe_key", sa.String(120), nullable=False),
        sa.Column("created_at", sa.String(19), nullable=False),
        sa.Column("read_at", sa.String(19)),
        sa.UniqueConstraint("user_id", "dedupe_key", name="uq_notification"),
    )
    op.create_index("ix_notifications_user_id", "notifications", ["user_id"])

    op.create_table(
        "gig_comments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("event_ref", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("event_at", sa.String(16), nullable=False),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("created_at", sa.String(19), nullable=False),
    )
    op.create_index("ix_gig_comments_user_id", "gig_comments", ["user_id"])
    op.create_index("ix_gig_comments_event_at", "gig_comments", ["event_at"])


def downgrade():
    for table in ("gig_comments", "notifications", "follows"):
        op.drop_table(table)
    with op.batch_alter_table("user_instruments") as batch:
        batch.drop_column("level")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("notify_friends_going")
        batch.drop_column("birth_date")
