"""Fixes from the code audit: clear Instagram token errors, Reel views, Facebook call counts, Video Down recheck cap."""

import asyncio
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.instagram_agent import InstagramCollector
from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.services import api_usage
from app.services.http_client import HttpRequestError
from app.utils.time import utcnow
from app.video_performance import repository as repo
from app.video_performance.models import VideoStatus, VtVideo, VtViewSnapshot
from app.video_performance.platforms import FacebookVideoClient, InstagramVideoClient, PlatformError

VIEWER_ERROR = {"error": {"code": 100, "error_subcode": 33,
                          "message": "Unsupported get request. Object with ID '17841418672730174' does not exist"}}


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


def test_instagram_viewer_not_reachable_is_one_clear_token_error():
    error = InstagramVideoClient._translate(HttpRequestError("Instagram Graph API returned HTTP 400", status_code=400,
                                                             payload=VIEWER_ERROR))
    assert error.code == PlatformError.AUTH and "cannot access your Instagram Business account" in error.message

    collector = InstagramCollector(get_settings())
    collected = collector._translate_error(HttpRequestError("x", status_code=400, payload=VIEWER_ERROR))
    assert "cannot access your Instagram Business account" in collected.message


def test_facebook_reel_views_fall_back_to_reel_metrics():
    def graph(request: httpx.Request):
        path = request.url.path
        if path.endswith("/1361142767079677") and request.url.params.get("fields") == "access_token":
            return httpx.Response(200, json={"access_token": "EAApage"})
        if path.endswith("/video_insights"):
            metric = request.url.params["metric"]
            if metric == "total_video_views":  # Reels do not report this metric
                return httpx.Response(200, json={"data": []})
            return httpx.Response(200, json={"data": [{"name": metric, "values": [{"value": 4321}]}]})
        return httpx.Response(200, json={"id": "555555555555555", "from": {"id": "1361142767079677", "name": "CreatorIntel"},
                                         "likes": {"summary": {"total_count": 3}}, "comments": {"summary": {"total_count": 1}}})

    settings = get_settings().model_copy(update={"meta_access_token": "EAAsystem", "meta_facebook_page_id": "1361142767079677"})
    metrics = asyncio.run(FacebookVideoClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(graph)))
                          .video("555555555555555"))
    assert metrics.views == 4321 and metrics.notes == []


def test_facebook_calls_are_counted_with_instagram(api):
    api_usage.flush()
    before = api_usage.instagram_usage().facebook_calls_today
    api_usage.record_facebook_call()
    api_usage.record_facebook_call()
    assert api_usage.instagram_usage().facebook_calls_today == before + 2
    assert api.get("/api/config/api-usage").json()["instagram"]["facebook_calls_today"] == before + 2


def test_long_down_videos_stop_daily_checks_and_status_counts(api):
    with session_scope() as db:
        old_down = VtVideo(platform="youtube", video_identifier="downOld0001", video_url="https://youtu.be/downOld0001",
                           status=VideoStatus.VIDEO_DOWN, status_reason="YouTube video not found",
                           tracking_started_at=utcnow() - timedelta(days=30))
        recent_down = VtVideo(platform="youtube", video_identifier="downNew0001", video_url="https://youtu.be/downNew0001",
                              status=VideoStatus.VIDEO_DOWN, status_reason="YouTube video not found",
                              tracking_started_at=utcnow() - timedelta(days=30))
        db.add_all([old_down, recent_down])
        db.flush()
        db.add(VtViewSnapshot(video_id=recent_down.id, captured_at=utcnow() - timedelta(days=3), views=10))
        ids = old_down.id, recent_down.id

    due = repo.videos_due_for_refresh(0, 14)
    assert ids[0] not in due and ids[1] in due  # seen 3 days ago -> still checked; never seen for 30 days -> stopped
    with session_scope() as db:
        reason = db.get(VtVideo, ids[0]).status_reason
    assert reason.endswith("no longer checked daily - use Retry to check again") and reason.count("no longer") == 1
    repo.videos_due_for_refresh(0, 14)  # the note is not added twice
    with session_scope() as db:
        assert db.get(VtVideo, ids[0]).status_reason == reason
    assert ids[0] in repo.videos_due_for_refresh(0, 0)  # 0 = always re-check

    assert repo.status_counts(list(ids)) == {VideoStatus.VIDEO_DOWN: 2}
    with session_scope() as db:
        for vid in ids:
            db.delete(db.get(VtVideo, vid))
