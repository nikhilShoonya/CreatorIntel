"""Apify Reel provenance and durable run reservations.

Revision ID: 0006_apify_reels
Revises: d1eb377704e1
"""

import re
from urllib.parse import urlsplit

import sqlalchemy as sa
from alembic import op

revision = "0006_apify_reels"
down_revision = "d1eb377704e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("video_tracking_videos", sa.Column("instagram_media_kind", sa.String(10), nullable=True))
    for table in ("video_tracking_videos", "video_tracking_view_history"):
        op.add_column(table, sa.Column("metrics_source", sa.String(40), nullable=True))
        op.add_column(table, sa.Column("view_metric", sa.String(40), nullable=True))
        op.add_column(table, sa.Column("source_metrics", sa.JSON(), nullable=True))
    with op.batch_alter_table("video_tracking_view_history") as batch:
        batch.add_column(sa.Column("source_run_id", sa.String(32), nullable=True))
        batch.create_unique_constraint("uq_vt_history_source_run", ["video_id", "source_run_id"])
    op.create_table(
        "video_tracking_apify_runs",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("remote_run_id", sa.String(100), nullable=True, unique=True),
        sa.Column("actor_id", sa.String(200), nullable=False),
        sa.Column("month", sa.String(7), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("budget_units", sa.Integer(), nullable=False),
        sa.Column("returned_items", sa.Integer(), nullable=True),
        sa.Column("charged_events", sa.JSON(), nullable=True),
        sa.Column("reported_cost_usd", sa.Float(), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=True),
    )
    op.create_index("ix_video_tracking_apify_runs_month", "video_tracking_apify_runs", ["month"])
    op.create_index("ix_video_tracking_apify_runs_status", "video_tracking_apify_runs", ["status"])
    op.create_table(
        "video_tracking_apify_work",
        sa.Column("video_id", sa.Integer(), sa.ForeignKey("video_tracking_videos.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("run_id", sa.String(32), sa.ForeignKey("video_tracking_apify_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_video_tracking_apify_work_run_id", "video_tracking_apify_work", ["run_id"])

    # Legacy canonical URLs lost /p/ versus /reel/. Only original, exact uploaded URLs prove the kind.
    connection = op.get_bind()
    kinds = {}
    for link, in connection.execute(sa.text("SELECT video_link FROM video_tracking_upload_rows WHERE platform = 'instagram'")):
        try:
            parts = urlsplit(link if "://" in (link or "") else "https://" + (link or ""))
            if (parts.hostname or "").lower() not in {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"}:
                continue
            match = re.fullmatch(r"/(?:[^/]+/)?(reel|reels|p|tv)/([A-Za-z0-9_-]{5,40})/?", parts.path)
            if match:
                kind = "post" if match[1] == "p" else "tv" if match[1] == "tv" else "reel"
                kinds.setdefault(match[2], set()).add(kind)
        except ValueError:
            continue
    for code, values in kinds.items():
        # Any ambiguous old representation stays on Meta until explicitly edited.
        if len(values) == 1:
            connection.execute(sa.text("UPDATE video_tracking_videos SET instagram_media_kind = :kind WHERE platform = 'instagram' AND video_identifier = :code"),
                               {"kind": next(iter(values)), "code": code})


def downgrade():
    op.drop_table("video_tracking_apify_work")
    op.drop_table("video_tracking_apify_runs")
    with op.batch_alter_table("video_tracking_view_history") as batch:
        batch.drop_constraint("uq_vt_history_source_run", type_="unique")
        batch.drop_column("source_run_id")
    for table in ("video_tracking_view_history", "video_tracking_videos"):
        with op.batch_alter_table(table) as batch:
            for name in ("metrics_source", "view_metric", "source_metrics"):
                batch.drop_column(name)
    op.drop_column("video_tracking_videos", "instagram_media_kind")
