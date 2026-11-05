"""Facebook metrics from Apify: extra metric columns and a run ledger separate from Instagram's.

Only adds nullable columns and one new table. No existing row or snapshot is changed.

Revision ID: 0009_facebook_apify_metrics
Revises: 0008_bulk_link_uploads
"""

import sqlalchemy as sa
from alembic import op

revision = "0009_facebook_apify_metrics"
down_revision = "0008_bulk_link_uploads"
branch_labels = None
depends_on = None

_TABLES = ("video_tracking_videos", "video_tracking_view_history")
_COLUMNS = (
    ("plays", sa.BigInteger), ("reactions", sa.BigInteger), ("shares", sa.BigInteger), ("content_type", lambda: sa.String(20)),
    ("profile_followers", sa.BigInteger), ("profile_page_likes", sa.BigInteger), ("views_precision", lambda: sa.String(30)),
    ("metric_status", sa.JSON),
)


def upgrade():
    for table in _TABLES:
        for name, kind in _COLUMNS:
            op.add_column(table, sa.Column(name, kind(), nullable=True))
    op.create_table(
        "video_tracking_facebook_apify_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("remote_run_id", sa.String(100), unique=True),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("budget_units", sa.Integer(), nullable=False),
        sa.Column("returned_items", sa.Integer()),
        sa.Column("summary", sa.JSON()),
        sa.Column("charged_events", sa.JSON()),
        sa.Column("reported_cost_usd", sa.Float()),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("message", sa.Text()),
    )
    op.create_index("ix_video_tracking_facebook_apify_runs_month", "video_tracking_facebook_apify_runs", ["month"])
    op.create_index("ix_video_tracking_facebook_apify_runs_status", "video_tracking_facebook_apify_runs", ["status"])


def downgrade():
    op.drop_table("video_tracking_facebook_apify_runs")
    for table in _TABLES:
        for name, _kind in _COLUMNS:
            # Not a batch operation: on SQLite a batch rebuilds these parent tables, and dropping the old copy
            # would cascade-delete every snapshot and sentiment row.
            op.execute(f"ALTER TABLE {table} DROP COLUMN {name}")
