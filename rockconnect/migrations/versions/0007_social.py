# File: 0007_social.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Going to gigs, instruments / genres / what members look for, and message requests.

Existing conversations stay open ('accepted'): only chats started from now on begin as requests.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("hide_plans", sa.Integer, nullable=False, server_default="0"))
    op.add_column("posts", sa.Column("genre", sa.String(30)))
    op.add_column("conversations", sa.Column("status", sa.String(10), nullable=False, server_default="accepted"))
    op.add_column("conversations", sa.Column("initiator_id", sa.Integer))

    op.create_table(
        "attendances",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("event_ref", sa.String(64), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("visible", sa.Integer, nullable=False, server_default="1"),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("venue", sa.String(160), nullable=False, server_default=""),
        sa.Column("city", sa.String(120), nullable=False, server_default=""),
        sa.Column("event_at", sa.String(16), nullable=False),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("genre", sa.String(30)),
        sa.Column("created_at", sa.String(19), nullable=False),
        sa.UniqueConstraint("user_id", "source", "event_ref", name="uq_attendance"),
    )
    op.create_index("ix_attendances_user_id", "attendances", ["user_id"])
    op.create_index("ix_attendances_event_at", "attendances", ["event_at"])

    for table, column in (("user_instruments", "instrument"), ("user_genres", "genre"), ("user_goals", "goal")):
        op.create_table(
            table,
            sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
            sa.Column(column, sa.String(30), primary_key=True),
        )
        op.create_index("ix_%s_%s" % (table, column), table, [column])


def downgrade():
    for table in ("user_goals", "user_genres", "user_instruments", "attendances"):
        op.drop_table(table)
    with op.batch_alter_table("conversations") as batch:
        batch.drop_column("initiator_id")
        batch.drop_column("status")
    with op.batch_alter_table("posts") as batch:
        batch.drop_column("genre")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("hide_plans")
