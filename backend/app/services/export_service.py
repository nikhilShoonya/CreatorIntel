"""Excel / CSV export of enriched creator data."""

from collections.abc import Iterable

from app.models.entities import Creator
from app.utils.spreadsheet import table_bytes

EXPORT_COLUMNS = [
    "Channel Name", "Platform", "Channel Link", "Subscribers / Followers", "Average Views",
    "Avg Views (Long-form >3 min)", "Avg Views (Short-form <=3 min)",
    "Top Performing Video", "Top Video Views", "Top Video URL", "Engagement Rate (%)",
    "Engagement Basis", "Genre", "Sub-Genre", "Language", "Sentiment", "Status", "Notes",
]

_PLATFORM_LABELS = {"youtube": "YouTube", "instagram": "Instagram", "invalid": "Invalid link", "unsupported": "Unsupported"}


def _row(creator: Creator) -> list:
    audience = creator.subscriber_count if creator.platform == "youtube" else creator.followers_count
    values = [
        creator.channel_name,
        _PLATFORM_LABELS.get(creator.platform, creator.platform),
        creator.channel_url,
        audience,
        round(creator.average_views) if creator.average_views is not None else None,
        round(creator.average_views_long) if creator.average_views_long is not None else None,
        round(creator.average_views_short) if creator.average_views_short is not None else None,
        creator.top_video_title,
        creator.top_video_views,
        creator.top_video_url,
        creator.engagement_rate,
        creator.engagement_rate_basis,
        creator.genre,
        creator.sub_genre,
        creator.language,
        creator.sentiment,
        creator.status,
        creator.error_message,
    ]
    return values  # cells are sanitised by the shared writer


def to_csv_bytes(creators: Iterable[Creator]) -> bytes:
    return table_bytes(EXPORT_COLUMNS, [_row(c) for c in creators], "csv", "Creators")


def to_excel_bytes(creators: Iterable[Creator]) -> bytes:
    return table_bytes(EXPORT_COLUMNS, [_row(c) for c in creators], "excel", "Creators")
