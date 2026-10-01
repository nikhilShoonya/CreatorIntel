"""Logging helpers: key=value structured messages with secret redaction."""

import logging
import re
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"(?i)(key|access_token|appsecret_proof|client_secret|token)=([^&\s\"']+)"),
    re.compile(r"(?i)(authorization:\s*(?:bearer|oauth)\s+)([^\s\"']+)"),
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),  # OpenAI-style keys
    re.compile(r"gsk_[A-Za-z0-9]{10,}"),  # Groq keys
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),  # Google API keys
    re.compile(r"\b(?:EAA|IGAA)[0-9A-Za-z]{30,}"),  # Meta / Instagram access tokens
    re.compile(r"(postgres(?:ql)?(?:\+\w+)?://[^:/\s]+:)[^@\s]+(@)"),  # database passwords
]


def redact(text: Any) -> str:
    """Remove anything that looks like a credential from a string."""
    value = str(text)
    for pattern in _SECRET_PATTERNS:
        if pattern.pattern.startswith("(postgres"):
            value = pattern.sub(lambda m: f"{m.group(1)}[REDACTED]{m.group(2)}", value)
        elif pattern.groups >= 2:
            value = pattern.sub(lambda m: f"{m.group(1)}{'=' if not m.group(1).lower().startswith('authorization') else ''}[REDACTED]", value)
        else:
            value = pattern.sub("[REDACTED]", value)
    return value


class RedactingFilter(logging.Filter):
    """Last line of defence: strip credentials from every record, whoever logged it."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        cleaned = redact(message)
        if cleaned != message:
            record.msg, record.args = cleaned, None
        return True


_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"


def configure_logging(level: str = "INFO", log_dir: Path | None = None, retention_days: int = 14) -> None:
    """Console logging plus (optionally) a daily rotating file: creatorintel.log, creatorintel.log.YYYY-MM-DD, ..."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            file_handler = TimedRotatingFileHandler(
                Path(log_dir) / "creatorintel.log", when="midnight", backupCount=retention_days, encoding="utf-8", delay=True
            )
            handlers.append(file_handler)
        except OSError as exc:  # never fail startup because of the log folder
            logging.getLogger(__name__).warning("File logging disabled: %s", exc)
    for handler in handlers:
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(RedactingFilter())
    logging.basicConfig(level=level.upper(), handlers=handlers, force=True)
    # uvicorn's own loggers don't propagate to root: give them the same file + redaction.
    for name in ("uvicorn.error", "uvicorn.access"):
        uvicorn_logger = logging.getLogger(name)
        for handler in handlers[1:]:
            if handler not in uvicorn_logger.handlers:
                uvicorn_logger.addHandler(handler)
        for handler in uvicorn_logger.handlers:
            handler.addFilter(RedactingFilter())
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
