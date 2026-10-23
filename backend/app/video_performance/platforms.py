"""Platform access for Video Performance (official APIs only).

YouTube Data API v3:
    videos.list (up to 50 IDs per call) for per-video statistics;
    channels.list + playlistItems.list (uploads playlist) for new-video discovery.
Instagram Graph API (Business Discovery):
    a Professional account's recent media, with like/comment/view counts. The API
    cannot look up an arbitrary reel by URL, so a reel is found by scanning its
    owner's recent media and matching the shortcode.
Facebook Graph API (configured Page only):
    videos/reels posted on META_FACEBOOK_PAGE_ID, read with a Page access token derived from the
    System User token. Meta does not allow reading other Pages' videos without App Review.
"""

import asyncio
import hashlib
import hmac
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from app.config.settings import Settings, get_settings
from app.services.api_usage import (
    record_facebook_call,
    record_instagram_call,
    record_meta_usage,
    record_youtube_call,
    record_youtube_quota_exceeded,
)
from app.services import meta_budget, youtube_keys
from app.services.http_client import HttpRequestError, request_json
from app.utils.logging import log_event
from app.utils.url_parser import ParsedLink
from app.video_performance.urls import instagram_shortcode_from_permalink, parse_instagram_handle

logger = logging.getLogger("creatorintel.video_performance.platforms")

YOUTUBE_API = "https://www.googleapis.com/youtube/v3"
GRAPH_API = "https://graph.facebook.com"
META_WAIT = "Pending - waiting for Meta's hourly limit"


def _meta_attempt(record, request_budget: list[int] | None = None) -> "Any":
    """on_attempt hook for Graph calls: reserve the call in the Meta budget (or stop), then count it."""
    def attempt() -> None:
        if request_budget is not None and request_budget[0] <= 0:
            raise PlatformError(PlatformError.OWNER_UNRESOLVED, "Instagram owner search incomplete: request limit reached")
        meta_budget.take()
        if request_budget is not None:
            request_budget[0] -= 1
        record()
    return attempt


def meta_wait_error(exc: "meta_budget.MetaBudgetExhausted") -> "PlatformError":
    when = datetime.fromtimestamp(exc.resume_at).astimezone()
    return PlatformError(PlatformError.RATE_LIMITED, f"{META_WAIT} ({exc.reason}; continues after {when:%H:%M})")


class PlatformError(Exception):
    CONFIG = "config"
    AUTH = "auth"
    NOT_FOUND = "not_found"
    UNSUPPORTED = "unsupported"
    RATE_LIMITED = "rate_limited"
    API = "api_error"
    OWNER_UNRESOLVED = "owner_unresolved"
    OWNER_CONFLICT = "owner_conflict"

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class VideoMetrics:
    identifier: str
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    title: str | None = None
    caption: str | None = None
    published_at: datetime | None = None
    channel_id: str | None = None
    channel_title: str | None = None
    owner_username: str | None = None
    notes: list[str] = field(default_factory=list)
    metrics_source: str | None = None
    view_metric: str | None = None
    source_metrics: dict | None = None
    source_run_id: str | None = None


@dataclass
class ChannelInfo:
    platform_channel_id: str | None
    platform_name: str | None
    uploads_playlist_id: str | None = None
    username: str | None = None


@dataclass
class DiscoveredVideo:
    identifier: str
    url: str
    published_at: datetime | None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dt(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    if len(text) > 5 and text[-5] in "+-" and text[-3] != ":":
        text = f"{text[:-2]}:{text[-2:]}"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# --------------------------------------------------------------------- YouTube
EXTRA_READ_DELAY_SECONDS = 1.0  # pause between repeated statistics reads (they reach different YouTube servers)


class YouTubeVideoClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client

    def _require_key(self) -> list[str]:
        keys = self.settings.youtube_api_keys
        if not keys:
            raise PlatformError(PlatformError.CONFIG, "YouTube API is not configured (YOUTUBE_API_KEY)")
        return keys

    async def _get(self, resource: str, params: dict[str, Any]) -> dict:
        keys = self._require_key()
        # Always start from the first usable key (a key skipped for quota is used again after the daily reset).
        candidates = youtube_keys.usable(keys) or keys
        for position, key in enumerate(candidates):
            try:
                return await request_json(
                    "GET", f"{YOUTUBE_API}/{resource}", service="YouTube API", params=params,
                    headers={"X-Goog-Api-Key": key}, client=self.client,
                    on_attempt=lambda key=key: record_youtube_call(key),
                )
            except HttpRequestError as exc:
                reasons = youtube_keys.error_reasons(exc.payload)
                quota = bool(reasons & youtube_keys.QUOTA_REASONS)
                bad_key = not quota and youtube_keys.is_key_problem(exc.status_code, exc.payload)
                if quota:
                    record_youtube_quota_exceeded(key)
                    youtube_keys.mark_quota_exhausted(key)
                elif bad_key:
                    youtube_keys.mark_invalid(key)
                if (quota or bad_key) and position + 1 < len(candidates):
                    continue
                if quota:
                    raise PlatformError(PlatformError.RATE_LIMITED, "YouTube API daily quota exceeded") from exc
                if bad_key:
                    raise PlatformError(PlatformError.AUTH, "YouTube API key is invalid or the API is not enabled") from exc
                if exc.status_code == 404:
                    raise PlatformError(PlatformError.NOT_FOUND, "YouTube resource not found") from exc
                raise PlatformError(PlatformError.API, exc.message) from exc
        raise PlatformError(PlatformError.CONFIG, "YouTube API is not configured (YOUTUBE_API_KEY)")

    async def videos(self, video_ids: list[str]) -> dict[str, VideoMetrics]:
        """Statistics + snippet for up to 50 videos per request. Missing IDs are absent from the result.

        YouTube answers from several servers that can hold different copies of a view count (an older copy can
        be ~100 views behind). The statistics are therefore read YOUTUBE_VIEW_READS times and the highest view
        count of each video is kept - every value is one YouTube itself returned.
        """
        found: dict[str, VideoMetrics] = {}
        for start in range(0, len(video_ids), 50):
            batch = video_ids[start : start + 50]
            found.update(await self._read_batch(batch))
            for _ in range(self.settings.youtube_view_reads - 1):
                await asyncio.sleep(EXTRA_READ_DELAY_SECONDS)
                try:
                    data = await self._get("videos", {"part": "statistics", "id": ",".join(batch), "maxResults": 50})
                except PlatformError:
                    break  # the first reading is complete on its own
                for item in data.get("items") or []:
                    current, stats = found.get(item.get("id")), item.get("statistics") or {}
                    views = _int(stats.get("viewCount"))
                    if current is not None and views is not None and (current.views is None or views > current.views):
                        current.views = views
                        current.likes = _int(stats.get("likeCount"))
                        current.comments = _int(stats.get("commentCount"))
        return found

    async def _read_batch(self, batch: list[str]) -> dict[str, VideoMetrics]:
        found: dict[str, VideoMetrics] = {}
        if batch:
            data = await self._get("videos", {"part": "snippet,statistics", "id": ",".join(batch), "maxResults": 50})
            for item in data.get("items") or []:
                snippet, stats = item.get("snippet") or {}, item.get("statistics") or {}
                metrics = VideoMetrics(
                    identifier=item.get("id"),
                    views=_int(stats.get("viewCount")),
                    likes=_int(stats.get("likeCount")),
                    comments=_int(stats.get("commentCount")),
                    title=snippet.get("title"),
                    caption=snippet.get("description"),
                    published_at=_dt(snippet.get("publishedAt")),
                    channel_id=snippet.get("channelId"),
                    channel_title=snippet.get("channelTitle"),
                )
                if metrics.likes is None:
                    metrics.notes.append("Likes are hidden on this video")
                if metrics.comments is None:
                    metrics.notes.append("Comments are disabled on this video")
                found[metrics.identifier] = metrics
        return found

    async def resolve_channel(self, parsed: ParsedLink) -> ChannelInfo:
        lookups: list[dict[str, str]]
        if parsed.identifier_type == "channel_id":
            lookups = [{"id": parsed.identifier or ""}]
        elif parsed.identifier_type == "username":
            lookups = [{"forUsername": parsed.identifier or ""}, {"forHandle": f"@{parsed.identifier}"}]
        else:
            lookups = [{"forHandle": f"@{parsed.identifier}"}, {"forUsername": parsed.identifier or ""}]
        for lookup in lookups:
            data = await self._get("channels", {"part": "snippet,contentDetails", "maxResults": 1, **lookup})
            items = data.get("items") or []
            if items:
                channel = items[0]
                uploads = ((channel.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
                return ChannelInfo(channel.get("id"), (channel.get("snippet") or {}).get("title"), uploads)
        raise PlatformError(PlatformError.NOT_FOUND, "YouTube channel not found")

    async def recent_uploads(self, uploads_playlist_id: str, limit: int = 15) -> list[DiscoveredVideo]:
        try:
            data = await self._get(
                "playlistItems", {"part": "contentDetails", "playlistId": uploads_playlist_id, "maxResults": min(limit, 50)}
            )
        except PlatformError as exc:
            if exc.code == PlatformError.NOT_FOUND:
                return []
            raise
        videos = []
        for item in data.get("items") or []:
            details = item.get("contentDetails") or {}
            video_id = details.get("videoId")
            if video_id:
                videos.append(
                    DiscoveredVideo(video_id, f"https://www.youtube.com/watch?v={video_id}", _dt(details.get("videoPublishedAt")))
                )
        return videos


# ------------------------------------------------------------------- Instagram
_RATE_CODES = {4, 17, 32, 613, 80001, 80002}
_PROFILE_FIELDS = "id,username,name,followers_count"
_MEDIA_FIELDS = "id,caption,media_type,media_product_type,permalink,timestamp,like_count,comments_count"


@dataclass
class InstagramMedia:
    shortcode: str
    url: str
    is_video: bool
    views: int | None
    likes: int | None
    comments: int | None
    caption: str | None
    published_at: datetime | None


class InstagramVideoClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client
        self._viewer_id: str | None = self.settings.meta_ig_business_account_id or None
        self._viewer_lock = asyncio.Lock()
        self._views_supported = True

    def _check_config(self) -> None:
        if not self.settings.instagram_configured:
            raise PlatformError(PlatformError.CONFIG, "Instagram API is not configured (META_ACCESS_TOKEN)")
        if self.settings.instagram_token_problem:
            raise PlatformError(PlatformError.AUTH, self.settings.instagram_token_problem)

    def _params(self, **extra: Any) -> dict[str, Any]:
        token = self.settings.meta_access_token.strip()
        if self.settings.meta_app_secret:
            extra["appsecret_proof"] = hmac.new(
                self.settings.meta_app_secret.encode(), token.encode(), hashlib.sha256
            ).hexdigest()
        return extra

    async def _get(self, path: str, params: dict[str, Any], request_budget: list[int] | None = None) -> dict:
        try:
            return await request_json(
                "GET", f"{GRAPH_API}/{self.settings.meta_api_version}/{path}", service="Instagram Graph API",
                params=self._params(**params),
                headers={"Authorization": f"Bearer {self.settings.meta_access_token.strip()}"},
                # Rate-limit answers are not retried here: Meta's limits run per hour, so retrying within
                # seconds never succeeds and only uses up more of the allowance.
                client=self.client,
                on_attempt=_meta_attempt(record_instagram_call, request_budget),
                on_response=record_meta_usage,
            )
        except meta_budget.MetaBudgetExhausted as exc:  # nothing was sent
            raise meta_wait_error(exc) from exc
        except HttpRequestError as exc:
            error = self._translate(exc)
            if error.code == PlatformError.RATE_LIMITED:
                meta_budget.note_rate_limited()
            raise error from exc

    @staticmethod
    def _translate(exc: HttpRequestError) -> PlatformError:
        error = (exc.payload or {}).get("error", {}) if isinstance(exc.payload, dict) else {}
        code, sub, message = error.get("code"), error.get("error_subcode"), str(error.get("message", ""))
        if sub == 2207013 or code == 110:
            return PlatformError(
                PlatformError.UNSUPPORTED,
                "Instagram data unavailable: the account is not a Professional (Business/Creator) account, "
                "is private, or does not exist",
            )
        if code == 190:
            return PlatformError(PlatformError.AUTH, "Meta access token is invalid or expired")
        if code == 100 and sub == 33:
            # Every Business Discovery request is made on OUR Instagram account, so "object does not exist /
            # missing permissions" means the token cannot reach that account - not a problem with the video.
            return PlatformError(PlatformError.AUTH, "The Meta token cannot access your Instagram Business account (META_IG_BUSINESS_ACCOUNT_ID) - check that the token is valid and that this account is assigned to it")
        if code in (10, 200, 3):
            return PlatformError(PlatformError.AUTH, "Instagram data unavailable with current API permissions")
        if code in _RATE_CODES or exc.status_code == 429:
            return PlatformError(PlatformError.RATE_LIMITED, "Instagram API rate limit reached, try again later")
        if code == 100 and "view_count" in message:
            return PlatformError("view_field", message)
        return PlatformError(PlatformError.API, f"Instagram API error: {message[:200] or exc.message}")

    async def _viewer(self) -> str:
        if self._viewer_id:
            return self._viewer_id
        async with self._viewer_lock:
            if not self._viewer_id:
                data = await self._get("me/accounts", {"fields": "instagram_business_account{id}", "limit": 50})
                for page in data.get("data") or []:
                    account = page.get("instagram_business_account") or {}
                    if account.get("id"):
                        self._viewer_id = str(account["id"])
                        break
            if not self._viewer_id:
                raise PlatformError(
                    PlatformError.CONFIG,
                    "The Meta token cannot see a Facebook Page with a linked Instagram Business account",
                )
            return self._viewer_id

    async def profile_media(
        self, username: str, pages: int = 1, per_page: int = 50, find: set[str] | None = None,
        request_budget: list[int] | None = None,
    ) -> tuple[dict, list[InstagramMedia]]:
        """A Professional account's profile + its most recent media (newest first).

        With `find`, pages are read one at a time and reading stops as soon as all those shortcodes were seen
        (one call when the reels are among the latest 50 posts) - older pages are read only when needed.
        """
        username = parse_instagram_handle(username)
        if username is None:
            raise PlatformError(PlatformError.OWNER_UNRESOLVED, "A valid Instagram owner username is required")
        self._check_config()
        viewer = await self._viewer()
        media: list[InstagramMedia] = []
        profile: dict = {}
        after: str | None = None
        cursors: set[str] = set()
        pages_read = 0
        complete = False
        scan_error = None
        while pages_read < pages:
            if request_budget is not None:
                if request_budget[0] <= 0:
                    break
            fields_media = f"{_MEDIA_FIELDS},view_count" if self._views_supported else _MEDIA_FIELDS
            cursor = f".after({after})" if after else ""
            fields = f"business_discovery.username({username}){{{_PROFILE_FIELDS},media{cursor}.limit({per_page}){{{fields_media}}}}}"
            try:
                data = await self._get(viewer, {"fields": fields}, request_budget=request_budget)
            except PlatformError as exc:
                if exc.code == "view_field" and self._views_supported:
                    self._views_supported = False  # this API version does not expose view counts
                    log_event(logger, logging.WARNING, "vt_instagram_view_count_unsupported")
                    continue
                if media:
                    scan_error = exc
                    break  # keep exact matches from earlier pages; unresolved reels retain this error
                raise
            pages_read += 1
            discovery = data.get("business_discovery")
            if not discovery:
                raise PlatformError(PlatformError.UNSUPPORTED, "Instagram account is unavailable through Business Discovery")
            profile = profile or {k: discovery.get(k) for k in ("id", "username", "name", "followers_count")}
            returned_owner = parse_instagram_handle(profile.get("username"))
            if returned_owner and returned_owner != username:
                raise PlatformError(PlatformError.OWNER_CONFLICT, "Business Discovery returned a different Instagram username")
            block = discovery.get("media") or {}
            if not isinstance(block.get("data"), list):
                raise PlatformError(PlatformError.API, "Instagram Business Discovery did not return a media list")
            for item in block.get("data") or []:
                shortcode = instagram_shortcode_from_permalink(item.get("permalink"))
                if not shortcode:
                    continue
                is_video = item.get("media_product_type") == "REELS" or item.get("media_type") == "VIDEO"
                media.append(
                    InstagramMedia(
                        shortcode=shortcode,
                        url=item.get("permalink"),
                        is_video=is_video,
                        views=_int(item.get("view_count")) if self._views_supported else None,
                        likes=_int(item.get("like_count")),
                        comments=_int(item.get("comments_count")),
                        caption=item.get("caption"),
                        published_at=_dt(item.get("timestamp")),
                    )
                )
            after = ((block.get("paging") or {}).get("cursors") or {}).get("after")
            # Nested Business Discovery edges can return cursors without a paging.next URL.
            if not after or not block["data"]:
                complete = True
                break
            if after in cursors:
                break
            cursors.add(after)
            if find and find <= {m.shortcode for m in media}:
                break  # every wanted reel found: no need to read older posts
        profile["_scan_complete"] = complete
        profile["_scan_pages"] = pages_read
        profile["_scan_error"] = scan_error
        return profile, media

# -------------------------------------------------------------------- Facebook
_FB_VIEW_METRICS = ("total_video_views", "fb_reels_total_plays", "blue_reels_play_count")
_FB_VIDEO_FIELDS = (
    "id,title,description,permalink_url,created_time,length,from{id,name},"
    "likes.summary(true).limit(0),comments.summary(true).limit(0)"
)


class FacebookVideoClient:
    """Videos and reels on the configured Facebook Page.

    META_ACCESS_TOKEN is the System User token. Page endpoints need a Page access token, which is derived
    from it once (GET /{page_id}?fields=access_token), kept only in memory and re-derived if Meta rejects it.
    Neither token is ever logged or returned to the frontend.
    """

    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client
        self._page_token: str | None = None
        self._token_lock = asyncio.Lock()
        self._concurrency = asyncio.Semaphore(4)

    @property
    def page_id(self) -> str:
        return self.settings.meta_facebook_page_id.strip()

    def _check_config(self) -> None:
        if not self.settings.meta_access_token.strip():
            raise PlatformError(PlatformError.CONFIG, "Meta API is not configured (META_ACCESS_TOKEN)")
        if not self.page_id:
            raise PlatformError(PlatformError.CONFIG, "Facebook is not configured (META_FACEBOOK_PAGE_ID)")

    def _proof(self, token: str) -> dict[str, str]:
        secret = self.settings.meta_app_secret
        return {"appsecret_proof": hmac.new(secret.encode(), token.encode(), hashlib.sha256).hexdigest()} if secret else {}

    async def _call(self, path: str, params: dict[str, Any], token: str) -> dict:
        try:
            return await request_json(
                "GET", f"{GRAPH_API}/{self.settings.meta_api_version}/{path}", service="Facebook Graph API",
                params={**params, **self._proof(token)},
                headers={"Authorization": f"Bearer {token}"},
                # no retry on rate-limit answers (hourly limits - an immediate retry only uses more of them)
                client=self.client,
                on_attempt=_meta_attempt(record_facebook_call),
                on_response=record_meta_usage,
            )
        except meta_budget.MetaBudgetExhausted as exc:  # nothing was sent
            raise meta_wait_error(exc) from exc
        except HttpRequestError as exc:
            error = self._translate(exc)
            if error.code == PlatformError.RATE_LIMITED:
                meta_budget.note_rate_limited()
            raise error from exc

    @staticmethod
    def _translate(exc: HttpRequestError) -> PlatformError:
        error = (exc.payload or {}).get("error", {}) if isinstance(exc.payload, dict) else {}
        code, sub, message = error.get("code"), error.get("error_subcode"), str(error.get("message", ""))
        if code == 190:
            result = PlatformError(PlatformError.AUTH, "Meta access token is invalid or expired")
        elif code in _RATE_CODES or exc.status_code == 429:
            result = PlatformError(PlatformError.RATE_LIMITED, "Facebook API rate limit reached, try again later")
        elif code in (10, 200, 3):
            result = PlatformError(PlatformError.AUTH, "Facebook data unavailable with the current Meta permissions")
        elif code == 100 and sub == 33:
            result = PlatformError(
                PlatformError.NOT_FOUND,
                "Facebook video not found on your Page (deleted, private, wrong link, or posted by another Page)",
            )
        else:
            result = PlatformError(PlatformError.API, f"Facebook API error: {message[:200] or exc.message}")
        result.graph_code = code  # type: ignore[attr-defined]
        return result

    async def page_token(self, refresh: bool = False) -> str:
        """Page access token derived from the System User token (memory only)."""
        self._check_config()
        async with self._token_lock:
            if self._page_token and not refresh:
                return self._page_token
            try:
                data = await self._call(self.page_id, {"fields": "access_token"}, self.settings.meta_access_token.strip())
            except PlatformError as exc:
                if exc.code in (PlatformError.NOT_FOUND, PlatformError.AUTH):
                    raise PlatformError(
                        PlatformError.AUTH,
                        "The Meta System User cannot access the configured Facebook Page - check META_FACEBOOK_PAGE_ID "
                        "and that the Page is assigned to the System User",
                    ) from exc
                raise
            token = data.get("access_token") if isinstance(data, dict) else None
            if not token:
                raise PlatformError(
                    PlatformError.AUTH,
                    "Meta did not return a Page access token - assign the Facebook Page to the System User",
                )
            self._page_token = token
            log_event(logger, logging.INFO, "vt_facebook_page_token_ready", page_id=self.page_id)
            return token

    async def _page_get(self, path: str, params: dict[str, Any]) -> dict:
        token = await self.page_token()
        try:
            return await self._call(path, params, token)
        except PlatformError as exc:
            if getattr(exc, "graph_code", None) != 190:
                raise
            token = await self.page_token(refresh=True)  # derived token was revoked/rotated: get a fresh one once
            return await self._call(path, params, token)

    async def _views(self, video_id: str) -> int | None:
        """Views/plays from video insights; None when Meta does not provide them for this video/token.

        Videos report total_video_views; Reels report fb_reels_total_plays / blue_reels_play_count instead,
        so those are tried only when the first metric returns nothing.
        """
        for metric_name in _FB_VIEW_METRICS:
            try:
                data = await self._page_get(f"{video_id}/video_insights", {"metric": metric_name})
            except PlatformError as exc:
                if exc.code == PlatformError.RATE_LIMITED:
                    raise
                log_event(logger, logging.INFO, "vt_facebook_views_unavailable", video_id=video_id,
                          metric=metric_name, reason=exc.code)
                continue
            for metric in data.get("data") or []:
                values = metric.get("values") or []
                value = _int(values[-1].get("value")) if values else None
                if metric.get("name") == metric_name and value is not None:
                    return value
        return None

    async def video(self, video_id: str) -> VideoMetrics:
        self._check_config()
        async with self._concurrency:
            try:
                data = await self._page_get(video_id, {"fields": _FB_VIDEO_FIELDS})
            except PlatformError as exc:
                if getattr(exc, "graph_code", None) in (10, 200):
                    # The Page token works (it was just derived), so a permission error here means the video is
                    # not on our Page - Meta does not allow reading other Pages' videos (verified live).
                    raise PlatformError(
                        PlatformError.UNSUPPORTED,
                        "This video is not on your Facebook Page. Meta only allows reading videos and reels posted "
                        "on the configured Page",
                    ) from exc
                raise
            owner = data.get("from") or {}
            if owner.get("id") and str(owner["id"]) != self.page_id:
                raise PlatformError(
                    PlatformError.UNSUPPORTED,
                    f"This video belongs to another Facebook Page ({owner.get('name') or owner['id']}). "
                    "Only videos on your configured Page can be tracked",
                )
            views = await self._views(video_id)
        metrics = VideoMetrics(
            identifier=str(data.get("id") or video_id),
            views=views,
            likes=_int(((data.get("likes") or {}).get("summary") or {}).get("total_count")),
            comments=_int(((data.get("comments") or {}).get("summary") or {}).get("total_count")),
            title=data.get("title") or None,
            caption=data.get("description") or None,
            published_at=_dt(data.get("created_time")),
            channel_id=str(owner["id"]) if owner.get("id") else None,
            channel_title=owner.get("name"),
        )
        if views is None:
            metrics.notes.append("Facebook did not return a view count for this video (video insights unavailable)")
        if metrics.likes is None:
            metrics.notes.append("Likes are not available for this video")
        return metrics
