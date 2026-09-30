from datetime import datetime, timedelta, timezone

import pytest

from app.agents.metrics_agent import MetricsProcessor
from app.schemas.platform import ChannelProfile, ContentItem

NOW = datetime(2024, 9, 30, tzinfo=timezone.utc)


def _item(i, views=None, likes=None, comments=None, is_video=True, url=True):
    return ContentItem(
        id=str(i), url=f"https://www.youtube.com/watch?v={i}" if url else None,
        published_at=NOW - timedelta(days=i), title=f"Video {i}",
        is_video=is_video, views=views, likes=likes, comments=comments,
    )


def _profile(platform, items, audience=1000):
    return ChannelProfile(platform=platform, platform_id="x", display_name="X", bio="", audience_count=audience,
                          account_access="public_channel", items=items, source="test")


def test_youtube_average_median_top_and_engagement():
    # 12 videos; only the latest 10 count toward averages
    items = [_item(i, views=(i + 1) * 100, likes=(i + 1) * 5, comments=(i + 1)) for i in range(12)]
    items.append(_item(99, views=None))  # hidden views ignored
    result = MetricsProcessor(sample_size=10).compute(_profile("youtube", items))
    assert result.average_views_sample_count == 10
    assert result.average_views == pytest.approx(sum((i + 1) * 100 for i in range(10)) / 10)
    assert result.median_views == pytest.approx(550)
    assert result.top_video.views == 1200  # highest among all recent videos
    assert result.engagement_rate_basis == "views"
    assert result.engagement_rate == pytest.approx(6.0)  # (5+1)/100 per video
    assert result.provenance["average_views"] == "calculated_from_10_videos"


def test_no_views_means_null_not_estimates():
    items = [_item(i, views=None, likes=50, comments=5) for i in range(5)]
    result = MetricsProcessor().compute(_profile("youtube", items))
    assert result.average_views is None
    assert result.average_views_sample_count == 0
    assert result.top_video is None
    assert result.engagement_rate is None


def test_instagram_falls_back_to_follower_basis_without_views():
    items = [_item(i, views=None, likes=90, comments=10) for i in range(4)]
    result = MetricsProcessor().compute(_profile("instagram", items, audience=10_000))
    assert result.average_views is None
    assert result.engagement_rate_basis == "followers"
    assert result.engagement_rate == pytest.approx(1.0)


def test_instagram_zero_views_with_likes_is_not_a_valid_observation():
    items = [_item(i, views=0, likes=100, comments=1) for i in range(3)]
    result = MetricsProcessor().compute(_profile("instagram", items, audience=5000))
    assert result.average_views is None
    assert result.engagement_rate_basis == "followers"
