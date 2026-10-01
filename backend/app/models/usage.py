"""Shared infrastructure tables: external API usage per day and the latest platform rate-limit reading."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.db import Base
from app.utils.time import utcnow


class ApiUsage(Base):
    __tablename__ = "api_usage"

    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD in the quota's reset timezone
    service: Mapped[str] = mapped_column(String(30), primary_key=True)  # e.g. "youtube"
    key_fingerprint: Mapped[str] = mapped_column(String(16), primary_key=True)  # hash prefix, never the key
    units: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    quota_exceeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class ApiRateStatus(Base):
    """Latest rate-limit usage reported by a platform (Meta sends it in response headers)."""

    __tablename__ = "api_rate_status"

    service: Mapped[str] = mapped_column(String(30), primary_key=True)  # e.g. "instagram"
    percent: Mapped[float] = mapped_column(Float, default=0.0)  # highest of the reported percentages
    details: Mapped[dict | None] = mapped_column(JSON)  # raw percentages (no identifiers)
    regain_access_minutes: Mapped[int | None] = mapped_column(Integer)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

