"""upload rows, file retention, format split

Revision ID: 0002_upload_rows
Revises: 0001_baseline
Create Date: 2026-10-01 09:52:45.481026
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = '0002_upload_rows'
down_revision: str | None = '0001_baseline'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    # Guards keep this safe for databases that existed before migrations were introduced.
    if not _has_table("upload_rows"):
        _create_upload_rows()
    if not _has_table("video_tracking_upload_rows"):
        _create_video_tracking_upload_rows()
    for table, column, type_ in (
        ("creators", "average_views_long", sa.Float()),
        ("creators", "average_views_long_count", sa.Integer()),
        ("creators", "average_views_short", sa.Float()),
        ("creators", "average_views_short_count", sa.Integer()),
        ("uploads", "file_deleted_at", sa.DateTime(timezone=True)),
        ("video_tracking_uploads", "file_deleted_at", sa.DateTime(timezone=True)),
    ):
        if not _has_column(table, column):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.add_column(sa.Column(column, type_, nullable=True))


def _create_upload_rows() -> None:
    op.create_table('upload_rows',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('upload_id', sa.String(length=32), nullable=False),
    sa.Column('row_number', sa.Integer(), nullable=False),
    sa.Column('channel_name', sa.String(length=300), nullable=True),
    sa.Column('channel_link', sa.String(length=2048), nullable=True),
    sa.Column('outcome', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['upload_id'], ['uploads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('upload_rows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_upload_rows_upload_id'), ['upload_id'], unique=False)


def _create_video_tracking_upload_rows() -> None:
    op.create_table('video_tracking_upload_rows',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('upload_id', sa.String(length=32), nullable=False),
    sa.Column('row_number', sa.Integer(), nullable=False),
    sa.Column('creator_name', sa.String(length=300), nullable=True),
    sa.Column('platform', sa.String(length=20), nullable=True),
    sa.Column('video_link', sa.String(length=2048), nullable=True),
    sa.Column('username', sa.String(length=100), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('message', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['upload_id'], ['video_tracking_uploads.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('video_tracking_upload_rows', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_video_tracking_upload_rows_upload_id'), ['upload_id'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('video_tracking_uploads', schema=None) as batch_op:
        batch_op.drop_column('file_deleted_at')

    with op.batch_alter_table('uploads', schema=None) as batch_op:
        batch_op.drop_column('file_deleted_at')

    with op.batch_alter_table('creators', schema=None) as batch_op:
        batch_op.drop_column('average_views_short_count')
        batch_op.drop_column('average_views_short')
        batch_op.drop_column('average_views_long_count')
        batch_op.drop_column('average_views_long')

    with op.batch_alter_table('video_tracking_upload_rows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_video_tracking_upload_rows_upload_id'))

    op.drop_table('video_tracking_upload_rows')
    with op.batch_alter_table('upload_rows', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_upload_rows_upload_id'))

    op.drop_table('upload_rows')
