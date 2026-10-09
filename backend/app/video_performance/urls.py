"""Parse video / reel links (YouTube, Instagram, Facebook) and Instagram handles. No network access here."""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
_YT_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_IG_HOSTS = {"instagram.com", "www.instagram.com", "m.instagram.com", "instagr.am", "www.instagr.am"}
_FB_HOSTS = {
    "facebook.com", "www.facebook.com", "m.facebook.com", "web.facebook.com", "mbasic.facebook.com",
    "business.facebook.com", "fb.com", "www.fb.com",
}
_FB_SHORT_HOSTS = {"fb.watch", "www.fb.watch"}
_FB_VIDEO_ID = re.compile(r"^\d{5,25}$")
_FB_NO_ID = (
    "do not contain the video ID, and Meta's official API cannot resolve them. Open the video on Facebook and "
    "copy the address bar link instead (facebook.com/<page>/videos/<id> or facebook.com/reel/<id>)"
)

_YT_VIDEO_ID = re.compile(r"^[A-Za-z0-9_\-]{11}$")
_IG_SHORTCODE = re.compile(r"^[A-Za-z0-9_\-]{5,40}$")
_IG_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_IG_MEDIA_PATHS = {"reel", "reels", "p", "tv"}
_IG_RESERVED = {"explore", "accounts", "direct", "stories", "about", "legal", "developer", "web", *_IG_MEDIA_PATHS}


@dataclass(frozen=True)
class ParsedVideo:
    platform: str | None  # youtube | instagram | facebook | None (invalid)
    identifier: str | None = None  # YouTube video ID (case-sensitive) / Instagram shortcode / Facebook video ID
    url: str | None = None  # canonical URL
    owner_username: str | None = None  # Instagram owner when present in the link
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.platform is not None and self.identifier is not None


def _invalid(message: str) -> ParsedVideo:
    return ParsedVideo(platform=None, error=message)


def parse_video_link(raw: object) -> ParsedVideo:
    text = str(raw or "").strip().strip("<>\"'")
    if not text or text.lower() in {"nan", "none", "null"}:
        return _invalid("Video link is empty")
    if len(text) > 2048 or any(ch.isspace() for ch in text):
        return _invalid("Video link is not a valid URL")
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    try:
        parts = urlsplit(text)
    except ValueError:
        return _invalid("Video link is not a valid URL")
    if parts.scheme.lower() not in ("http", "https"):
        return _invalid("Only http(s) links are supported")
    host = (parts.hostname or "").lower().rstrip(".")
    segments = [unquote(s) for s in parts.path.split("/") if s]

    if host in _YT_SHORT_HOSTS:
        return _youtube(segments[0] if segments else "")
    if host in _YT_HOSTS:
        if segments[:1] == ["watch"]:
            return _youtube((parse_qs(parts.query).get("v") or [""])[0])
        if len(segments) >= 2 and segments[0].lower() in ("shorts", "live", "embed", "v"):
            return _youtube(segments[1])
        return _invalid("This YouTube link is not a video link (use a watch, shorts or youtu.be URL)")
    if host in _IG_HOSTS:
        return _instagram(segments)
    if host in _FB_SHORT_HOSTS:
        return _invalid(f"fb.watch short links {_FB_NO_ID}")
    if host in _FB_HOSTS:
        return _facebook(segments, parts.query)
    if not host or "." not in host:
        return _invalid("Video link is not a valid URL")
    return _invalid(f"Unsupported platform ({host}). Only YouTube and Instagram videos are supported")


def _youtube(video_id: str) -> ParsedVideo:
    if not _YT_VIDEO_ID.match(video_id or ""):
        return _invalid("YouTube video ID in the link is not valid")
    return ParsedVideo("youtube", video_id, f"https://www.youtube.com/watch?v={video_id}")


def _instagram(segments: list[str]) -> ParsedVideo:
    owner = None
    if len(segments) >= 2 and segments[0].lower() in _IG_MEDIA_PATHS:
        code = segments[1]
    elif len(segments) >= 3 and segments[1].lower() in _IG_MEDIA_PATHS and segments[0].lower() not in _IG_RESERVED:
        owner, code = segments[0].lower(), segments[2]  # instagram.com/<username>/reel/<code>/
    else:
        return _invalid("This Instagram link is not a reel/post link (expected instagram.com/reel/<code>/)")
    if not _IG_SHORTCODE.match(code):
        return _invalid("Instagram reel code in the link is not valid")
    if owner is not None and not _IG_USERNAME.match(owner):
        owner = None
    return ParsedVideo("instagram", code, f"https://www.instagram.com/reel/{code}/", owner_username=owner)


def _facebook(segments: list[str], query: str) -> ParsedVideo:
    """facebook.com/reel/<id>, /<page>/videos/[<slug>/]<id>, /watch/?v=<id>, /video.php?v=<id> (query strings dropped)."""
    first = segments[0].lower() if segments else ""
    v_param = (parse_qs(query).get("v") or [""])[0]
    if first == "share":
        return _invalid(f"Facebook share links (facebook.com/share/...) {_FB_NO_ID}")
    if first == "reel" and len(segments) >= 2:
        video_id, kind = segments[1], "reel"
    elif first in ("watch", "video.php"):
        video_id, kind = v_param, "video"
    elif len(segments) >= 3 and segments[1].lower() == "videos":
        video_id, kind = next((s for s in reversed(segments[2:]) if _FB_VIDEO_ID.match(s)), ""), "video"
    elif len(segments) >= 2 and segments[1].lower() in ("posts", "permalink.php") or first in ("permalink.php", "story.php"):
        return _invalid("Facebook post links are not supported - open the video itself and use its link "
                        "(facebook.com/<page>/videos/<id> or facebook.com/reel/<id>)")
    else:
        return _invalid("This Facebook link is not a video or reel link (expected facebook.com/<page>/videos/<id> "
                        "or facebook.com/reel/<id>)")
    if not _FB_VIDEO_ID.match(video_id or ""):
        return _invalid("Facebook video ID in the link is not valid")
    url = f"https://www.facebook.com/reel/{video_id}/" if kind == "reel" else f"https://www.facebook.com/watch/?v={video_id}"
    return ParsedVideo("facebook", video_id, url)


def instagram_shortcode_from_permalink(permalink: str | None) -> str | None:
    parsed = parse_video_link(permalink) if permalink else None
    return parsed.identifier if parsed and parsed.platform == "instagram" else None


def parse_instagram_handle(value: object) -> str | None:
    """'@abc', 'abc', or an instagram.com/abc profile URL -> 'abc'. Anything else -> None."""
    text = str(value or "").strip()
    if not text:
        return None
    if "instagram.com" in text.lower() or "instagr.am" in text.lower():
        url = text if "://" in text else "https://" + text
        segments = [s for s in urlsplit(url).path.split("/") if s]
        if not segments or segments[0].lower() in _IG_RESERVED:
            return None
        text = segments[0]
    text = text.lstrip("@").lower()
    if _IG_USERNAME.match(text) and text.strip(".") == text:
        return text
    return None
