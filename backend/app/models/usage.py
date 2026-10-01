"""Shared infrastructure table: external API usage per day (used by both modules' YouTube clients)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
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
