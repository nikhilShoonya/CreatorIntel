"""YouTube collector - official YouTube Data API v3 only.

channels.list (forHandle / id / forUsername) -> uploads playlist ->
playlistItems.list -> videos.list (batched). search.list is never used.
"""

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from app.config.settings import Settings, get_settings
from app.schemas.platform import ChannelProfile, CollectionError, ContentItem
from app.services.api_usage import record_youtube_call, record_youtube_quota_exceeded
from app.services.http_client import HttpRequestError, request_json
from app.utils.logging import log_event
from app.utils.url_parser import ParsedLink

logger = logging.getLogger("creatorintel.youtube")

API_BASE = "https://www.googleapis.com/youtube/v3"
_RATE_LIMIT_REASONS = {"rateLimitExceeded", "userRateLimitExceeded"}
_QUOTA_REASONS = {"quotaExceeded", "dailyLimitExceeded"}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_AUTH_REASONS = {"keyInvalid", "keyExpired", "accessNotConfigured", "forbidden", "ipRefererBlocked", "API_KEY_INVALID"}


def _error_reasons(payload: Any) -> set[str]:
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


def _should_retry(status: int, payload: Any) -> bool:
    return status == 403 and bool(_error_reasons(payload) & _RATE_LIMIT_REASONS)


def _to_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


_ISO_DURATION = re.compile(r"^P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def _duration_seconds(value: Any) -> int | None:
    """ISO 8601 duration from the API ("PT1H2M3S") -> seconds."""
    match = _ISO_DURATION.match(str(value or ""))
    if not match or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(g or 0) for g in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _to_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


class YouTubeCollector:
    source = "youtube_data_api_v3"

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client
        self._key_index = 0  # several keys may be configured; move to the next on quota / invalid key

    @staticmethod
    def _is_key_error(exc: HttpRequestError, reasons: set[str]) -> bool:
        message = str(((exc.payload or {}).get("error") or {}).get("message", "")) if isinstance(exc.payload, dict) else ""
        return bool(reasons & _AUTH_REASONS) or exc.status_code in (401, 403) or (
            exc.status_code == 400 and "API key" in message
        )

    async def _get(self, resource: str, params: dict[str, Any]) -> dict:
        keys = self.settings.youtube_api_keys
        while True:
            index = min(self._key_index, len(keys) - 1)
            try:
                return await request_json(
                    "GET", f"{API_BASE}/{resource}",
                    service="YouTube API",
                    params=params,
                    headers={"X-Goog-Api-Key": keys[index], "Accept": "application/json"},
                    should_retry=_should_retry,
                    client=self.client,
                    on_attempt=lambda key=keys[index]: record_youtube_call(key),
                )
            except HttpRequestError as exc:
                reasons = _error_reasons(exc.payload)
                quota = bool(reasons & _QUOTA_REASONS)
                if quota:
                    record_youtube_quota_exceeded(keys[index])
                key_error = not quota and self._is_key_error(exc, reasons) and not (reasons & _RATE_LIMIT_REASONS)
                if (quota or key_error) and index + 1 < len(keys):
                    if self._key_index == index:
                        self._key_index = index + 1
                    log_event(
                        logger, logging.WARNING, "youtube_key_switched",
                        from_key=index + 1, to_key=index + 2, reason="quota_exceeded" if quota else "invalid_key",
                    )
                    continue
                if quota:
                    raise CollectionError(CollectionError.QUOTA_EXCEEDED, "YouTube API daily quota exceeded") from exc
                if reasons & _RATE_LIMIT_REASONS or exc.status_code == 429:
                    raise CollectionError(CollectionError.RATE_LIMITED, "YouTube API rate limit reached, try again later") from exc
                if key_error:
                    raise CollectionError(
                        CollectionError.AUTH_ERROR,
                        "YouTube API key is invalid or the YouTube Data API is not enabled for it",
                    ) from exc
                if exc.status_code == 404:
                    raise CollectionError(CollectionError.NOT_FOUND, "YouTube resource not found") from exc
                raise CollectionError(CollectionError.API_ERROR, exc.message) from exc

    async def _channel_id_for_video(self, video_id: str) -> str:
        data = await self._get("videos", {"part": "snippet", "id": video_id, "maxResults": 1})
        items = data.get("items") or []
        channel_id = ((items[0].get("snippet") or {}).get("channelId")) if items else None
        if not channel_id:
            raise CollectionError(
                CollectionError.NOT_FOUND, "YouTube video not found (private or deleted), so its channel is unknown",
                account_access="not_found",
            )
        return str(channel_id)

    async def _find_channel(self, parsed: ParsedLink) -> dict | None:
        part = "snippet,statistics,contentDetails"
        attempts: list[dict[str, str]]
        if parsed.identifier_type == "video":
            attempts = [{"id": await self._channel_id_for_video(parsed.identifier or "")}]
        elif parsed.identifier_type == "handle":
            attempts = [{"forHandle": f"@{parsed.identifier}"}]
        elif parsed.identifier_type == "channel_id":
            attempts = [{"id": parsed.identifier or ""}]
        elif parsed.identifier_type == "username":
            attempts = [{"forUsername": parsed.identifier or ""}, {"forHandle": f"@{parsed.identifier}"}]
        else:
            # Legacy /c/ vanity URLs have no direct API lookup; most match the handle or legacy username.
            attempts = [{"forHandle": f"@{parsed.identifier}"}, {"forUsername": parsed.identifier or ""}]

        for lookup in attempts:
            data = await self._get("channels", {"part": part, "maxResults": 1, **lookup})
            items = data.get("items") or []
            if items:
                return items[0]
        return None

    async def _recent_video_ids(self, uploads_playlist_id: str) -> list[str]:
        try:
            data = await self._get(
                "playlistItems",
                {
                    "part": "contentDetails",
                    "playlistId": uploads_playlist_id,
                    "maxResults": min(self.settings.recent_content_fetch_limit, 50),
                },
            )
        except CollectionError as exc:
            if exc.code == CollectionError.NOT_FOUND:
                return []  # channel without public uploads
            raise
        ids: list[str] = []
        for item in data.get("items") or []:
            video_id = (item.get("contentDetails") or {}).get("videoId")
            if video_id and video_id not in ids:
                ids.append(video_id)
        return ids

    async def _videos(self, video_ids: list[str]) -> list[ContentItem]:
        if not video_ids:
            return []
        data = await self._get(
            "videos",
            {"part": "snippet,statistics,contentDetails", "id": ",".join(video_ids[:50]), "maxResults": 50},
        )
        videos: list[ContentItem] = []
        for item in data.get("items") or []:
            snippet = item.get("snippet") or {}
            stats = item.get("statistics") or {}
            video_id = item.get("id")
            if not video_id:
                continue
            videos.append(
                ContentItem(
                    id=video_id,
                    url=f"https://www.youtube.com/watch?v={video_id}",
                    published_at=_to_datetime(snippet.get("publishedAt")),
                    title=snippet.get("title"),
                    text=snippet.get("description"),
                    is_video=True,
                    views=_to_int(stats.get("viewCount")),
                    likes=_to_int(stats.get("likeCount")),
                    comments=_to_int(stats.get("commentCount")),
                    tags=[str(t) for t in (snippet.get("tags") or [])][:20],
                    is_live_or_upcoming=snippet.get("liveBroadcastContent") in ("live", "upcoming"),
                    duration_seconds=_duration_seconds((item.get("contentDetails") or {}).get("duration")),
                )
            )
        videos.sort(key=lambda v: v.published_at or _EPOCH, reverse=True)
        return videos

    async def collect(self, parsed: ParsedLink) -> ChannelProfile:
        if not self.settings.youtube_configured:
            raise CollectionError(
                CollectionError.CONFIG_MISSING,
                "YouTube API is not configured (set YOUTUBE_API_KEY in backend/.env)",
                account_access="api_unavailable",
            )

        channel = await self._find_channel(parsed)
        if channel is None:
            raise CollectionError(CollectionError.NOT_FOUND, "YouTube channel not found", account_access="not_found")

        snippet = channel.get("snippet") or {}
        stats = channel.get("statistics") or {}
        thumbs = snippet.get("thumbnails") or {}
        avatar = next(
            (str(thumbs[size]["url"]) for size in ("medium", "high", "default")
             if isinstance(thumbs.get(size), dict) and thumbs[size].get("url")),
            None,
        )
        uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")

        notes: list[str] = []
        subscribers = None if stats.get("hiddenSubscriberCount") else _to_int(stats.get("subscriberCount"))
        if subscribers is None:
            notes.append("Subscriber count is hidden by the channel")

        video_ids = await self._recent_video_ids(uploads) if uploads else []
        videos = await self._videos(video_ids)
        if not videos:
            notes.append("No public uploads available")

        resolved_url = None
        if parsed.identifier_type == "video":
            custom = str(snippet.get("customUrl") or "")
            resolved_url = (
                f"https://www.youtube.com/{custom}" if custom.startswith("@")
                else f"https://www.youtube.com/channel/{channel.get('id')}"
            )
            notes.append("Channel resolved from the video link in the file")

        log_event(
            logger, logging.INFO, "youtube_collected",
            channel_id=channel.get("id"), videos=len(videos),
        )
        return ChannelProfile(
            platform="youtube",
            platform_id=str(channel.get("id")),
            display_name=snippet.get("title"),
            bio=snippet.get("description"),
            audience_count=subscribers,
            account_access="public_channel",
            profile_picture_url=avatar,
            items=videos,
            source=self.source,
            notes=notes,
            resolved_channel_url=resolved_url,
        )
