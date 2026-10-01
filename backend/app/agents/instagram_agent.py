"""Instagram collector - official Instagram Graph API (Business Discovery) only.

Business Discovery returns public data for Instagram *Professional*
(Business/Creator) accounts, queried through your own Instagram Professional
account. Personal/consumer accounts are not available through the official
API and are reported as such - there is no scraping fallback.
"""

import asyncio
import hashlib
import hmac
import logging
from datetime import datetime
from typing import Any

import httpx

from app.config.settings import Settings, get_settings
from app.schemas.platform import ChannelProfile, CollectionError, ContentItem
from app.services.api_usage import record_instagram_call, record_meta_usage
from app.services.http_client import HttpRequestError, request_json
from app.utils.logging import log_event
from app.utils.text import caption_title
from app.utils.url_parser import ParsedLink

logger = logging.getLogger("creatorintel.instagram")

GRAPH_BASE = "https://graph.facebook.com"
_RATE_LIMIT_CODES = {4, 17, 32, 613, 80001, 80002}
_PERMISSION_CODES = {10, 200, 3}
_NOT_PROFESSIONAL_SUBCODE = 2207013

_PROFILE_FIELDS = "id,username,name,biography,website,followers_count,media_count"
_MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"
_VIEW_FIELD = "view_count"


def _graph_error(payload: Any) -> dict:
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return payload["error"]
    return {}


def _should_retry(_status: int, payload: Any) -> bool:
    return _graph_error(payload).get("code") in _RATE_LIMIT_CODES


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value)
    if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":  # 2024-09-30T10:00:00+0000
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _safe_permalink(url: Any) -> str | None:
    if isinstance(url, str) and url.startswith("https://www.instagram.com/"):
        return url
    return None


class InstagramCollector:
    source = "instagram_graph_api_business_discovery"

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client
        self._viewer_id: str | None = self.settings.meta_ig_business_account_id or None
        self._viewer_lock = asyncio.Lock()
        self._view_field_supported = True

    # ------------------------------------------------------------------ helpers
    def _auth_params(self) -> dict[str, str]:
        params: dict[str, str] = {}
        if self.settings.meta_app_secret:
            params["appsecret_proof"] = hmac.new(
                self.settings.meta_app_secret.encode(),
                self.settings.meta_access_token.encode(),
                hashlib.sha256,
            ).hexdigest()
        return params

    async def _graph_get(self, path: str, params: dict[str, Any]) -> dict:
        url = f"{GRAPH_BASE}/{self.settings.meta_api_version}/{path.lstrip('/')}"
        return await request_json(
            "GET", url,
            service="Instagram Graph API",
            params={**params, **self._auth_params()},
            headers={"Authorization": f"Bearer {self.settings.meta_access_token}"},
            should_retry=_should_retry,
            client=self.client,
            on_attempt=record_instagram_call,
            on_response=record_meta_usage,
        )

    def _translate_error(self, exc: HttpRequestError) -> CollectionError:
        error = _graph_error(exc.payload)
        code, subcode = error.get("code"), error.get("error_subcode")
        if subcode == _NOT_PROFESSIONAL_SUBCODE or code == 110:
            return CollectionError(
                CollectionError.NOT_ACCESSIBLE,
                "Instagram data unavailable: account is not a Professional (Business/Creator) account, "
                "is private, or does not exist - it cannot be accessed through the official API",
                account_access="consumer_or_unavailable",
            )
        if code == 190:
            return CollectionError(
                CollectionError.AUTH_ERROR, "Meta access token is invalid or expired", account_access="api_error"
            )
        if code in _PERMISSION_CODES:
            return CollectionError(
                CollectionError.AUTH_ERROR,
                "Meta access token lacks the permissions required for Business Discovery "
                "(instagram_basic, instagram_manage_insights, pages_read_engagement)",
                account_access="api_error",
            )
        if code in _RATE_LIMIT_CODES or exc.status_code == 429:
            return CollectionError(
                CollectionError.RATE_LIMITED, "Instagram API rate limit reached, try again later", account_access="api_error"
            )
        return CollectionError(CollectionError.API_ERROR, f"Instagram API error ({exc.message})", account_access="api_error")

    async def _resolve_viewer_id(self) -> str:
        """The caller's own Instagram Professional account ID (needed for Business Discovery)."""
        if self._viewer_id:
            return self._viewer_id
        async with self._viewer_lock:
            if self._viewer_id:
                return self._viewer_id
            try:
                data = await self._graph_get("me/accounts", {"fields": "instagram_business_account{id}", "limit": 50})
            except HttpRequestError as exc:
                raise self._translate_error(exc) from exc
            for page in data.get("data") or []:
                account = page.get("instagram_business_account") or {}
                if account.get("id"):
                    self._viewer_id = str(account["id"])
                    return self._viewer_id
            raise CollectionError(
                CollectionError.CONFIG_MISSING,
                "The Meta token cannot see any Facebook Page with a linked Instagram Business account. "
                "Link your Instagram account to a Facebook Page, then regenerate the token in Graph API Explorer "
                "selecting that Page and adding instagram_manage_insights (and business_management if the Page "
                "belongs to a Business portfolio)",
                account_access="api_unavailable",
            )

    def _discovery_fields(self, username: str, include_views: bool) -> str:
        media_fields = f"{_MEDIA_FIELDS},{_VIEW_FIELD}" if include_views else _MEDIA_FIELDS
        limit = self.settings.recent_content_fetch_limit
        return f"business_discovery.username({username}){{{_PROFILE_FIELDS},media.limit({limit}){{{media_fields}}}}}"

    async def _business_discovery(self, viewer_id: str, username: str) -> tuple[dict, bool]:
        include_views = self._view_field_supported
        try:
            data = await self._graph_get(viewer_id, {"fields": self._discovery_fields(username, include_views)})
            return data, include_views
        except HttpRequestError as exc:
            message = str(_graph_error(exc.payload).get("message", ""))
            if include_views and _graph_error(exc.payload).get("code") == 100 and _VIEW_FIELD in message:
                # The configured API version does not expose view counts on discovered media.
                self._view_field_supported = False
                log_event(logger, logging.WARNING, "instagram_view_field_unsupported", api_version=self.settings.meta_api_version)
                try:
                    data = await self._graph_get(viewer_id, {"fields": self._discovery_fields(username, False)})
                    return data, False
                except HttpRequestError as retry_exc:
                    raise self._translate_error(retry_exc) from retry_exc
            raise self._translate_error(exc) from exc

    # -------------------------------------------------------------------- main
    async def collect(self, parsed: ParsedLink) -> ChannelProfile:
        if not self.settings.instagram_configured:
            raise CollectionError(
                CollectionError.CONFIG_MISSING,
                "Instagram API is not configured (set META_ACCESS_TOKEN in backend/.env)",
                account_access="api_unavailable",
            )
        if self.settings.instagram_token_problem:
            raise CollectionError(
                CollectionError.AUTH_ERROR, self.settings.instagram_token_problem, account_access="api_error"
            )
        username = parsed.identifier or ""
        viewer_id = await self._resolve_viewer_id()
        data, views_requested = await self._business_discovery(viewer_id, username)

        profile = data.get("business_discovery")
        if not isinstance(profile, dict):
            raise CollectionError(
                CollectionError.NOT_ACCESSIBLE,
                "Instagram data unavailable for this account through the official API",
                account_access="consumer_or_unavailable",
            )

        items: list[ContentItem] = []
        for media in ((profile.get("media") or {}).get("data") or []):
            media_id = media.get("id")
            if not media_id:
                continue
            is_video = media.get("media_product_type") == "REELS" or media.get("media_type") == "VIDEO"
            items.append(
                ContentItem(
                    id=str(media_id),
                    url=_safe_permalink(media.get("permalink")),
                    published_at=_to_datetime(media.get("timestamp")),
                    title=caption_title(media.get("caption")),
                    text=media.get("caption"),
                    is_video=is_video,
                    views=_to_int(media.get(_VIEW_FIELD)) if views_requested and is_video else None,
                    likes=_to_int(media.get("like_count")),
                    comments=_to_int(media.get("comments_count")),
                )
            )

        notes: list[str] = []
        if not views_requested:
            notes.append("View counts are not available from the Instagram API for this account")
        followers = _to_int(profile.get("followers_count"))

        log_event(logger, logging.INFO, "instagram_collected", username=username, media=len(items))
        return ChannelProfile(
            platform="instagram",
            platform_id=str(profile.get("id") or username),
            display_name=profile.get("name") or profile.get("username"),
            bio=profile.get("biography"),
            website=profile.get("website"),
            audience_count=followers,
            account_access="professional_account",
            items=items,
            source=self.source,
            notes=notes,
        )
