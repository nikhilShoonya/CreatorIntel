"""Excel / CSV export of enriched creator data."""

import io
from collections.abc import Iterable

import pandas as pd
from openpyxl.utils import get_column_letter

from app.models.entities import Creator
from app.utils.spreadsheet import keep_whole_numbers
from app.utils.text import sanitize_spreadsheet_cell

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
    return [sanitize_spreadsheet_cell(v) for v in values]


def _frame(creators: Iterable[Creator]) -> pd.DataFrame:
    return keep_whole_numbers(pd.DataFrame([_row(c) for c in creators], columns=EXPORT_COLUMNS))


def to_csv_bytes(creators: Iterable[Creator]) -> bytes:
    # utf-8-sig so Excel opens non-ASCII names (Hindi etc.) correctly
    return _frame(creators).to_csv(index=False).encode("utf-8-sig")


def to_excel_bytes(creators: Iterable[Creator]) -> bytes:
    frame = _frame(creators)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="Creators")
        sheet = writer.sheets["Creators"]
        sheet.freeze_panes = "B2"
        for index, column in enumerate(frame.columns, start=1):
            longest = max([len(str(column)), *(len(str(v)) for v in frame[column].head(200) if v is not None)])
            sheet.column_dimensions[get_column_letter(index)].width = min(max(12, longest + 2), 60)
    return buffer.getvalue()
