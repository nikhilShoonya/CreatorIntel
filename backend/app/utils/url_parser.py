"""Parse and normalise creator channel links.

Only the URL is used to decide the platform - never the channel name.
Nothing here performs network requests (no SSRF surface).
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit

PlatformKind = Literal["youtube", "instagram", "unsupported", "invalid"]

INSTAGRAM_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
YOUTUBE_VIDEO_HOSTS = {"youtu.be", "www.youtu.be"}

# First path segments that are Instagram features rather than usernames.
_INSTAGRAM_RESERVED = {
    "p", "reel", "tv", "explore", "accounts", "direct", "about", "legal",
    "developer", "web", "challenge", "emails", "session", "privacy",
}
_INSTAGRAM_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")

_YOUTUBE_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_\-]{22}$")
_YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_\-]{11}$")
_YOUTUBE_LEGACY_NAME = re.compile(r"^[A-Za-z0-9._\-]{1,100}$")
_YOUTUBE_RESERVED = {
    "watch", "shorts", "live", "playlist", "results", "feed", "embed", "redirect",
    "hashtag", "premium", "gaming", "account", "signin", "logout", "t", "about",
}


@dataclass(frozen=True)
class ParsedLink:
    platform: PlatformKind
    identifier: str | None = None
    # instagram: "username"; youtube: "handle" | "channel_id" | "username" | "custom" | "video"
    identifier_type: str | None = None
    canonical_url: str | None = None
    error: str | None = None

    @property
    def is_supported(self) -> bool:
        return self.platform in ("youtube", "instagram") and self.identifier is not None

    @property
    def normalized_identifier(self) -> str | None:
        """Stable key used together with the platform for de-duplication."""
        if not self.is_supported:
            return None
        if self.platform == "instagram":
            return self.identifier
        prefix = {"handle": "@", "channel_id": "channel:", "username": "user:", "custom": "c:", "video": "video:"}[
            self.identifier_type or ""
        ]
        return f"{prefix}{self.identifier}"


def _invalid(message: str) -> ParsedLink:
    return ParsedLink(platform="invalid", error=message)


def parse_channel_link(raw: object) -> ParsedLink:
    if raw is None:
        return _invalid("Channel link is empty")
    text = str(raw).strip().strip("<>\"'")
    if not text or text.lower() in {"nan", "none", "null"}:
        return _invalid("Channel link is empty")
    if len(text) > 2048:
        return _invalid("Channel link is too long")
    if any(ch.isspace() for ch in text):
        return _invalid("Channel link contains spaces")

    if "://" not in text:
        text = "https://" + text.lstrip("/")

    try:
        parts = urlsplit(text)
    except ValueError:
        return _invalid("Channel link is not a valid URL")

    if parts.scheme.lower() not in ("http", "https"):
        return _invalid("Only http(s) links are supported")

    host = (parts.hostname or "").lower().rstrip(".")
    if not host or "." not in host:
        return _invalid("Channel link is not a valid URL")

    segments = [unquote(s) for s in parts.path.split("/") if s]

    if host in INSTAGRAM_HOSTS:
        return _parse_instagram(segments)
    if host in YOUTUBE_HOSTS:
        return _parse_youtube(segments, parse_qs(parts.query))
    if host in YOUTUBE_VIDEO_HOSTS:
        return _youtube_video(segments[0] if segments else "")
    return ParsedLink(platform="unsupported", error=f"Unsupported platform ({host}). Only YouTube and Instagram are supported")


def _parse_instagram(segments: list[str]) -> ParsedLink:
    if not segments:
        return _invalid("Instagram link has no username")
    first = segments[0]
    if first.lower() == "stories" and len(segments) >= 2:
        first = segments[1]
    elif first.lower() in _INSTAGRAM_RESERVED:
        if first.lower() in {"p", "reel", "tv"}:
            return _invalid("This is an Instagram post/reel link, not a profile link")
        return _invalid("Instagram link does not point to a profile")
    username = first.lstrip("@").lower()
    if not _INSTAGRAM_USERNAME.match(username) or username.strip(".") != username:
        return _invalid("Instagram username in link is not valid")
    return ParsedLink(
        platform="instagram",
        identifier=username,
        identifier_type="username",
        canonical_url=f"https://www.instagram.com/{username}/",
    )


def _valid_youtube_handle(handle: str) -> bool:
    """YouTube handles are 3-30 characters: letters / digits in any script (with their combining marks,
    e.g. Devanagari vowel signs), plus underscore, hyphen, period and middle dot."""
    return 3 <= len(handle) <= 30 and all(
        ch.isalnum() or ch in "._-·" or unicodedata.category(ch).startswith("M") for ch in handle
    )


def _youtube_video(video_id: str) -> ParsedLink:
    """A video link: the channel is resolved later through the API (videos.list -> channelId)."""
    if not _YOUTUBE_VIDEO_ID.match(video_id):
        return _invalid("YouTube video link is not valid")
    return ParsedLink("youtube", video_id, "video", f"https://www.youtube.com/watch?v={video_id}")


def _parse_youtube(segments: list[str], query: dict[str, list[str]] | None = None) -> ParsedLink:
    if not segments:
        return _invalid("YouTube link has no channel")
    first = segments[0]
    if first.lower() == "watch":
        return _youtube_video(((query or {}).get("v") or [""])[0])
    if first.lower() in ("shorts", "live", "embed") and len(segments) >= 2:
        return _youtube_video(segments[1])

    if first.startswith("@"):
        handle = first[1:]
        if not _valid_youtube_handle(handle):
            return _invalid("YouTube handle in link is not valid")
        handle = handle.lower()
        return ParsedLink("youtube", handle, "handle", f"https://www.youtube.com/@{handle}")

    kind = first.lower()
    if kind == "channel":
        if len(segments) < 2 or not _YOUTUBE_CHANNEL_ID.match(segments[1]):
            return _invalid("YouTube channel ID in link is not valid")
        channel_id = segments[1]
        return ParsedLink("youtube", channel_id, "channel_id", f"https://www.youtube.com/channel/{channel_id}")
    if kind in ("user", "c"):
        if len(segments) < 2 or not _YOUTUBE_LEGACY_NAME.match(segments[1]):
            return _invalid("YouTube channel name in link is not valid")
        name = segments[1].lower()
        id_type = "username" if kind == "user" else "custom"
        return ParsedLink("youtube", name, id_type, f"https://www.youtube.com/{kind}/{name}")
    if kind in ("watch", "shorts", "live", "embed"):
        return _invalid("YouTube video link is not valid")
    if kind in _YOUTUBE_RESERVED:
        return _invalid("YouTube link does not point to a channel")
    # Legacy vanity URL such as youtube.com/SomeName
    if _YOUTUBE_LEGACY_NAME.match(first):
        name = first.lower()
        return ParsedLink("youtube", name, "custom", f"https://www.youtube.com/c/{name}")
    return _invalid("YouTube link does not point to a channel")


def short_display_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    return f"{host}{parts.path}".rstrip("/")
