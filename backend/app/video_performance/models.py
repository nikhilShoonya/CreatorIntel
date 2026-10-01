"""ORM models for the Video Performance module (tables prefixed ``video_tracking_``)."""

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.db import Base
from app.utils.time import utcnow


class VideoStatus:
    PENDING = "Pending"
    PROCESSING = "Processing"
    TRACKING = "Tracking"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    PARTIAL = "Partial"
    FAILED = "Failed"
    UNSUPPORTED = "Unsupported"

    ALL = (PENDING, PROCESSING, TRACKING, PAUSED, COMPLETED, PARTIAL, FAILED, UNSUPPORTED)
    ACTIVE_WORK = (PENDING, PROCESSING)
    # Checked automatically by the daily refresh job (failures are retried daily).
    DAILY = (TRACKING, PARTIAL, FAILED)


class CreatorTrackingStatus:
    PENDING = "Pending"
    ACTIVE = "Active"
    PAUSED = "Paused"
    FAILED = "Failed"
    UNSUPPORTED = "Unsupported"


class JobType:
    METRICS_REFRESH = "metrics_refresh"
    CREATOR_DISCOVERY = "creator_discovery"


class VtUpload(Base):
    __tablename__ = "video_tracking_uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str | None] = mapped_column(String(300))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    already_tracked: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    invalid: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # The original file is deleted after the retention period; its rows stay in video_tracking_upload_rows.
    file_deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class VtUploadRow(Base):
    """Every row of an uploaded video list exactly as it was read."""

    __tablename__ = "video_tracking_upload_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("video_tracking_uploads.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int] = mapped_column(Integer)
    creator_name: Mapped[str | None] = mapped_column(String(300))
    platform: Mapped[str | None] = mapped_column(String(20))
    video_link: Mapped[str | None] = mapped_column(String(2048))
    username: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20))  # added | already_tracked | duplicate | invalid
    message: Mapped[str | None] = mapped_column(Text)


class VtCreator(Base):
    """A channel/profile watched for newly published videos."""

    __tablename__ = "video_tracking_creators"
    __table_args__ = (UniqueConstraint("platform", "normalized_identifier", name="uq_vt_creator_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    creator_name: Mapped[str] = mapped_column(String(300))
    platform: Mapped[str] = mapped_column(String(20), index=True)
    channel_url: Mapped[str] = mapped_column(String(2048))
    normalized_identifier: Mapped[str] = mapped_column(String(300))
    identifier_type: Mapped[str | None] = mapped_column(String(20))
    platform_channel_id: Mapped[str | None] = mapped_column(String(100), index=True)  # YouTube channel ID
    username: Mapped[str | None] = mapped_column(String(100), index=True)  # Instagram username
    platform_name: Mapped[str | None] = mapped_column(String(300))
    uploads_playlist_id: Mapped[str | None] = mapped_column(String(100))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default=CreatorTrackingStatus.PENDING)
    status_reason: Mapped[str | None] = mapped_column(Text)
    tracking_since: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_discovery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    videos_discovered: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class VtVideo(Base):
    __tablename__ = "video_tracking_videos"
    __table_args__ = (UniqueConstraint("platform", "video_identifier", name="uq_vt_video_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[str] = mapped_column(String(20), index=True)
    video_identifier: Mapped[str] = mapped_column(String(100))  # YouTube video ID / Instagram shortcode
    video_url: Mapped[str] = mapped_column(String(2048))
    creator_name: Mapped[str | None] = mapped_column(String(300), index=True)
    owner_username: Mapped[str | None] = mapped_column(String(100))  # Instagram owner (needed by the official API)
    channel_id: Mapped[str | None] = mapped_column(String(100))  # YouTube channel ID
    creator_id: Mapped[int | None] = mapped_column(
        ForeignKey("video_tracking_creators.id", ondelete="SET NULL"), index=True
    )
    upload_id: Mapped[str | None] = mapped_column(ForeignKey("video_tracking_uploads.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(20), default="manual")  # upload | manual | discovered

    title: Mapped[str | None] = mapped_column(String(500))
    caption: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    current_views: Mapped[int | None] = mapped_column(BigInteger)
    previous_views: Mapped[int | None] = mapped_column(BigInteger)  # latest snapshot from an earlier day
    views_gained: Mapped[int | None] = mapped_column(BigInteger)
    growth_pct: Mapped[float | None] = mapped_column(Float)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    engagement_rate: Mapped[float | None] = mapped_column(Float)
    engagement_basis: Mapped[str | None] = mapped_column(String(20))  # "views" when calculable

    sentiment: Mapped[str | None] = mapped_column(String(20), index=True)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)

    status: Mapped[str] = mapped_column(String(20), default=VideoStatus.PENDING, index=True)
    status_reason: Mapped[str | None] = mapped_column(Text)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tracking_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    history: Mapped[list["VtViewSnapshot"]] = relationship(back_populates="video", passive_deletes=True)


class VtViewSnapshot(Base):
    __tablename__ = "video_tracking_view_history"
    __table_args__ = (Index("ix_vt_history_video_time", "video_id", "captured_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_tracking_videos.id", ondelete="CASCADE"))
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    views: Mapped[int | None] = mapped_column(BigInteger)
    likes: Mapped[int | None] = mapped_column(BigInteger)
    comments: Mapped[int | None] = mapped_column(BigInteger)
    engagement_rate: Mapped[float | None] = mapped_column(Float)

    video: Mapped[VtVideo] = relationship(back_populates="history")


class VtSentiment(Base):
    __tablename__ = "video_tracking_sentiment"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[int] = mapped_column(ForeignKey("video_tracking_videos.id", ondelete="CASCADE"), index=True)
    sentiment: Mapped[str | None] = mapped_column(String(20))
    confidence: Mapped[float | None] = mapped_column(Float)
    model: Mapped[str | None] = mapped_column(String(100))
    input_chars: Mapped[int] = mapped_column(Integer, default=0)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class VtJobRun(Base):
    __tablename__ = "video_tracking_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_type: Mapped[str] = mapped_column(String(40), index=True)
    trigger: Mapped[str] = mapped_column(String(20))  # scheduled | manual | catch_up
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | completed | failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text)


class VtJobLock(Base):
    """Cross-process lock so a daily job runs once even with several backend processes."""

    __tablename__ = "video_tracking_job_locks"

    job_type: Mapped[str] = mapped_column(String(40), primary_key=True)
    owner: Mapped[str | None] = mapped_column(String(64))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

