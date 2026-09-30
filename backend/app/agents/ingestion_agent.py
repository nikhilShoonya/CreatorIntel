"""Agent 1 - File Ingestion.

Validates the uploaded spreadsheet, maps column-name variants to
channel_name / channel_link, resolves each link's platform and removes
duplicate creators (platform + normalised identifier).
"""

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from app.agents.platform_agent import PlatformResolver
from app.utils.text import clean_text

ALLOWED_EXTENSIONS = {".xlsx", ".xls", ".csv"}

_NAME_ALIASES = {
    "channelname", "creatorname", "channel", "creator", "name", "influencername",
    "influencer", "accountname", "profilename", "channeltitle",
}
_LINK_ALIASES = {
    "channellink", "creatorlink", "channelurl", "creatorurl", "link", "url", "profilelink",
    "profileurl", "influencerlink", "accountlink", "instagramlink", "youtubelink",
}


class IngestionError(ValueError):
    """The file cannot be processed (shown to the user before enrichment starts)."""


@dataclass
class IngestedRow:
    source_row: int
    channel_name: str
    channel_link: str
    platform: str  # youtube | instagram | unsupported | invalid
    identifier: str | None
    identifier_type: str | None
    normalized_identifier: str
    canonical_url: str
    error: str | None = None

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return (self.platform, self.normalized_identifier)


@dataclass
class IngestionResult:
    rows: list[IngestedRow] = field(default_factory=list)
    total_input_rows: int = 0
    duplicate_rows: int = 0


def sanitize_filename(filename: str | None) -> str:
    name = Path(filename or "upload").name
    stem, ext = Path(name).stem, Path(name).suffix.lower()
    stem = re.sub(r"[^A-Za-z0-9._\- ]+", "_", stem).strip(" ._") or "upload"
    return f"{stem[:100]}{ext}"


def _normalise_header(value: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def _detect_file_kind(content: bytes, extension: str) -> str:
    """Verify the bytes match the declared extension (never trust the name alone)."""
    if extension == ".xlsx":
        if not content.startswith(b"PK\x03\x04"):
            raise IngestionError("The .xlsx file is not a valid Excel workbook")
        return "xlsx"
    if extension == ".xls":
        if content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            return "xls"
        if content.startswith(b"PK\x03\x04"):
            return "xlsx"  # mis-named xlsx
        raise IngestionError("The .xls file is not a valid Excel workbook")
    if b"\x00" in content[:4096]:
        raise IngestionError("The .csv file appears to be binary, not text")
    return "csv"


class FileIngestionAgent:
    def __init__(self, resolver: PlatformResolver | None = None, max_rows: int = 1000):
        self.resolver = resolver or PlatformResolver()
        self.max_rows = max_rows

    def validate_file(self, filename: str | None, content: bytes, max_bytes: int) -> str:
        """Return the sanitised filename or raise IngestionError."""
        safe_name = sanitize_filename(filename)
        extension = Path(safe_name).suffix.lower()
        if extension not in ALLOWED_EXTENSIONS:
            raise IngestionError("Unsupported file type. Upload a .xlsx, .xls or .csv file")
        if not content:
            raise IngestionError("The uploaded file is empty")
        if len(content) > max_bytes:
            raise IngestionError(f"File is larger than the {max_bytes // (1024 * 1024)} MB limit")
        _detect_file_kind(content, extension)
        return safe_name

    def read_table(self, filename: str, content: bytes) -> pd.DataFrame:
        kind = _detect_file_kind(content, Path(filename).suffix.lower())
        try:
            if kind == "csv":
                for encoding in ("utf-8-sig", "cp1252", "latin-1"):
                    try:
                        text = content.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                return pd.read_csv(io.StringIO(text), dtype=str, keep_default_na=False, skip_blank_lines=True)
            engine = "openpyxl" if kind == "xlsx" else "xlrd"
            return pd.read_excel(io.BytesIO(content), dtype=str, keep_default_na=False, engine=engine)
        except IngestionError:
            raise
        except Exception as exc:  # pandas raises many different parser errors
            raise IngestionError(f"Could not read the file: {type(exc).__name__}") from exc

    def map_columns(self, frame: pd.DataFrame) -> tuple[str, str]:
        name_col = link_col = None
        for column in frame.columns:
            key = _normalise_header(column)
            if name_col is None and key in _NAME_ALIASES:
                name_col = column
            elif link_col is None and key in _LINK_ALIASES:
                link_col = column
        missing = [label for label, col in (("Channel Name", name_col), ("Channel Link", link_col)) if col is None]
        if missing:
            found = ", ".join(str(c) for c in frame.columns[:10]) or "none"
            raise IngestionError(
                f"Missing required column(s): {', '.join(missing)}. Found columns: {found}. "
                "Expected headers such as 'Channel Name' and 'Channel Link'."
            )
        return name_col, link_col  # type: ignore[return-value]

    def ingest(self, filename: str, content: bytes) -> IngestionResult:
        frame = self.read_table(filename, content)
        if frame.empty and len(frame.columns) == 0:
            raise IngestionError("The file has no header row")
        name_col, link_col = self.map_columns(frame)

        result = IngestionResult()
        seen: set[tuple[str, str]] = set()
        for index, record in enumerate(frame[[name_col, link_col]].itertuples(index=False), start=2):
            raw_name = clean_text(record[0])[:300]
            raw_link = clean_text(record[1])[:2048]
            if not raw_name and not raw_link:
                continue
            result.total_input_rows += 1
            if result.total_input_rows > self.max_rows:
                raise IngestionError(f"File has more than {self.max_rows} creator rows. Split it into smaller files.")

            parsed = self.resolver.resolve(raw_link)
            if parsed.is_supported:
                row = IngestedRow(
                    source_row=index,
                    channel_name=raw_name or parsed.identifier or "",
                    channel_link=raw_link,
                    platform=parsed.platform,
                    identifier=parsed.identifier,
                    identifier_type=parsed.identifier_type,
                    normalized_identifier=parsed.normalized_identifier or "",
                    canonical_url=parsed.canonical_url or raw_link,
                )
            else:
                row = IngestedRow(
                    source_row=index,
                    channel_name=raw_name or "(no name)",
                    channel_link=raw_link,
                    platform=parsed.platform,
                    identifier=None,
                    identifier_type=None,
                    # invalid rows are keyed by the raw link so they are still de-duplicated
                    normalized_identifier=(raw_link.lower() or f"row-{index}")[:300],
                    canonical_url=raw_link,
                    error=parsed.error or "Channel link is not valid",
                )

            if row.dedupe_key in seen:
                result.duplicate_rows += 1
                continue
            seen.add(row.dedupe_key)
            result.rows.append(row)

        if not result.rows:
            raise IngestionError("The file does not contain any creator rows")
        return result
