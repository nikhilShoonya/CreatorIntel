"""Meta rate limits: a temporary refusal never turns good numbers into "Failed", is not retried within seconds,
stops further calls in the same run, and is re-checked automatically an hour later."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.utils.time import as_utc, utcnow
from app.video_performance import repository as repo
from app.video_performance.models import VideoStatus, VtVideo, VtViewSnapshot
from app.video_performance.platforms import InstagramVideoClient, PlatformError
from app.video_performance.tracker import VideoTracker

RATE_LIMIT = {"error": {"code": 4, "message": "Application request limit reached"}}


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def reels(api):
    """Two Instagram reels of different owners: one already checked (has numbers), one never checked."""
    checked_at = utcnow() - timedelta(minutes=40)
    with session_scope() as db:
        seen = VtVideo(platform="instagram", video_identifier="RateSeen001", video_url="https://www.instagram.com/reel/RateSeen001/",
                       owner_username="rate_owner_a", status=VideoStatus.TRACKING, current_views=19_511, likes=10,
                       comments=1, last_checked_at=checked_at)
        new = VtVideo(platform="instagram", video_identifier="RateNew0001", video_url="https://www.instagram.com/reel/RateNew0001/",
                      owner_username="rate_owner_b", status=VideoStatus.PENDING)
        db.add_all([seen, new])
        db.flush()
        db.add(VtViewSnapshot(video_id=seen.id, captured_at=checked_at, views=19_511, likes=10, comments=1))
        ids = seen.id, new.id
    yield ids, checked_at
    repo.delete_videos(list(ids))


def test_rate_limit_queues_the_video_and_keeps_its_numbers(reels):
    (seen, new), checked_at = reels
    error = PlatformError(PlatformError.RATE_LIMITED, "@rate_owner_a: Instagram API rate limit reached, try again later")
    repo.save_error(seen, error)
    repo.save_error(new, error)
    with session_scope() as db:
        good, fresh = db.get(VtVideo, seen), db.get(VtVideo, new)
        assert good.status == VideoStatus.PENDING and good.current_views == 19_511  # stored numbers kept
        assert abs((as_utc(good.last_checked_at) - checked_at).total_seconds()) < 1  # last SUCCESSFUL check
        assert good.status_reason.startswith("Pending - waiting for Meta's hourly limit (Instagram API rate limit reached")
        assert "showing the numbers from" in good.status_reason
        assert fresh.status == VideoStatus.PENDING  # never had numbers: queued too, not failed
    assert {seen, new} <= set(repo.meta_waiting_ids())  # continued automatically by the Meta queue

    repo.save_error(seen, PlatformError(PlatformError.AUTH, "Meta access token is invalid or expired"))
    with session_scope() as db:
        assert db.get(VtVideo, seen).status == VideoStatus.FAILED  # needs action: shown as Failed


def test_rate_limited_graph_call_is_not_retried_within_seconds():
    calls = []

    def graph(request: httpx.Request):
        calls.append(request.url.path)
        return httpx.Response(403, json=RATE_LIMIT)

    settings = get_settings().model_copy(update={"http_max_retries": 3})
    client = InstagramVideoClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    with pytest.raises(PlatformError) as err:
        asyncio.run(client.profile_media("rate_owner_a"))
    assert err.value.code == PlatformError.RATE_LIMITED and len(calls) == 1


class _RateLimitedInstagram:
    def __init__(self):
        self.owners: list[str] = []

    async def profile_media(self, owner: str, pages: int = 1, **_kwargs):
        self.owners.append(owner)
        raise PlatformError(PlatformError.RATE_LIMITED, "Instagram API rate limit reached, try again later")


def test_run_stops_calling_after_a_rate_limit_and_queues_the_rest(reels):
    (seen, new), _ = reels
    instagram = _RateLimitedInstagram()
    tracker = VideoTracker(get_settings(), instagram=instagram)

    async def run():
        await tracker.process_videos([seen, new])
        await tracker.shutdown()

    asyncio.run(run())
    assert len(instagram.owners) == 1  # the second owner was not called after Meta refused
    assert {seen, new} <= set(repo.meta_waiting_ids())  # both wait in the Meta queue (survives restarts)
    with session_scope() as db:
        assert db.get(VtVideo, seen).status == VideoStatus.PENDING and db.get(VtVideo, seen).current_views == 19_511


def test_repair_moves_old_rate_limit_failures_into_the_meta_queue(reels):
    (seen, new), checked_at = reels
    with session_scope() as db:  # as older versions left them: Failed, "last checked" = the failed attempt
        for vid in (seen, new):
            video = db.get(VtVideo, vid)
            video.status = VideoStatus.FAILED
            video.status_reason = "@rate_owner_a: Instagram API rate limit reached, try again later"
            video.last_checked_at = utcnow()

    waiting = repo.repair_temporary_failures()
    assert seen in waiting and new in waiting
    with session_scope() as db:
        good, fresh = db.get(VtVideo, seen), db.get(VtVideo, new)
        assert good.status == fresh.status == VideoStatus.PENDING
        assert abs((as_utc(good.last_checked_at) - checked_at).total_seconds()) < 1  # last successful check
        assert good.current_views == 19_511
        assert good.status_reason.startswith("Pending - waiting for Meta's hourly limit (Instagram API rate limit reached")
    assert seen in repo.repair_temporary_failures()  # idempotent: still waiting, nothing changed twice
