"""Instagram (Meta) access-token health: validity, expiry and missing permissions.

Uses Meta's debug_token endpoint with the app credentials from backend/.env. The result is cached
for a few minutes so pages can show it without calling Meta on every request.
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

from app.config.settings import Settings
from app.services.http_client import HttpRequestError, request_json

REQUIRED_PERMISSIONS = ("instagram_basic", "instagram_manage_insights", "pages_read_engagement", "pages_show_list")
WARN_DAYS = 7
CACHE_SECONDS = 600

TokenState = Literal["ok", "expiring", "expired", "invalid", "missing", "wrong_type", "unknown", "error"]


class InstagramTokenStatus(BaseModel):
    state: TokenState
    message: str
    expires_at: datetime | None = None
    days_left: int | None = None
    never_expires: bool = False
    missing_permissions: list[str] = []
    checked_at: datetime


_cache: tuple[float, InstagramTokenStatus] | None = None
_lock = asyncio.Lock()


def _status(state: TokenState, message: str, **extra) -> InstagramTokenStatus:
    return InstagramTokenStatus(state=state, message=message, checked_at=datetime.now(timezone.utc), **extra)


async def _check(settings: Settings) -> InstagramTokenStatus:
    token = settings.meta_access_token.strip()
    if not token:
        return _status("missing", "No Instagram access token is configured (META_ACCESS_TOKEN)")
    if settings.instagram_token_problem:
        return _status("wrong_type", settings.instagram_token_problem)
    if not settings.meta_app_id or not settings.meta_app_secret:
        return _status("unknown", "Add META_APP_ID and META_APP_SECRET to check when the token expires")
    try:
        payload = await request_json(
            "GET", f"https://graph.facebook.com/{settings.meta_api_version}/debug_token", service="Meta token check",
            params={"input_token": token, "access_token": f"{settings.meta_app_id.strip()}|{settings.meta_app_secret.strip()}"},
            max_retries=1,
        )
    except HttpRequestError as exc:
        error = (exc.payload or {}).get("error", {}) if isinstance(exc.payload, dict) else {}
        meta_message = str(error.get("message") or "").strip()
        if "blocked" in meta_message.lower():
            return _status(
                "invalid",
                f"Meta says: \"{meta_message}\" - API access is blocked for this Meta app. "
                "Check the app's status and alerts in the Meta developer dashboard.",
            )
        if error.get("code") == 190:
            return _status("expired", f"Meta says: \"{meta_message or 'the token is invalid or expired'}\"")
        detail = f'Meta says: "{meta_message}"' if meta_message else exc.message
        return _status("error", f"Could not check the token with Meta ({detail})")

    data = payload.get("data") or {}
    if not data.get("is_valid"):
        reason = (data.get("error") or {}).get("message") or "Meta reports the token is not valid"
        expired = "expired" in reason.lower() or "session has expired" in reason.lower()
        return _status("expired" if expired else "invalid", reason)

    missing = [p for p in REQUIRED_PERMISSIONS if p not in set(data.get("scopes") or [])]
    expires = int(data.get("expires_at") or 0)
    if expires == 0:
        message = "Token is valid and does not expire"
        return _status("ok", message, never_expires=True, missing_permissions=missing)

    expires_at = datetime.fromtimestamp(expires, tz=timezone.utc)
    days_left = int((expires_at - datetime.now(timezone.utc)).total_seconds() // 86400)
    if expires_at <= datetime.now(timezone.utc):
        return _status("expired", "The Instagram access token has expired", expires_at=expires_at, days_left=0)
    state: TokenState = "expiring" if days_left < WARN_DAYS else "ok"
    message = (
        f"Token expires in {days_left} day{'s' if days_left != 1 else ''}" if days_left >= 1
        else "Token expires within 24 hours"
    )
    return _status(state, message, expires_at=expires_at, days_left=days_left, missing_permissions=missing)


async def instagram_token_status(settings: Settings, refresh: bool = False) -> InstagramTokenStatus:
    global _cache
    async with _lock:
        if not refresh and _cache is not None and time.monotonic() - _cache[0] < CACHE_SECONDS:
            return _cache[1]
        status = await _check(settings)
        _cache = (time.monotonic(), status)
        return status
