"""Video Performance dashboard trend: daily totals built only from recorded view checks."""

from datetime import datetime, time, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.db import session_scope
from app.video_performance import queries
from app.video_performance.models import VtVideo, VtViewSnapshot
from app.video_performance.schemas import youtube_thumbnail
from app.video_performance.timeutil import tracking_tz


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


def _at(days_ago: int) -> datetime:
    tz = tracking_tz()
    day = datetime.now(timezone.utc).astimezone(tz).date() - timedelta(days=days_ago)
    return datetime.combine(day, time(12), tzinfo=tz).astimezone(timezone.utc)


def test_trend_uses_only_recorded_checks(api):
    with session_scope() as db:
        older = VtVideo(platform="youtube", video_identifier="trendvidAAA", video_url="https://youtu.be/trendvidAAA",
                        status="Tracking", created_at=_at(10))
        newer = VtVideo(platform="instagram", video_identifier="TrendReelB", video_url="https://www.instagram.com/reel/TrendReelB/",
                        status="Tracking", created_at=_at(1))
        db.add_all([older, newer])
        db.flush()
        db.add_all([
            VtViewSnapshot(video_id=older.id, captured_at=_at(4), views=100, likes=5, comments=5),
            VtViewSnapshot(video_id=older.id, captured_at=_at(2), views=150, likes=10, comments=5),
            VtViewSnapshot(video_id=older.id, captured_at=_at(0), views=180, likes=10, comments=8),
            VtViewSnapshot(video_id=newer.id, captured_at=_at(1), views=1000, likes=40, comments=10),
            VtViewSnapshot(video_id=newer.id, captured_at=_at(0), views=1100, likes=50, comments=10),
        ])
        ids = older.id, newer.id

    result = queries.trend(3)
    assert [p.day for p in result.points] == [_at(d).astimezone(tracking_tz()).date() for d in (2, 1, 0)]
    assert result.video_gains[ids[0]] == [50, 0, 30]  # not checked yesterday -> no change that day
    assert result.video_gains[ids[1]] == [None, None, 100]  # its first check is not a gain
    # Views carry forward: yesterday still counts the older video at 150 plus the new one at 1000.
    assert result.points[1].total_views - result.points[0].total_views >= 1000
    assert result.points[2].videos >= result.points[1].videos >= result.points[0].videos

    body = api.get("/api/video-performance/dashboard", params={"days": 14}).json()
    assert body["range_days"] == 14 and len(body["trend"]) == 14
    assert set(map(int, body["video_trends"])) <= {v["id"] for v in body["top_performing"] + body["highest_engagement"]}
    assert all(len(series) == 14 for series in body["video_trends"].values())
    assert api.get("/api/video-performance/dashboard", params={"days": 1}).status_code == 422


def test_thumbnails_only_for_real_youtube_ids():
    assert youtube_thumbnail("youtube", "dQw4w9WgXcQ") == "https://i.ytimg.com/vi/dQw4w9WgXcQ/mqdefault.jpg"
    assert youtube_thumbnail("youtube", "bad/../id") is None
    assert youtube_thumbnail("instagram", "Cabc12345") is None
