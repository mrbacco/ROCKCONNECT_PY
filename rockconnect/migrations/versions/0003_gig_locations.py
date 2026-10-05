# File: 0003_gig_locations.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Coordinates on posts (for "gigs near me") and the cache of place-name lookups.

Existing gigs get no coordinates: they simply do not show up in nearby searches until they are posted again
with a location.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("posts", sa.Column("latitude", sa.Float))
    op.add_column("posts", sa.Column("longitude", sa.Float))
    op.create_index("ix_posts_latitude", "posts", ["latitude"])
    op.create_table(
        "geocache",
        sa.Column("place", sa.String(120), primary_key=True),
        sa.Column("latitude", sa.Float),
        sa.Column("longitude", sa.Float),
        sa.Column("created_at", sa.String(19), nullable=False),
    )


def downgrade():
    op.drop_table("geocache")
    op.drop_index("ix_posts_latitude", table_name="posts")
    with op.batch_alter_table("posts") as batch:
        batch.drop_column("longitude")
        batch.drop_column("latitude")
