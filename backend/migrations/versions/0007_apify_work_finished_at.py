"""Repair already-applied Apify work tables missing the completion marker.

Revision ID: 0007_apify_work_finished_at
Revises: 0006_apify_reels
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_apify_work_finished_at"
down_revision = "0006_apify_reels"
branch_labels = None
depends_on = None


def upgrade():
    # Some installations applied the earlier 0006 before this column was added to it.
    # Later/fresh 0006 installations already have it; both histories must upgrade safely.
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("video_tracking_apify_work")}
    if "finished_at" not in columns:
        op.add_column("video_tracking_apify_work", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))


def downgrade():
    # Current 0006 already defines this nullable column. Keep it on downgrade to that revision;
    # downgrading through 0006 removes the entire provider work table.
    pass
