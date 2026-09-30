"""ORM entities: Creator, Upload and the UploadItem link between them."""

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.db import Base
from app.utils.time import utcnow


class CreatorStatus:
    PENDING = "Pending"
    PROCESSING = "Processing"
    COMPLETED = "Completed"
    PARTIAL = "Partial"
    FAILED = "Failed"

    FINAL = (COMPLETED, PARTIAL, FAILED)
    ACTIVE = (PENDING, PROCESSING)


class UploadStatus:
    VALIDATING = "validating"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Creator(Base):
    __tablename__ = "creators"
    __table_args__ = (UniqueConstraint("platform", "normalized_identifier", name="uq_creator_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_name: Mapped[str] = mapped_column(String(300))
    platform: Mapped[str] = mapped_column(String(20), index=True)  # youtube | instagram | invalid | unsupported
    channel_url: Mapped[str] = mapped_column(String(2048))
    normalized_identifier: Mapped[str] = mapped_column(String(300))
    identifier_type: Mapped[str | None] = mapped_column(String(20))

    # Identity as returned by the platform API
    platform_id: Mapped[str | None] = mapped_column(String(100))
    platform_display_name: Mapped[str | None] = mapped_column(String(300))
    account_access: Mapped[str | None] = mapped_column(String(40))

    # Audience + performance (API data or calculated from API data)
    followers_count: Mapped[int | None] = mapped_column(BigInteger)
    subscriber_count: Mapped[int | None] = mapped_column(BigInteger)
    average_views: Mapped[float | None] = mapped_column(Float)
    average_views_sample_count: Mapped[int | None] = mapped_column(Integer)
    median_views: Mapped[float | None] = mapped_column(Float)

    top_video_title: Mapped[str | None] = mapped_column(String(500))
    top_video_url: Mapped[str | None] = mapped_column(String(2048))
    top_video_views: Mapped[int | None] = mapped_column(BigInteger)

    engagement_rate: Mapped[float | None] = mapped_column(Float)
    engagement_rate_basis: Mapped[str | None] = mapped_column(String(20))  # views | followers
    engagement_sample_count: Mapped[int | None] = mapped_column(Integer)

    # AI-derived qualitative fields
    genre: Mapped[str | None] = mapped_column(String(80), index=True)
    sub_genre: Mapped[str | None] = mapped_column(String(80))
    genre_needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    language: Mapped[str | None] = mapped_column(String(40), index=True)
    secondary_language: Mapped[str | None] = mapped_column(String(40))
    sentiment: Mapped[str | None] = mapped_column(String(20), index=True)
    sentiment_score: Mapped[float | None] = mapped_column(Float)
    genre_confidence: Mapped[float | None] = mapped_column(Float)
    language_confidence: Mapped[float | None] = mapped_column(Float)
    sentiment_confidence: Mapped[float | None] = mapped_column(Float)
    evidence_topics: Mapped[list | None] = mapped_column(JSON)

    # Processing state
    status: Mapped[str] = mapped_column(String(20), default=CreatorStatus.PENDING, index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    issues: Mapped[list | None] = mapped_column(JSON)  # field-level notes (e.g. "AI analysis unavailable")
    provenance: Mapped[dict | None] = mapped_column(JSON)  # field -> source
    # Bounded text sample used for AI analysis, kept so Re-analyze does not re-query platforms
    content_sample: Mapped[dict | None] = mapped_column(JSON)

    data_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    items: Mapped[list["UploadItem"]] = relationship(back_populates="creator")


class Upload(Base):
    __tablename__ = "uploads"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uuid.uuid4().hex)
    filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str | None] = mapped_column(String(300))
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    successful_rows: Mapped[int] = mapped_column(Integer, default=0)
    partial_rows: Mapped[int] = mapped_column(Integer, default=0)
    failed_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default=UploadStatus.VALIDATING)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    items: Mapped[list["UploadItem"]] = relationship(
        back_populates="upload", cascade="all, delete-orphan", order_by="UploadItem.position"
    )


class UploadItem(Base):
    """One unique creator row inside an upload (duplicates collapsed)."""

    __tablename__ = "upload_items"
    __table_args__ = (UniqueConstraint("upload_id", "creator_id", name="uq_upload_creator"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), index=True)
    creator_id: Mapped[int] = mapped_column(ForeignKey("creators.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    source_row: Mapped[int] = mapped_column(Integer)  # 1-based row in the spreadsheet (after header)
    from_cache: Mapped[bool] = mapped_column(Boolean, default=False)

    upload: Mapped[Upload] = relationship(back_populates="items")
    creator: Mapped[Creator] = relationship(back_populates="items")
