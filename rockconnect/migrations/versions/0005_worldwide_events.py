# File: 0005_worldwide_events.py
# Author: mrbacco04@gmail.com
# Date: 2026-10-05
"""Worldwide event import: remember which parts of the world were fetched, and let a band connect its own
Bandsintown artist page.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "import_coverage",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("cell", sa.String(24), nullable=False),
        sa.Column("radius_km", sa.Integer, nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("fetched_at", sa.String(19), nullable=False),
        sa.UniqueConstraint("provider", "cell", name="uq_import_coverage"),
    )
    op.add_column("external_events", sa.Column("user_id", sa.Integer))
    op.create_index("ix_external_events_user_id", "external_events", ["user_id"])
    op.add_column("users", sa.Column("bandsintown_artist", sa.String(120)))
    op.add_column("users", sa.Column("bandsintown_app_id", sa.String(64)))


def downgrade():
    with op.batch_alter_table("users") as batch:
        batch.drop_column("bandsintown_app_id")
        batch.drop_column("bandsintown_artist")
    op.drop_index("ix_external_events_user_id", table_name="external_events")
    with op.batch_alter_table("external_events") as batch:
        batch.drop_column("user_id")
    op.drop_table("import_coverage")
