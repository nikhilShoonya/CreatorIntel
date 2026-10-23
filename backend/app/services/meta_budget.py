"""Meta (Instagram / Facebook Graph API) call budget - shared by both modules.

Meta limits calls per rolling hour. Before EVERY Graph call `take()` checks:
  * the local count of calls made in the last rolling hour (META_HOURLY_CALL_BUDGET);
  * Meta's own usage report (X-App-Usage / X-Business-Use-Case-Usage headers): at or above
    META_USAGE_STOP_PERCENT the app stops calling Meta until the reading has had time to drop;
  * a recent "rate limit reached" answer from Meta (then nothing is called for an hour, or for the
    time Meta says until access is regained).
When any of these says no, `take()` raises MetaBudgetExhausted (with the time calls can resume) and no
request is sent - work is queued instead of failed. The call log and pause survive restarts: they are kept
in the api_rate_status table (service "meta_budget"), written by the usage flusher.
"""

import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone

from app.config.settings import get_settings
from app.utils.logging import log_event

logger = logging.getLogger("creatorintel.meta_budget")

WINDOW_SECONDS = 3600
STATE_SERVICE = "meta_budget"

_now = time.time  # time hook (tests)
_calls: deque[float] = deque()  # timestamps of Meta calls in the last rolling hour
_paused_until = 0.0  # Meta answered "rate limit reached"
_reported: tuple[float, float, int | None] | None = None  # (percent, observed_at, regain_minutes) from Meta's headers
_loaded = False
_dirty = False
_lock = threading.Lock()


class MetaBudgetExhausted(Exception):
    def __init__(self, reason: str, resume_at: float):
        super().__init__(reason)
        self.reason = reason
        self.resume_at = resume_at


def _limits() -> tuple[int, float]:
    settings = get_settings()
    return settings.meta_hourly_call_budget, settings.meta_usage_stop_percent


def _prune(now: float) -> None:
    while _calls and _calls[0] <= now - WINDOW_SECONDS:
        _calls.popleft()


def _blocked(now: float) -> MetaBudgetExhausted | None:
    """Why no call may be made right now (None = a call is allowed). Caller holds the lock."""
    budget, stop_percent = _limits()
    if _paused_until > now:
        return MetaBudgetExhausted("Meta's rate limit was reached", _paused_until)
    if _reported is not None:
        percent, observed, regain = _reported
        resume = observed + (regain * 60 if regain else WINDOW_SECONDS)
        if percent >= stop_percent and resume > now:
            return MetaBudgetExhausted(f"Meta reports {percent:.0f}% of its hourly limit used", resume)
    _prune(now)
    if len(_calls) >= budget:
        return MetaBudgetExhausted(f"{budget} Meta calls made in the last hour", _calls[0] + WINDOW_SECONDS)
    return None


def take() -> None:
    """Reserve one Meta call (call right before sending it). Raises MetaBudgetExhausted instead of calling."""
    global _dirty
    _ensure_loaded()
    now = _now()
    with _lock:
        blocked = _blocked(now)
        if blocked is not None:
            raise blocked
        _calls.append(now)
        _dirty = True


def available() -> int:
    """How many Meta calls may be made right now."""
    _ensure_loaded()
    now = _now()
    with _lock:
        if _blocked(now) is not None:
            return 0
        return max(_limits()[0] - len(_calls), 0)


def resume_at() -> datetime | None:
    """When Meta calls can start again (None = now)."""
    _ensure_loaded()
    now = _now()
    with _lock:
        blocked = _blocked(now)
    return datetime.fromtimestamp(blocked.resume_at, timezone.utc) if blocked else None


def observe(percent: float, regain_minutes: int | None) -> None:
    """Meta's usage report from a response's headers (called by api_usage.record_meta_usage)."""
    global _reported
    with _lock:
        _reported = (float(percent), _now(), regain_minutes)


def note_rate_limited(regain_minutes: int | None = None) -> None:
    """Meta answered "rate limit reached": no call for the rest of its hourly window."""
    global _paused_until, _dirty
    with _lock:
        _paused_until = max(_paused_until, _now() + (regain_minutes * 60 if regain_minutes else WINDOW_SECONDS))
        _dirty = True
    log_event(logger, logging.WARNING, "meta_rate_limited", paused_minutes=regain_minutes or WINDOW_SECONDS // 60)


def status() -> dict:
    _ensure_loaded()
    now = _now()
    with _lock:
        blocked = _blocked(now)
        _prune(now)
        used = len(_calls)
        reported = _reported[0] if _reported and now - _reported[1] < WINDOW_SECONDS else None
    budget, stop_percent = _limits()
    return {
        "calls_last_hour": used,
        "hourly_budget": budget,
        "reported_percent": reported,
        "stop_percent": stop_percent,
        "paused": blocked is not None,
        "reason": blocked.reason if blocked else None,
        "resume_at": datetime.fromtimestamp(blocked.resume_at, timezone.utc) if blocked else None,
    }


# ------------------------------------------------------------------ persistence (survives restarts)
def snapshot() -> dict | None:
    """State to save (None when nothing changed since the last save)."""
    global _dirty
    with _lock:
        if not _dirty:
            return None
        _prune(_now())
        _dirty = False
        return {"calls": list(_calls), "paused_until": _paused_until}


def mark_unsaved() -> None:
    global _dirty
    with _lock:
        _dirty = True


def restore(state: dict | None, reported: tuple[float, float, int | None] | None = None) -> None:
    global _paused_until, _reported, _loaded
    with _lock:
        now = _now()
        if state:
            _calls.clear()
            _calls.extend(sorted(t for t in state.get("calls") or [] if isinstance(t, (int, float)) and t > now - WINDOW_SECONDS))
            _paused_until = max(_paused_until, float(state.get("paused_until") or 0))
        if reported is not None and _reported is None:
            _reported = reported
        _loaded = True


def _ensure_loaded() -> None:
    """Load the saved call log once per process (after a restart the last hour's calls still count)."""
    if _loaded:
        return
    try:
        from app.models.db import ReadSessionLocal
        from app.models.usage import ApiRateStatus

        with ReadSessionLocal() as db:
            saved = db.get(ApiRateStatus, STATE_SERVICE)
            meta = db.get(ApiRateStatus, "instagram")
            state = saved.details if saved else None
            reported = None
            if meta is not None and meta.observed_at is not None:
                observed = meta.observed_at if meta.observed_at.tzinfo else meta.observed_at.replace(tzinfo=timezone.utc)
                reported = (float(meta.percent or 0), observed.timestamp(), meta.regain_access_minutes)
    except Exception:  # database not ready yet: start empty (Meta's own headers still protect)
        state, reported = None, None
    restore(state, reported)


def reset() -> None:
    """Forget everything (tests)."""
    global _paused_until, _reported, _loaded, _dirty
    with _lock:
        _calls.clear()
        _paused_until = 0.0
        _reported = None
        _loaded = True
        _dirty = False
