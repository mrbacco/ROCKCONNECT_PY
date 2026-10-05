# File: 0002_community_features.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Band / venue / fan accounts, gigs, e-mail verification, moderation and rate limiting.

Existing members are kept: they are treated as fans, as verified (they registered before e-mail
checks existed, locking them out would be worse) and get a "member since" date of today.

Revision ID: 0002
Revises: 0001
"""
from datetime import datetime, timezone

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    # ---- users ----
    op.add_column("users", sa.Column("kind", sa.String(10), nullable=False, server_default="fan"))
    op.add_column("users", sa.Column("role", sa.String(10), nullable=False, server_default="member"))
    op.add_column("users", sa.Column("status", sa.String(10), nullable=False, server_default="active"))
    op.add_column("users", sa.Column("ban_reason", sa.String(255)))
    op.add_column("users", sa.Column("email_verified", sa.Integer, nullable=False, server_default="0"))
    op.add_column("users", sa.Column("location", sa.String(120)))
    op.add_column("users", sa.Column("website", sa.String(200)))
    op.add_column("users", sa.Column("terms_accepted_at", sa.String(19)))
    op.add_column("users", sa.Column("created_at", sa.String(19)))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    op.execute(sa.text("UPDATE users SET email_verified = 1, created_at = :now").bindparams(now=now))

    # ---- posts: gigs ----
    op.add_column("posts", sa.Column("event_at", sa.String(16)))
    op.add_column("posts", sa.Column("event_place", sa.String(120)))
    op.create_index("ix_posts_event_at", "posts", ["event_at"])

    # ---- new tables ----
    op.create_table(
        "email_tokens",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("purpose", sa.String(10), nullable=False),
        sa.Column("new_email", sa.String(254)),
        sa.Column("created_at", sa.String(19), nullable=False),
        sa.Column("expires_at", sa.String(19), nullable=False),
    )
    op.create_index("ix_email_tokens_user_id", "email_tokens", ["user_id"])

    op.create_table(
        "rate_hits",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("bucket", sa.String(64), nullable=False),
        sa.Column("created_at", sa.String(19), nullable=False),
    )
    op.create_index("ix_rate_hits_bucket", "rate_hits", ["bucket"])

    op.create_table(
        "reports",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("reporter_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("target_type", sa.String(10), nullable=False),
        sa.Column("target_id", sa.Integer, nullable=False),
        sa.Column("target_user_id", sa.Integer),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column("details", sa.String(500), nullable=False, server_default=""),
        sa.Column("snapshot", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.String(10), nullable=False, server_default="open"),
        sa.Column("resolution", sa.String(20)),
        sa.Column("resolved_by", sa.Integer),
        sa.Column("resolved_at", sa.String(19)),
        sa.Column("created_at", sa.String(19), nullable=False),
    )
    op.create_index("ix_reports_reporter_id", "reports", ["reporter_id"])
    op.create_index("ix_reports_target_user_id", "reports", ["target_user_id"])
    op.create_index("ix_reports_status", "reports", ["status"])

    op.create_table(
        "blocks",
        sa.Column("blocker_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("blocked_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", sa.String(19), nullable=False),
    )

    op.create_table(
        "mod_log",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("actor_id", sa.Integer),
        sa.Column("actor_name", sa.String(80), nullable=False),
        sa.Column("action", sa.String(30), nullable=False),
        sa.Column("target", sa.String(60), nullable=False, server_default=""),
        sa.Column("detail", sa.String(255), nullable=False, server_default=""),
        sa.Column("created_at", sa.String(19), nullable=False),
    )


def downgrade():
    for table in ("mod_log", "blocks", "reports", "rate_hits", "email_tokens"):
        op.drop_table(table)
    op.drop_index("ix_posts_event_at", table_name="posts")
    with op.batch_alter_table("posts") as batch:
        batch.drop_column("event_place")
        batch.drop_column("event_at")
    with op.batch_alter_table("users") as batch:
        for column in ("created_at", "terms_accepted_at", "website", "location", "email_verified",
                       "ban_reason", "status", "role", "kind"):
            batch.drop_column(column)
