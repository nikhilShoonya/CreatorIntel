"""Metrics calculation from real API observations only.

- average_views: mean of the latest N videos/reels that have a view count
- top video: highest view count among the recent videos/reels fetched
- engagement: mean per-item ((likes + comments) / views) * 100, or - for
  Instagram when no view counts exist - ((likes + comments) / followers) * 100.
  The two bases are never mixed.
"""

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.schemas.platform import ChannelProfile, ContentItem

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass
class TopVideo:
    title: str | None
    url: str
    views: int


@dataclass
class MetricsResult:
    audience_count: int | None
    average_views: float | None = None
    average_views_sample_count: int = 0
    median_views: float | None = None
    top_video: TopVideo | None = None
    engagement_rate: float | None = None
    engagement_rate_basis: str | None = None  # "views" | "followers"
    engagement_sample_count: int = 0
    provenance: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _newest_first(items: list[ContentItem]) -> list[ContentItem]:
    return sorted(items, key=lambda i: i.published_at or _EPOCH, reverse=True)


def _has_valid_views(item: ContentItem) -> bool:
    if item.views is None or item.views < 0:
        return False
    # A zero view count alongside likes means the platform did not report views.
    if item.views == 0 and (item.likes or 0) > 0:
        return False
    return True


class MetricsProcessor:
    def __init__(self, sample_size: int = 10):
        self.sample_size = sample_size

    def compute(self, profile: ChannelProfile) -> MetricsResult:
        unit = "videos" if profile.platform == "youtube" else "reels"
        result = MetricsResult(audience_count=profile.audience_count)
        if profile.audience_count is not None:
            result.provenance["audience_count"] = profile.source

        videos = _newest_first([i for i in profile.items if i.is_video and not i.is_live_or_upcoming])
        with_views = [v for v in videos if _has_valid_views(v)]

        # Average / median views over the latest N valid observations
        sample = with_views[: self.sample_size]
        if sample:
            views = [v.views for v in sample if v.views is not None]
            result.average_views = round(sum(views) / len(views), 2)
            result.median_views = float(statistics.median(views))
            result.average_views_sample_count = len(views)
            result.provenance["average_views"] = f"calculated_from_{len(views)}_{unit}"
        else:
            result.notes.append(f"Average views unavailable: no {unit} with view counts")

        # Top performing video among recent items that have real URLs and views
        candidates = [v for v in with_views if v.url and v.views]
        if candidates:
            best = max(candidates, key=lambda v: v.views or 0)
            result.top_video = TopVideo(title=best.title, url=best.url or "", views=best.views or 0)
            result.provenance["top_video"] = f"highest_views_of_latest_{len(with_views)}_{unit}"

        self._engagement(profile, with_views, result, unit)
        return result

    def _engagement(self, profile: ChannelProfile, with_views: list[ContentItem], result: MetricsResult, unit: str) -> None:
        by_views = [
            v for v in with_views
            if v.views and v.views > 0 and v.likes is not None and v.comments is not None
        ][: self.sample_size]
        if by_views:
            rates = [((v.likes or 0) + (v.comments or 0)) / (v.views or 1) * 100 for v in by_views]
            result.engagement_rate = round(sum(rates) / len(rates), 2)
            result.engagement_rate_basis = "views"
            result.engagement_sample_count = len(rates)
            result.provenance["engagement_rate"] = f"(likes+comments)/views_over_{len(rates)}_{unit}"
            return

        # Instagram fallback only: follower-based engagement, clearly labelled.
        followers = profile.audience_count
        if profile.platform == "instagram" and followers and followers > 0:
            posts = [
                p for p in _newest_first(profile.items) if p.likes is not None and p.comments is not None
            ][: self.sample_size]
            if posts:
                rates = [((p.likes or 0) + (p.comments or 0)) / followers * 100 for p in posts]
                result.engagement_rate = round(sum(rates) / len(rates), 2)
                result.engagement_rate_basis = "followers"
                result.engagement_sample_count = len(rates)
                result.provenance["engagement_rate"] = f"(likes+comments)/followers_over_{len(rates)}_posts"
                return

        result.notes.append("Engagement rate unavailable: insufficient engagement data")
