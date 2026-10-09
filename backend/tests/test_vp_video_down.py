"""Video Down: a tracked video that the platform no longer returns (deleted, private or removed)."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.services.llm_client import LLMClient
from app.video_performance.models import VideoStatus, VtVideo
from app.video_performance.platforms import InstagramVideoClient, YouTubeVideoClient
from app.video_performance.sentiment import VideoSentimentAnalyzer
from app.video_performance.tracker import VideoTracker
from tests.fakes import sentiment_batch_response

YT_ID = "downvidAAA1"
IG_CODE = "DownReel01"
IG_OWNER = "downowner"
ONLINE = {"youtube": True, "instagram": True}


def youtube(request: httpx.Request) -> httpx.Response:
    ids = request.url.params["id"].split(",")
    items = [
        {"id": YT_ID, "snippet": {"title": "Down test"}, "statistics": {"viewCount": "500", "likeCount": "5", "commentCount": "1"}}
    ] if ONLINE["youtube"] and YT_ID in ids else []
    return httpx.Response(200, json={"items": items})  # YouTube simply omits deleted/private videos


def instagram(request: httpx.Request) -> httpx.Response:
    media = [{
        "id": "1", "permalink": "https://www.instagram.com/reel/OtherReel1/", "media_type": "VIDEO", "media_product_type": "REELS",
        "timestamp": "2026-09-01T10:00:00+0000", "like_count": 1, "comments_count": 0, "view_count": 10, "caption": "other",
    }]
    if ONLINE["instagram"]:
        media.append({**media[0], "id": "2", "permalink": f"https://www.instagram.com/reel/{IG_CODE}/", "view_count": 900})
    return httpx.Response(200, json={"business_discovery": {"id": "9", "username": IG_OWNER, "media": {"data": media}}})


def llm(request: httpx.Request) -> httpx.Response:
    return sentiment_batch_response(request, "Neutral", 0.5)


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(scope="module")
def setup():
    with TestClient(app) as api:
        settings = get_settings()
        tracker = VideoTracker(
            settings,
            youtube=YouTubeVideoClient(settings, client=client_for(youtube)),
            instagram=InstagramVideoClient(settings, client=client_for(instagram)),
            sentiment=VideoSentimentAnalyzer(settings, LLMClient(settings, client=client_for(llm))),
        )
        app.state.video_tracker = tracker
        with session_scope() as db:
            yt = VtVideo(platform="youtube", video_identifier=YT_ID, video_url=f"https://youtu.be/{YT_ID}", status=VideoStatus.PENDING)
            ig = VtVideo(platform="instagram", video_identifier=IG_CODE, owner_username=IG_OWNER,
                         video_url=f"https://www.instagram.com/reel/{IG_CODE}/", status=VideoStatus.PENDING)
            db.add_all([yt, ig])
            db.flush()
            ids = (yt.id, ig.id)
        yield api, tracker, ids


def statuses(ids) -> list[tuple[str, str | None, int | None]]:
    with session_scope() as db:
        return [(v.status, v.status_reason, v.current_views) for v in (db.get(VtVideo, i) for i in ids)]


def videos_down(api) -> int:
    return api.get("/api/video-performance/dashboard").json()["videos_down"]


def test_unavailable_video_is_marked_down_and_recovers(setup):
    api, tracker, ids = setup
    ONLINE.update(youtube=True, instagram=True)
    asyncio.run(tracker.process_videos(list(ids)))
    assert [s[0] for s in statuses(ids)] == [VideoStatus.TRACKING, VideoStatus.TRACKING]
    before = videos_down(api)

    ONLINE.update(youtube=False, instagram=False)  # both videos deleted / made private
    asyncio.run(tracker.process_videos(list(ids)))
    (yt_status, yt_reason, yt_views), (ig_status, ig_reason, ig_views) = statuses(ids)
    assert yt_status == ig_status == VideoStatus.VIDEO_DOWN
    assert "not found" in yt_reason and "no longer found" in ig_reason
    assert (yt_views, ig_views) == (500, 900)  # last known views are kept, never zeroed or invented
    assert videos_down(api) == before + 2

    # Video Down videos stay in the daily refresh, so a video that comes back resumes tracking.
    assert VideoStatus.VIDEO_DOWN in VideoStatus.DAILY
    ONLINE.update(youtube=True, instagram=True)
    asyncio.run(tracker.process_videos(list(ids)))
    assert [s[0] for s in statuses(ids)] == [VideoStatus.TRACKING, VideoStatus.TRACKING]
    assert videos_down(api) == before


def test_video_down_can_be_retried_and_filtered(setup):
    api, tracker, ids = setup
    ONLINE.update(youtube=False)
    asyncio.run(tracker.process_videos([ids[0]]))
    assert statuses([ids[0]])[0][0] == VideoStatus.VIDEO_DOWN

    listed = api.get("/api/video-performance/videos", params={"status": "Video Down", "page_size": 100}).json()
    assert ids[0] in {v["id"] for v in listed["items"]} and listed["status_counts"]["Video Down"] >= 1

    ONLINE.update(youtube=True)
    assert api.post(f"/api/video-performance/videos/{ids[0]}/retry").status_code == 202


def test_old_reel_outside_scan_window_is_not_called_down():
    """A reel missing from a *partial* scan may just be older - that is Unsupported, not Video Down."""
    settings = get_settings()
    many = [{"id": str(i), "permalink": f"https://www.instagram.com/reel/Reel{i:05d}/", "media_type": "VIDEO",
             "media_product_type": "REELS", "timestamp": "2026-09-01T10:00:00+0000", "like_count": 1,
             "comments_count": 0, "view_count": 1, "caption": "x"} for i in range(50)]

    def full_page(_request):
        return httpx.Response(200, json={"business_discovery": {"id": "9", "username": IG_OWNER, "media": {"data": many}}})

    tracker = VideoTracker(
        settings.model_copy(update={"video_tracking_instagram_scan_pages": 1}),
        youtube=YouTubeVideoClient(settings, client=client_for(youtube)),
        instagram=InstagramVideoClient(settings, client=client_for(full_page)),
        sentiment=VideoSentimentAnalyzer(settings, LLMClient(settings, client=client_for(llm))),
    )
    results: dict = {}
    asyncio.run(tracker._instagram_owner(IG_OWNER, [{"id": 1, "identifier": "OldReel99"}], results))
    assert results[1].code == "unsupported" and "older than" in results[1].message
