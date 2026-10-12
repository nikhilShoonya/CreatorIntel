"""A lower view count returned by the API shortly after a higher one is an outdated cached count, not a loss."""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.main import app
from app.models.db import session_scope
from app.utils.time import utcnow
from app.video_performance import queries
from app.video_performance import repository as repo
from app.video_performance.models import VideoStatus, VtVideo, VtViewSnapshot
from app.video_performance.platforms import VideoMetrics


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def video(api):  # the app creates the tables on startup
    with session_scope() as db:
        row = VtVideo(platform="youtube", video_identifier="staleCount1", video_url="https://youtu.be/staleCount1",
                      status=VideoStatus.TRACKING)
        db.add(row)
        db.flush()
        video_id = row.id
    yield video_id
    repo.delete_videos([video_id])


def _snapshots(video_id: int) -> list[int]:
    with session_scope() as db:
        return list(db.scalars(select(VtViewSnapshot.views).where(VtViewSnapshot.video_id == video_id)
                               .order_by(VtViewSnapshot.captured_at)))


def _age_snapshots(video_id: int, hours: int) -> None:
    with session_scope() as db:
        for snap in db.scalars(select(VtViewSnapshot).where(VtViewSnapshot.video_id == video_id)):
            snap.captured_at = snap.captured_at - timedelta(hours=hours)


def test_outdated_lower_count_is_not_stored(video):
    repo.save_metrics(video, VideoMetrics("staleCount1", views=84_293, likes=2_397, comments=187))
    repo.save_metrics(video, VideoMetrics("staleCount1", views=84_210, likes=2_397, comments=187))  # 3 min later
    assert _snapshots(video) == [84_293]
    with session_scope() as db:
        row = db.get(VtVideo, video)
        assert row.current_views == 84_293 and row.last_checked_at is not None

    repo.save_metrics(video, VideoMetrics("staleCount1", views=84_294, likes=2_398, comments=187))  # back up: kept
    assert _snapshots(video) == [84_293, 84_294]


def test_real_decrease_shows_after_the_window(video):
    repo.save_metrics(video, VideoMetrics("staleCount1", views=1_000, likes=10, comments=1))
    _age_snapshots(video, 13)  # the higher reading is older than the 12-hour window
    repo.save_metrics(video, VideoMetrics("staleCount1", views=950, likes=10, comments=1))
    assert _snapshots(video) == [1_000, 950]
    with session_scope() as db:
        assert db.get(VtVideo, video).current_views == 950


def test_previously_saved_outdated_count_is_ignored_everywhere(api, video):
    now = utcnow()
    with session_scope() as db:  # stored before the filter existed: 83,761 (3 days ago) -> 84,293 -> 84,210 (3 min later)
        db.add_all([
            VtViewSnapshot(video_id=video, captured_at=now - timedelta(days=3), views=83_761, likes=2_393, comments=187),
            VtViewSnapshot(video_id=video, captured_at=now - timedelta(minutes=6), views=84_293, likes=2_397, comments=187),
            VtViewSnapshot(video_id=video, captured_at=now - timedelta(minutes=3), views=84_210, likes=2_397, comments=187),
        ])
        db.execute(update(VtVideo).where(VtVideo.id == video).values(current_views=84_210))

    history = api.get(f"/api/video-performance/history/{video}").json()["snapshots"]
    assert [(s["views"], s["views_change"], s["outdated"]) for s in history] == [
        (84_210, None, True), (84_293, 532, False), (83_761, None, False),
    ]

    # the next check (still within the window) restores the higher real reading as the current count
    repo.save_metrics(video, VideoMetrics("staleCount1", views=84_250, likes=2_397, comments=187))
    with session_scope() as db:
        row = db.get(VtVideo, video)
        assert row.current_views == 84_293 and row.previous_views == 83_761 and row.views_gained == 532

    rows = [(video, now - timedelta(minutes=6), 84_293, 1, 1), (video, now - timedelta(minutes=3), 84_210, 1, 1)]
    assert [r[2] for r in queries._without_outdated_counts(rows)] == [84_293]


def test_repair_restores_the_real_count_for_every_affected_video(video):
    now = utcnow()
    with session_scope() as db:
        db.add_all([
            VtViewSnapshot(video_id=video, captured_at=now - timedelta(days=3), views=83_761, likes=2_393, comments=187),
            VtViewSnapshot(video_id=video, captured_at=now - timedelta(minutes=6), views=84_293, likes=2_397, comments=187),
            VtViewSnapshot(video_id=video, captured_at=now - timedelta(minutes=3), views=84_210, likes=2_397, comments=187),
        ])
        db.execute(update(VtVideo).where(VtVideo.id == video).values(current_views=84_210, views_gained=449))

    assert repo.repair_outdated_counts() >= 1
    with session_scope() as db:
        row = db.get(VtVideo, video)
        assert (row.current_views, row.previous_views, row.views_gained) == (84_293, 83_761, 532)
    assert _snapshots(video) == [83_761, 84_293, 84_210]  # saved readings are not changed
    with session_scope() as db:
        before = db.get(VtVideo, video).updated_at
    repo.repair_outdated_counts()  # nothing left to repair for this video
    with session_scope() as db:
        assert db.get(VtVideo, video).updated_at == before


def test_youtube_views_are_read_several_times_and_the_highest_kept(monkeypatch):
    import asyncio

    import httpx

    from app.config.settings import get_settings
    from app.video_performance import platforms

    answers = iter([84_210, 84_288, 84_210])  # YouTube's servers answer with different copies

    def handler(request: httpx.Request):
        views = next(answers)
        item = {"id": "staleCount1", "statistics": {"viewCount": str(views), "likeCount": "2397", "commentCount": "187"}}
        if "snippet" in request.url.params["part"]:
            item["snippet"] = {"title": "End of Nifty50", "channelId": "UCx", "channelTitle": "Super Trader Lakshya"}
        return httpx.Response(200, json={"items": [item]})

    monkeypatch.setattr(platforms, "EXTRA_READ_DELAY_SECONDS", 0)
    settings = get_settings().model_copy(update={"youtube_view_reads": 3})
    client = platforms.YouTubeVideoClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    metrics = asyncio.run(client.videos(["staleCount1"]))["staleCount1"]
    assert metrics.views == 84_288 and metrics.title == "End of Nifty50" and metrics.likes == 2397
