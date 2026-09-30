"""Excel/CSV import for Video Performance (Creator Name, Platform, Video Link [, Username])."""

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.utils.text import clean_text
from app.video_performance.urls import ParsedVideo, parse_instagram_handle, parse_video_link

ALLOWED = {".xlsx", ".xls", ".csv"}
MAX_ROWS = 2000

_LINK = {"videolink", "videourl", "link", "url", "reellink", "reelurl", "video", "postlink", "posturl", "videos"}
_CREATOR = {"creatorname", "creator", "channelname", "channel", "name", "influencer", "influencername", "accountname"}
_PLATFORM = {"platform", "source", "site"}
_OWNER = {
    "username", "handle", "creatorhandle", "instagramusername", "instagramhandle", "iguser", "igusername",
    "creatorlink", "profilelink", "profileurl", "channellink", "accountlink",
}


class ImportError_(ValueError):
    """The file cannot be imported (shown to the user)."""


@dataclass
class ImportRow:
    row: int
    creator_name: str | None
    parsed: ParsedVideo
    owner_username: str | None
    status: str = "pending"  # added | already_tracked | duplicate | invalid
    message: str | None = None


@dataclass
class ImportResult:
    rows: list[ImportRow] = field(default_factory=list)


def _key(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _check_bytes(name: str, content: bytes, max_bytes: int) -> str:
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise ImportError_("Unsupported file type. Upload a .xlsx, .xls or .csv file")
    if not content:
        raise ImportError_("The uploaded file is empty")
    if len(content) > max_bytes:
        raise ImportError_(f"File is larger than the {max_bytes // (1024 * 1024)} MB limit")
    if ext == ".xlsx" and not content.startswith(b"PK\x03\x04"):
        raise ImportError_("The .xlsx file is not a valid Excel workbook")
    if ext == ".xls" and not (content.startswith(b"\xd0\xcf\x11\xe0") or content.startswith(b"PK\x03\x04")):
        raise ImportError_("The .xls file is not a valid Excel workbook")
    if ext == ".csv" and b"\x00" in content[:4096]:
        raise ImportError_("The .csv file appears to be binary, not text")
    return "xlsx" if content.startswith(b"PK\x03\x04") else ("xls" if ext == ".xls" else "csv")


def _read(kind: str, content: bytes) -> pd.DataFrame:
    try:
        if kind == "csv":
            for encoding in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    return pd.read_csv(io.StringIO(content.decode(encoding)), dtype=str, keep_default_na=False)
                except UnicodeDecodeError:
                    continue
        return pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False, engine="openpyxl" if kind == "xlsx" else "xlrd")
    except Exception as exc:
        raise ImportError_(f"Could not read the file: {type(exc).__name__}") from exc


def parse_import(filename: str, content: bytes, max_bytes: int) -> ImportResult:
    frame = _read(_check_bytes(filename, content, max_bytes), content)
    columns = {"link": None, "creator": None, "platform": None, "owner": None}
    for column in frame.columns:
        key = _key(column)
        for slot, aliases in (("link", _LINK), ("creator", _CREATOR), ("platform", _PLATFORM), ("owner", _OWNER)):
            if columns[slot] is None and key in aliases:
                columns[slot] = column
                break
    if columns["link"] is None:
        found = ", ".join(str(c) for c in frame.columns[:10]) or "none"
        raise ImportError_(f"Missing required column 'Video Link'. Found columns: {found}")

    result = ImportResult()
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(frame.to_dict("records"), start=2):
        link = clean_text(record.get(columns["link"], ""))[:2048]
        creator = clean_text(record.get(columns["creator"], "")) if columns["creator"] else ""
        platform_hint = clean_text(record.get(columns["platform"], "")).lower() if columns["platform"] else ""
        owner_cell = record.get(columns["owner"], "") if columns["owner"] else ""
        if not link and not creator:
            continue
        if len(result.rows) >= MAX_ROWS:
            raise ImportError_(f"File has more than {MAX_ROWS} rows. Split it into smaller files.")

        parsed = parse_video_link(link)
        # Instagram needs the reel owner: explicit username column, the link itself, or a handle-like creator cell.
        owner = (
            (parse_instagram_handle(owner_cell) or parsed.owner_username or parse_instagram_handle(creator))
            if parsed.platform == "instagram"
            else None
        )
        row = ImportRow(index, creator[:300] or None, parsed, owner)
        if not parsed.ok:
            row.status, row.message = "invalid", parsed.error
        else:
            if platform_hint and platform_hint not in (parsed.platform, "yt" if parsed.platform == "youtube" else "ig"):
                row.message = f"Platform column says '{platform_hint}', link is {parsed.platform} - the link was used"
            key = (parsed.platform, parsed.identifier)
            if key in seen:
                row.status, row.message = "duplicate", "Duplicate of an earlier row in this file"
            seen.add(key)
        result.rows.append(row)

    if not result.rows:
        raise ImportError_("The file does not contain any video rows")
    return result
