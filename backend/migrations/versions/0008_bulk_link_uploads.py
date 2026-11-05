"""Upload batches of pasted links: upload type, and a real link from each upload row to its video.

Both changes only add to the schema. Existing uploads become type "file"; their rows keep video_id empty and
stay matched to their videos through the stored link, as before.

Revision ID: 0008_bulk_link_uploads
Revises: 0007_apify_work_finished_at
"""

import sqlalchemy as sa
from alembic import op

revision = "0008_bulk_link_uploads"
down_revision = "0007_apify_work_finished_at"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("video_tracking_uploads", sa.Column("upload_type", sa.String(20), nullable=False, server_default="file"))
    with op.batch_alter_table("video_tracking_upload_rows", schema=None) as batch_op:
        batch_op.add_column(sa.Column("video_id", sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f("ix_video_tracking_upload_rows_video_id"), ["video_id"], unique=False)
        batch_op.create_foreign_key(
            "fk_video_tracking_upload_rows_video_id", "video_tracking_videos", ["video_id"], ["id"], ondelete="SET NULL"
        )


def downgrade():
    with op.batch_alter_table("video_tracking_upload_rows", schema=None) as batch_op:
        batch_op.drop_constraint("fk_video_tracking_upload_rows_video_id", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_video_tracking_upload_rows_video_id"))
        batch_op.drop_column("video_id")
    # Not a batch operation: on SQLite a batch would rebuild this parent table, and dropping the old copy
    # would cascade-delete every upload's saved rows.
    op.execute("ALTER TABLE video_tracking_uploads DROP COLUMN upload_type")
