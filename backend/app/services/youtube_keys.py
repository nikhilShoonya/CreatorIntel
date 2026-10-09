"""Which configured YouTube API keys can be used right now (shared by both modules' YouTube clients).

* A key whose daily quota is used up is skipped only until Google's daily reset (midnight Pacific Time),
  then it is used again automatically - keys are always tried in their configured order.
* A key that is invalid / blocked is skipped for INVALID_KEY_RETRY_SECONDS, then tried again (it may have
  been fixed in Google Cloud Console).
* Only real key problems count; a 403 about one specific channel or playlist is not a key problem.
Keys are identified by a short hash; the key itself is never stored or logged.
"""

import threading
import time
from datetime import datetime, timezone
from typing import Any

from app.services.api_usage import key_fingerprint, next_reset

INVALID_KEY_RETRY_SECONDS = 60 * 60

# Reasons Google returns when the key itself cannot be used (not about the requested resource).
KEY_REASONS = {"keyInvalid", "keyExpired", "accessNotConfigured", "ipRefererBlocked", "API_KEY_INVALID",
               "API_KEY_SERVICE_BLOCKED", "API_KEY_HTTP_REFERRER_BLOCKED", "API_KEY_IP_ADDRESS_BLOCKED"}
QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
RATE_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}

_skipped_until: dict[str, float] = {}  # key fingerprint -> wall-clock time it may be used again
_lock = threading.Lock()


def error_reasons(payload: Any) -> set[str]:
    reasons: set[str] = set()
    if isinstance(payload, dict):
        error = payload.get("error") or {}
        for item in error.get("errors") or []:
            if isinstance(item, dict) and item.get("reason"):
                reasons.add(str(item["reason"]))
        for detail in error.get("details") or []:
            if isinstance(detail, dict) and detail.get("reason"):
                reasons.add(str(detail["reason"]))
    return reasons


def error_message(payload: Any) -> str:
    return str(((payload or {}).get("error") or {}).get("message", "")) if isinstance(payload, dict) else ""


def is_key_problem(status_code: int | None, payload: Any) -> bool:
    """True only when the key itself is unusable (invalid, expired, API not enabled, blocked)."""
    reasons = error_reasons(payload)
    if reasons & (QUOTA_REASONS | RATE_REASONS):
        return False
    if reasons & KEY_REASONS or status_code == 401:
        return True
    return status_code in (400, 403) and "api key" in error_message(payload).lower()


def usable(keys: list[str]) -> list[str]:
    """Configured keys that are not currently skipped, in configured order."""
    now = time.time()
    with _lock:
        return [k for k in keys if _skipped_until.get(key_fingerprint(k), 0.0) <= now]


def mark_quota_exhausted(key: str) -> None:
    with _lock:
        _skipped_until[key_fingerprint(key)] = next_reset().timestamp()


def mark_invalid(key: str) -> None:
    with _lock:
        _skipped_until[key_fingerprint(key)] = time.time() + INVALID_KEY_RETRY_SECONDS


def next_available(keys: list[str]) -> datetime | None:
    with _lock:
        times = [_skipped_until.get(key_fingerprint(k), 0.0) for k in keys]
    return datetime.fromtimestamp(min(times), timezone.utc) if times and min(times) > time.time() else None


def reset() -> None:
    """Forget skipped keys (tests)."""
    with _lock:
        _skipped_until.clear()
