"""Logging helpers: key=value structured messages with secret redaction."""

import logging
import re
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"(?i)(key|access_token|appsecret_proof|client_secret|token)=([^&\s\"']+)"),
    re.compile(r"(?i)(authorization:\s*(?:bearer|oauth)\s+)([^\s\"']+)"),
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
]


def redact(text: Any) -> str:
    """Remove anything that looks like a credential from a string."""
    value = str(text)
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            value = pattern.sub(lambda m: f"{m.group(1)}{'=' if not m.group(1).lower().startswith('authorization') else ''}[REDACTED]", value)
        else:
            value = pattern.sub("[REDACTED]", value)
    return value


def configure_logging(level: str = "INFO") -> None:
    logging.basicConfig(
        level=level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    )
    # httpx logs full request URLs at INFO; keep it quiet so query strings never reach logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_event(logger: logging.Logger, level: int, event: str, **fields: Any) -> None:
    """Log `event` followed by sorted key=value pairs (values redacted)."""
    parts = [event]
    for key, value in fields.items():
        if value is None:
            continue
        rendered = redact(value)
        if " " in rendered:
            rendered = f'"{rendered}"'
        parts.append(f"{key}={rendered}")
    logger.log(level, " ".join(parts))
