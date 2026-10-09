"""Resolve Facebook share links (facebook.com/share/v|r/<code>, fb.watch/<code>) to the real video link.

A share link does not contain the video ID, but Facebook answers it with an HTTP redirect to the canonical
video/Reel URL. For each hop one GET is sent and only the status code and Location header are used - the page
body is never read, no JavaScript runs and no cookies are kept. Safety limits:
  * every hop must be an allow-listed Facebook host and resolve only to public IP addresses (no SSRF),
  * at most MAX_HOPS redirects and a short timeout per request.
A link counts as resolved only when a redirect lands on a link that parse_video_link accepts as a Facebook
video/Reel - nothing is guessed. Resolving a link does not mean Meta allows reading that video's data.
"""

import asyncio
import ipaddress
import logging
import re
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from app.utils.logging import log_event
from app.video_performance.urls import parse_video_link

logger = logging.getLogger("creatorintel.video_performance.share_links")

MAX_HOPS = 5
TIMEOUT_SECONDS = 10.0
CONCURRENCY = 4
CACHE_LIMIT = 2000
USER_AGENT = "CreatorIntel/1.0 (+share-link resolver; reads redirect headers only)"
_FB_HOSTS = {"facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "mbasic.facebook.com"}
_FB_SHORT_HOSTS = {"fb.watch", "www.fb.watch"}
_SHARE_PATH = re.compile(r"^/share/(?:v|r)/[A-Za-z0-9_-]+/?$")
_REDIRECTS = {301, 302, 303, 307, 308}

NOTE_PREFIX = "Resolved from a Facebook share link to "
_NOTE_LINK = re.compile(re.escape(NOTE_PREFIX) + r"(https://www\.facebook\.com/\S+?)(?:;|\s|$)")

HostResolver = Callable[[str], Awaitable[list[str]]]


@dataclass(frozen=True)
class ShareResolution:
    original: str
    resolved_url: str | None = None  # canonical video/Reel URL, when resolved
    error: str | None = None


def is_share_link(url: object) -> bool:
    """facebook.com/share/v|r/<code> or fb.watch/<code> (links that do not contain a video ID)."""
    text = str(url or "").strip()
    if not text:
        return False
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    try:
        parts = urlsplit(text)
    except ValueError:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    if host in _FB_SHORT_HOSTS:
        return bool(parts.path.strip("/"))
    return host in _FB_HOSTS and bool(_SHARE_PATH.match(parts.path))


def resolution_note(url: str) -> str:
    return f"{NOTE_PREFIX}{url}"


def resolved_link_from_note(message: str | None) -> str | None:
    """The canonical link recorded on an uploaded row (keeps 'View data' matched to the file)."""
    match = _NOTE_LINK.search(message or "")
    return match.group(1) if match else None


async def _public_ips(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


class ShareLinkResolver:
    def __init__(self, client: httpx.AsyncClient | None = None, resolve_host: HostResolver | None = None):
        self._client = client  # tests inject a mock transport; production creates a fresh client per batch
        self._resolve_host = resolve_host or _public_ips
        self._cache: dict[str, ShareResolution] = {}
        self._semaphore = asyncio.Semaphore(CONCURRENCY)

    async def _safe(self, host: str) -> bool:
        if host not in _FB_HOSTS | _FB_SHORT_HOSTS:
            return False
        try:
            ips = await self._resolve_host(host)
        except OSError:
            return False
        return bool(ips) and all(ipaddress.ip_address(ip).is_global for ip in ips)

    async def _follow(self, client: httpx.AsyncClient, url: str) -> ShareResolution:
        current = url if "://" in url else "https://" + url.lstrip("/")
        for _ in range(MAX_HOPS):
            host = (urlsplit(current).hostname or "").lower().rstrip(".")
            if not await self._safe(host):
                return ShareResolution(url, error="Facebook share link redirected to an unexpected address - not followed")
            client.cookies.clear()  # never send cookies between hops
            try:
                async with client.stream("GET", current) as response:  # headers only, the body is never read
                    status, location = response.status_code, response.headers.get("location")
            except httpx.HTTPError as exc:
                log_event(logger, logging.WARNING, "vt_share_link_network_error", reason=type(exc).__name__)
                return ShareResolution(url, error="Could not reach Facebook to resolve the share link - try again later")
            if status not in _REDIRECTS or not location:
                return ShareResolution(
                    url, error="Facebook did not redirect this share link to a video (it may be deleted, private, "
                               "expired or not a video link)",
                )
            current = str(httpx.URL(current).join(location))
            parsed = parse_video_link(current)
            if parsed.ok and parsed.platform == "facebook":
                return ShareResolution(url, resolved_url=parsed.url)
        return ShareResolution(url, error="Facebook share link redirected too many times - not resolved")

    async def resolve(self, url: str) -> ShareResolution:
        cached = self._cache.get(url)
        if cached:
            return cached
        async with self._semaphore:
            if self._client is not None:
                result = await self._follow(self._client, url)
            else:
                async with httpx.AsyncClient(
                    timeout=TIMEOUT_SECONDS, follow_redirects=False, headers={"User-Agent": USER_AGENT}
                ) as client:
                    result = await self._follow(client, url)
        if result.resolved_url:  # only successes are cached; failures may be transient
            if len(self._cache) >= CACHE_LIMIT:
                self._cache.pop(next(iter(self._cache)))
            self._cache[url] = result
        log_event(logger, logging.INFO, "vt_share_link", resolved=bool(result.resolved_url))
        return result

    async def resolve_many(self, urls: Iterable[str]) -> dict[str, ShareResolution]:
        unique = list(dict.fromkeys(u for u in urls if is_share_link(u)))
        results = await asyncio.gather(*(self.resolve(u) for u in unique))
        return dict(zip(unique, results))


_default: ShareLinkResolver | None = None


def get_share_resolver() -> ShareLinkResolver:
    global _default
    if _default is None:
        _default = ShareLinkResolver()
    return _default
