# File: 0010_private_accounts.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-06
"""Private accounts: a member can be found, but their posts and plans are only for members they accepted.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("is_private", sa.Integer, nullable=False, server_default="0"))
    op.create_table(
        "follow_requests",
        sa.Column("follower_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("followed_id", sa.Integer, sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("created_at", sa.String(19), nullable=False),
    )
    op.create_index("ix_follow_requests_followed_id", "follow_requests", ["followed_id"])
    op.create_index("ix_posts_image_filename", "posts", ["image_filename"])      # a photo is checked against its owner's privacy


def downgrade():
    op.drop_index("ix_posts_image_filename", table_name="posts")
    op.drop_table("follow_requests")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("is_private")
