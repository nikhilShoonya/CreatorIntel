"""Small text helpers for cleaning captions and bounding AI input size."""

import re
import unicodedata

_HASHTAG = re.compile(r"#(\w+)", re.UNICODE)
_MENTION = re.compile(r"@\w+")
_URL = re.compile(r"https?://\S+")
_WS = re.compile(r"\s+")
_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKC", str(value))
    value = _CONTROL.sub("", value)
    return value.strip()


def truncate(value: str | None, limit: int) -> str:
    text = _WS.sub(" ", clean_text(value))
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 0)].rstrip() + "…"


def extract_hashtags(value: str | None) -> list[str]:
    return [tag.lower() for tag in _HASHTAG.findall(value or "")]


def caption_title(caption: str | None, limit: int = 70) -> str | None:
    """Derive a short display title from an Instagram caption without inventing words."""
    text = clean_text(caption)
    if not text:
        return None
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    first_line = _URL.sub("", first_line)
    first_line = _HASHTAG.sub("", first_line)
    first_line = _MENTION.sub("", first_line)
    first_line = _WS.sub(" ", first_line).strip(" -|•:·")
    if not first_line:
        return None
    return truncate(first_line, limit)


def sanitize_spreadsheet_cell(value: object) -> object:
    """Neutralise formula injection when exporting user-controlled strings to CSV/Excel."""
    if isinstance(value, str) and value != "-" and value and value[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value
