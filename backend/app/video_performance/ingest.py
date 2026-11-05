"""Excel/CSV import for Video Performance (Creator Name, Platform, Video Link [, Username]) and pasted lists of links."""

import csv
import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
from openpyxl import load_workbook

from app.utils.text import clean_text
from app.video_performance.share_links import ShareResolution, is_share_link, resolution_note
from app.video_performance.urls import ParsedVideo, parse_instagram_handle, parse_video_link

ALLOWED = {".xlsx", ".xls", ".csv"}
MAX_ROWS = 2000
HEADER_SEARCH_ROWS = 10
MAX_LINK_TEXT_CHARS = 2_000_000

_LINK = {"videolink", "videourl", "link", "url", "reellink", "reelurl", "video", "postlink", "posturl", "videos",
         "instagramreelurl", "instagramreellink", "igreelurl", "igreellink", "instagramreelshortcode"}
_CREATOR = {"creatorname", "creator", "channelname", "channel", "name", "influencer", "influencername", "accountname"}
_PLATFORM = {"platform", "source", "site"}
_OWNER = {
    "username", "handle", "creatorhandle", "instagramusername", "instagramhandle", "iguser", "igusername",
    "creatorlink", "profilelink", "profileurl", "channellink", "accountlink",
    "instagramprofile", "instagramprofileurl", "instagramprofilelink", "creatorusername", "ownerusername",
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
    grid = pd.read_excel(io.BytesIO(content), header=None, dtype=str, keep_default_na=False, engine=engine)
    if kind == "xlsx":
        workbook = load_workbook(io.BytesIO(content), data_only=False)
        try:
            links = {}
            for cells in workbook.worksheets[0].iter_rows():
                for cell in cells:
                    target = cell.hyperlink.target if cell.hyperlink else None
                    # Only a literal HYPERLINK URL is supported; never evaluate workbook formulas.
                    formula = re.fullmatch(r'=HYPERLINK\(\s*"([^"]+)"\s*[,;].*\)', str(cell.value or ""), re.I)
                    if target or formula:
                        links[(cell.row, cell.column - 1)] = target or formula.group(1)
            grid.attrs["hyperlinks"] = links
        finally:
            workbook.close()
    return grid


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
    hyperlinks = grid.attrs.get("hyperlinks", {})
    labels = list(frame.columns)
    owner_columns = [label for label in labels if _key(label) in _OWNER]
    for index, record in enumerate(frame.to_dict("records"), start=header_row + 1):
        def cell_link(column):
            return hyperlinks.get((index, labels.index(column))) if column is not None else None

        cell_value = clean_text(record.get(columns["link"], ""))[:2048]
        link = cell_link(columns["link"]) or cell_value
        creator = clean_text(record.get(columns["creator"], "")) if columns["creator"] else ""
        platform_hint = clean_text(record.get(columns["platform"], "")).lower() if columns["platform"] else ""
        if not link and not creator:
            continue
        if len(result.rows) >= MAX_ROWS:
            raise ImportError_(f"File has more than {MAX_ROWS} rows. Split it into smaller files.")

        resolution = (resolutions or {}).get(link)
        reel_hint = (platform_hint in {"instagram", "ig"} or _key(columns["link"]) in {
            "instagramreelurl", "instagramreellink", "igreelurl", "igreellink", "instagramreelshortcode",
        })
        parsed = parse_video_link(resolution.resolved_url if resolution and resolution.resolved_url else link,
                                  instagram_reel_hint=reel_hint)
        visible = parse_video_link(cell_value, instagram_reel_hint=reel_hint)
        owners = {}
        if parsed.platform == "instagram":
            sources = [("source URL", parsed.owner_username)]
            if visible.ok and (visible.platform, visible.identifier) == (parsed.platform, parsed.identifier):
                sources.append(("visible URL", visible.owner_username))
            for column in owner_columns:
                target = cell_link(column)
                value = clean_text(record.get(column))
                # In a profile-link column, a hyperlink's friendly label is not another username.
                if not target or "username" in _key(column) or "handle" in _key(column) or value.startswith("@") or "/" in value:
                    sources.append((column, value))
                sources.append((f"{column} hyperlink", target))
            # An explicit @handle or Instagram profile hyperlink is trustworthy; a display name is not.
            if creator.startswith("@") or "/" in creator:
                sources.append(("Creator Name", creator))
            sources.append(("Creator Name hyperlink", cell_link(columns["creator"])))
            owners = {source: handle for source, value in sources if (handle := parse_instagram_handle(value))}
        owner = next(iter(owners.values()), None) if len(set(owners.values())) <= 1 else None
        row = ImportRow(index, creator[:300] or None, parsed, owner, raw_link=link, raw_platform=platform_hint or None)
        if not parsed.ok:
            row.status, row.message = "invalid", (resolution.error if resolution and resolution.error else parsed.error)
        elif cell_link(columns["link"]) and visible.ok and (visible.platform, visible.identifier) != (parsed.platform, parsed.identifier):
            row.status, row.message = "invalid", "Conflicting video link cell value and hyperlink target; correct the workbook link"
        elif len(set(owners.values())) > 1:
            row.status = "invalid"
            row.message = "Conflicting Instagram owner metadata: " + ", ".join(f"{source}=@{name}" for source, name in owners.items())
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


# ------------------------------------------------------------------ pasted links
_LINK_SEPARATORS = re.compile(r"[\s,;|]+|(?=https?://)", re.I)
_CELL_SEPARATORS = re.compile(r"[\t,;|]+")
_SCHEME_LESS_URL = re.compile(r"(?:www\.|[a-z0-9-]+(?:\.[a-z0-9-]+)*\.[a-z]{2,}/)", re.I)
_INVISIBLE = str.maketrans("", "", "﻿​‌‍⁠")
_WRAPPING = "\"'<>()[]{}"
_TRAILING = _WRAPPING + ".:!?"
_HEADER_CELLS = _LINK | _CREATOR | _PLATFORM | _OWNER


def _looks_like_url(token: str) -> bool:
    return "://" in token or bool(_SCHEME_LESS_URL.match(token))


def extract_links(text: str) -> list[str]:
    """The links in pasted text, in order: one per line, or any mix of tabs, spaces, commas and table cells.

    Other cells of a line that has a link (titles, names, numbers) are dropped. A line with no link is kept
    whole so it is reported as invalid instead of vanishing, unless it is a table's header.
    """
    entries: list[str] = []
    first_line = True
    for line in text.translate(_INVISIBLE).splitlines():
        tokens = [t.lstrip(_WRAPPING).rstrip(_TRAILING) for t in _LINK_SEPARATORS.split(line)]
        tokens = [t for t in tokens if t]
        if not tokens:
            continue
        links = [t for t in tokens if _looks_like_url(t)]
        if links:
            entries.extend(links)
        else:
            known_header = all(_key(cell) in _HEADER_CELLS for cell in _CELL_SEPARATORS.split(line.strip()) if cell.strip())
            if not known_header and not (first_line and "\t" in line.strip()):
                entries.append(" ".join(line.split()))
        first_line = False
    return entries


def parse_link_batch(text: str, resolutions: dict[str, ShareResolution] | None = None) -> ImportResult:
    """Read and validate pasted links as import rows. `resolutions` maps Facebook share links to their real video link."""
    if len(text) > MAX_LINK_TEXT_CHARS:
        raise ImportError_("The pasted text is too long. Split it into smaller batches.")
    entries = extract_links(text)
    if not entries:
        raise ImportError_("No video links found. Paste at least one YouTube, Instagram or Facebook link.")
    if len(entries) > MAX_ROWS:
        raise ImportError_(f"There are more than {MAX_ROWS} links. Split them into smaller batches.")

    result = ImportResult()
    seen: set[tuple[str, str]] = set()
    for number, entry in enumerate(entries, start=1):
        link = entry[:2048]
        resolution = (resolutions or {}).get(link)
        parsed = parse_video_link(resolution.resolved_url if resolution and resolution.resolved_url else link)
        row = ImportRow(number, None, parsed, parsed.owner_username, raw_link=link)
        if not parsed.ok:
            row.status, row.message = "invalid", (resolution.error if resolution and resolution.error else parsed.error)
        else:
            notes = [resolution_note(parsed.url)] if resolution and resolution.resolved_url else []
            key = (parsed.platform, parsed.identifier)
            if key in seen:
                row.status = "duplicate"
                notes.insert(0, "Duplicate of an earlier link in this batch")
            row.message = "; ".join(notes) or None
            seen.add(key)
        result.rows.append(row)
    return result


@dataclass
class InvalidLink:
    link: str
    message: str


@dataclass
class LinkPreview:
    detected: int = 0
    valid: int = 0
    duplicates: int = 0
    invalid: int = 0
    platforms: dict[str, int] = field(default_factory=dict)  # valid, unique links per detected platform
    invalid_links: list[InvalidLink] = field(default_factory=list)


def preview_link_batch(text: str) -> LinkPreview:
    """What pasted text would upload, read by the same parser and without saving or fetching anything.

    A Facebook share link only reveals its video when it is resolved over the network on upload, so here it
    counts as a valid Facebook link and duplicates of it are found on the link text.
    """
    preview = LinkPreview()
    share_links: set[str] = set()
    for row in parse_link_batch(text).rows:
        preview.detected += 1
        platform = row.parsed.platform
        if is_share_link(row.raw_link):
            platform = "facebook"
            row.status = "duplicate" if row.raw_link in share_links else "pending"
            share_links.add(row.raw_link)
        if row.status == "invalid":
            preview.invalid += 1
            preview.invalid_links.append(InvalidLink(row.raw_link, row.message))
        elif row.status == "duplicate":
            preview.duplicates += 1
        else:
            preview.valid += 1
            preview.platforms[platform] = preview.platforms.get(platform, 0) + 1
    return preview
