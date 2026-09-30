"""Shared filtering / sorting for the creator list and exports."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Select, case, func, or_, select

from app.models.entities import Creator, UploadItem

SortKey = Literal[
    "audience", "average_views", "engagement_rate", "genre", "sentiment", "channel_name", "updated_at", "position"
]


@dataclass
class CreatorFilters:
    q: str | None = None
    platform: str | None = None
    genre: str | None = None
    language: str | None = None
    sentiment: str | None = None
    status: str | None = None
    upload_id: str | None = None
    sort_by: SortKey | None = None
    sort_dir: Literal["asc", "desc"] = "desc"


_SENTIMENT_ORDER = case(
    (Creator.sentiment == "Positive", 0),
    (Creator.sentiment == "Neutral", 1),
    (Creator.sentiment == "Negative", 2),
    (Creator.sentiment == "Unknown", 3),
    else_=None,
)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def build_creator_query(filters: CreatorFilters) -> Select:
    stmt = select(Creator)
    if filters.upload_id:
        stmt = stmt.join(UploadItem, UploadItem.creator_id == Creator.id).where(UploadItem.upload_id == filters.upload_id)

    if filters.q:
        term = f"%{_escape_like(filters.q.strip().lower())}%"
        stmt = stmt.where(
            or_(
                func.lower(Creator.channel_name).like(term, escape="\\"),
                func.lower(Creator.channel_url).like(term, escape="\\"),
                func.lower(Creator.normalized_identifier).like(term, escape="\\"),
                func.lower(Creator.platform_display_name).like(term, escape="\\"),
                func.lower(Creator.genre).like(term, escape="\\"),
                func.lower(Creator.language).like(term, escape="\\"),
                func.lower(Creator.platform).like(term, escape="\\"),
            )
        )
    for column, value in (
        (Creator.platform, filters.platform),
        (Creator.genre, filters.genre),
        (Creator.language, filters.language),
        (Creator.sentiment, filters.sentiment),
        (Creator.status, filters.status),
    ):
        if value:
            stmt = stmt.where(column == value)

    audience = func.coalesce(Creator.subscriber_count, Creator.followers_count)
    sort_columns = {
        "audience": audience,
        "average_views": Creator.average_views,
        "engagement_rate": Creator.engagement_rate,
        "genre": Creator.genre,
        "sentiment": _SENTIMENT_ORDER,
        "channel_name": func.lower(Creator.channel_name),
        "updated_at": Creator.updated_at,
    }

    sort_by = filters.sort_by
    if sort_by == "position" or (sort_by is None and filters.upload_id):
        if filters.upload_id:
            return stmt.order_by(UploadItem.position.asc())
        sort_by = None
    if sort_by is None:
        return stmt.order_by(Creator.id.desc())

    column = sort_columns[sort_by]
    ordered = column.asc() if filters.sort_dir == "asc" else column.desc()
    return stmt.order_by(ordered.nulls_last(), Creator.id.asc())
