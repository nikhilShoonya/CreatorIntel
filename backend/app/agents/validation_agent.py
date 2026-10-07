"""Result validation before anything is saved.

Invalid values are replaced by None (= Unavailable) and reported as issues;
nothing is ever "repaired" with invented data.
"""

import math
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from app.schemas.taxonomy import GENRE_TAXONOMY, LANGUAGES, SENTIMENTS, UNKNOWN

_VIDEO_HOSTS = {
    "youtube": {"www.youtube.com", "youtube.com"},
    "instagram": {"www.instagram.com", "instagram.com"},
}
_CHANNEL_HOSTS = _VIDEO_HOSTS


@dataclass
class ValidationOutcome:
    values: dict
    issues: list[str] = field(default_factory=list)


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _https_on(url: object, hosts: set[str]) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" and (parts.hostname or "").lower() in hosts


class ResultValidator:
    def validate(self, platform: str, values: dict) -> ValidationOutcome:
        v = dict(values)
        issues: list[str] = []

        def drop(keys: tuple[str, ...], message: str) -> None:
            for key in keys:
                v[key] = None
            issues.append(message)

        if platform not in _CHANNEL_HOSTS:
            raise ValueError(f"Cannot validate unsupported platform '{platform}'")
        if not _https_on(v.get("channel_url"), _CHANNEL_HOSTS[platform]):
            issues.append("Channel URL does not match the platform")
        if not v.get("platform_id"):
            issues.append("Creator identity could not be confirmed by the platform API")
        avatar = v.get("profile_picture_url")
        if avatar is not None and not (isinstance(avatar, str) and urlsplit(avatar).scheme == "https"):
            v["profile_picture_url"] = None  # cosmetic only, so not reported as an issue

        for key in ("followers_count", "subscriber_count"):
            if v.get(key) is not None and not _is_count(v[key]):
                drop((key,), f"{key} was not a valid count")

        if v.get("average_views") is not None:
            if not _is_number(v["average_views"]) or v["average_views"] < 0 or not v.get("average_views_sample_count"):
                drop(("average_views", "median_views"), "Average views failed validation")
                v["average_views_sample_count"] = 0

        if v.get("top_video_url") is not None or v.get("top_video_views") is not None:
            if not _https_on(v.get("top_video_url"), _VIDEO_HOSTS[platform]) or not _is_count(v.get("top_video_views")):
                drop(("top_video_title", "top_video_url", "top_video_views"), "Top video failed validation")

        rate, basis = v.get("engagement_rate"), v.get("engagement_rate_basis")
        if rate is not None:
            valid = _is_number(rate) and rate >= 0 and basis in ("views", "followers")
            if valid and basis == "views" and rate > 100:
                valid = False
            if not valid:
                drop(("engagement_rate", "engagement_rate_basis"), "Engagement rate failed validation")
                v["engagement_sample_count"] = 0

        genre = v.get("genre")
        if genre is not None and genre not in GENRE_TAXONOMY:
            drop(("genre", "sub_genre"), "Genre outside allowed taxonomy")
        elif genre is not None and v.get("sub_genre") is not None and v["sub_genre"] not in GENRE_TAXONOMY[genre]:
            drop(("sub_genre",), "Sub-genre does not belong to genre")

        if v.get("language") is not None and v["language"] not in (*LANGUAGES, UNKNOWN):
            drop(("language", "secondary_language"), "Language outside allowed list")
        if v.get("secondary_language") is not None and v["secondary_language"] not in LANGUAGES:
            drop(("secondary_language",), "Secondary language outside allowed list")
        if v.get("sentiment") is not None and v["sentiment"] not in (*SENTIMENTS, UNKNOWN):
            drop(("sentiment", "sentiment_score"), "Sentiment outside allowed values")

        for key in ("genre_confidence", "language_confidence", "sentiment_confidence"):
            if v.get(key) is not None and not (_is_number(v[key]) and 0 <= v[key] <= 1):
                drop((key,), f"{key} outside 0-1")
        if v.get("sentiment_score") is not None and not (_is_number(v["sentiment_score"]) and -1 <= v["sentiment_score"] <= 1):
            drop(("sentiment_score",), "Sentiment score outside -1..1")

        return ValidationOutcome(values=v, issues=issues)
