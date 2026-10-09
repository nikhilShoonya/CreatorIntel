"""Excel/CSV import for Video Performance (Creator Name, Platform, Video Link [, Username])."""

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.utils.text import clean_text
from app.video_performance.share_links import ShareResolution, resolution_note
from app.video_performance.urls import ParsedVideo, parse_instagram_handle, parse_video_link

ALLOWED = {".xlsx", ".xls", ".csv"}
MAX_ROWS = 2000
HEADER_SEARCH_ROWS = 10

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
    raw_link: str | None = None
    raw_platform: str | None = None


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


def _decode(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("latin-1")


def read_grid(kind: str, content: bytes) -> pd.DataFrame:
    """Every line of the sheet as a row of strings (no header assumed), so row numbers match the file."""
    if kind == "csv":
        text = _decode(content)
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.reader(io.StringIO(text), dialect))
        width = max((len(r) for r in rows), default=0)
        return pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=str)
    engine = "openpyxl" if kind == "xlsx" else "xlrd"
    return pd.read_excel(io.BytesIO(content), header=None, dtype=str, keep_default_na=False, engine=engine)


def header_labels(values: list) -> list[str]:
    """Header cells as column labels; blank/repeated cells get unique placeholders."""
    labels: list[str] = []
    for position, value in enumerate(values):
        label = str(value).strip()
        if not label or label in labels:
            label = f"column_{position + 1}"
        labels.append(label)
    return labels


def _read(kind: str, content: bytes) -> pd.DataFrame:
    try:
        return read_grid(kind, content)
    except Exception as exc:
        raise ImportError_(f"Could not read the file: {type(exc).__name__}") from exc


def _map_columns(headers) -> dict:
    columns = {"link": None, "creator": None, "platform": None, "owner": None}
    for column in headers:
        key = _key(column)
        for slot, aliases in (("link", _LINK), ("creator", _CREATOR), ("platform", _PLATFORM), ("owner", _OWNER)):
            if columns[slot] is None and key in aliases:
                columns[slot] = column
                break
    return columns


def parse_import(
    filename: str, content: bytes, max_bytes: int, resolutions: dict[str, ShareResolution] | None = None
) -> ImportResult:
    """Read and validate the rows. `resolutions` maps Facebook share links in the file to their real video link."""
    grid = _read(_check_bytes(filename, content, max_bytes), content)
    frame, columns, header_row = grid, {"link": None}, 1
    # The header is the first row (within the first rows) with a video-link column; title rows above it are skipped.
    for index in range(min(HEADER_SEARCH_ROWS, len(grid))):
        values = header_labels(grid.iloc[index].tolist())
        candidate = _map_columns(values)
        if candidate["link"] is not None:
            frame = grid.iloc[index + 1 :].copy()
            frame.columns = values
            frame = frame.reset_index(drop=True)
            columns, header_row = candidate, index + 1
            break
    if columns["link"] is None:
        first = next((r for r in grid.head(HEADER_SEARCH_ROWS).itertuples(index=False) if any(str(v).strip() for v in r)), [])
        found = ", ".join(str(v) for v in first if str(v).strip()) or "none"
        raise ImportError_(f"Missing required column 'Video Link'. Found columns: {found}")

    result = ImportResult()
    seen: set[tuple[str, str]] = set()
    for index, record in enumerate(frame.to_dict("records"), start=header_row + 1):
        link = clean_text(record.get(columns["link"], ""))[:2048]
        creator = clean_text(record.get(columns["creator"], "")) if columns["creator"] else ""
        platform_hint = clean_text(record.get(columns["platform"], "")).lower() if columns["platform"] else ""
        owner_cell = record.get(columns["owner"], "") if columns["owner"] else ""
        if not link and not creator:
            continue
        if len(result.rows) >= MAX_ROWS:
            raise ImportError_(f"File has more than {MAX_ROWS} rows. Split it into smaller files.")

        resolution = (resolutions or {}).get(link)
        parsed = parse_video_link(resolution.resolved_url if resolution and resolution.resolved_url else link)
        # Instagram needs the reel owner: explicit username column, the link itself, or a handle-like creator cell.
        owner = (
            (parse_instagram_handle(owner_cell) or parsed.owner_username or parse_instagram_handle(creator))
            if parsed.platform == "instagram"
            else None
        )
        row = ImportRow(index, creator[:300] or None, parsed, owner, raw_link=link, raw_platform=platform_hint or None)
        if not parsed.ok:
            row.status, row.message = "invalid", (resolution.error if resolution and resolution.error else parsed.error)
        else:
            notes = [resolution_note(parsed.url)] if resolution and resolution.resolved_url else []
            aliases = {"youtube": "yt", "instagram": "ig", "facebook": "fb"}
            if platform_hint and platform_hint not in (parsed.platform, aliases.get(parsed.platform)):
                notes.append(f"Platform column says '{platform_hint}', link is {parsed.platform} - the link was used")
            key = (parsed.platform, parsed.identifier)
            if key in seen:
                row.status = "duplicate"
                notes.insert(0, "Duplicate of an earlier row in this file")
            row.message = "; ".join(notes) or None
            seen.add(key)
        result.rows.append(row)

    if not result.rows:
        raise ImportError_("The file does not contain any video rows")
    return result
