"""Facebook support in Video Performance (videos/reels on the configured Page). All Meta calls are mocked."""

import asyncio
import io
import logging
import time
from datetime import timedelta

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.services.llm_client import LLMClient
from app.video_performance.models import VideoStatus, VtVideo, VtViewSnapshot
from app.video_performance.platforms import FacebookVideoClient, InstagramVideoClient, PlatformError, YouTubeVideoClient
from app.video_performance.sentiment import VideoSentimentAnalyzer
from app.video_performance.share_links import ShareLinkResolver
from app.video_performance.tracker import VideoTracker
from app.video_performance.urls import parse_video_link

PAGE_ID = "1361142767079677"
SYSTEM_TOKEN = "EAAsystem-user-token-SECRET"
PAGE_TOKEN = "EAApage-token-SECRET"
OWN, OWN_REEL, OTHER, GONE = "111111111111111", "222222222222222", "333333333333333", "444444444444444"
FOREIGN = "10153231379946729"  # a public video on another Page: Meta answers with a permission error
STATE = {"views": {OWN: 2000, OWN_REEL: 900}, "page_token_calls": 0, "insights": True, "rate_limit": False,
         "deleted": set(), "revoke_page_token_once": False}


def graph(request: httpx.Request) -> httpx.Response:
    path = request.url.path.split("/", 2)[-1]  # strip /v21.0/
    auth = request.headers.get("authorization", "")
    fields = request.url.params.get("fields", "")
    if STATE["rate_limit"]:
        return httpx.Response(400, json={"error": {"code": 4, "message": "Application request limit reached"}})
    if path == PAGE_ID and fields == "access_token":
        if auth != f"Bearer {SYSTEM_TOKEN}":  # the System User token is used only to derive the Page token
            return httpx.Response(400, json={"error": {"code": 190, "message": "Invalid OAuth access token"}})
        STATE["page_token_calls"] += 1
        return httpx.Response(200, json={"access_token": PAGE_TOKEN, "id": PAGE_ID})
    if auth != f"Bearer {PAGE_TOKEN}":
        return httpx.Response(400, json={"error": {"code": 190, "message": "Invalid OAuth access token"}})
    if STATE["revoke_page_token_once"]:
        STATE["revoke_page_token_once"] = False
        return httpx.Response(400, json={"error": {"code": 190, "message": "Session has expired"}})
    video_id, _, edge = path.partition("/")
    if video_id == FOREIGN:
        return httpx.Response(400, json={"error": {"code": 10, "message": "(#10) Application does not have permission for this action"}})
    if video_id in STATE["deleted"] or video_id not in (OWN, OWN_REEL, OTHER):
        return httpx.Response(400, json={"error": {"code": 100, "error_subcode": 33, "message": "Unsupported get request."}})
    if edge == "video_insights":
        if not STATE["insights"]:
            return httpx.Response(400, json={"error": {"code": 10, "message": "(#10) Requires read_insights"}})
        return httpx.Response(200, json={"data": [{"name": "total_video_views", "values": [{"value": STATE["views"][video_id]}]}]})
    owner = {"id": "999", "name": "Someone Else"} if video_id == OTHER else {"id": PAGE_ID, "name": "CreatorIntel"}
    return httpx.Response(200, json={
        "id": video_id, "title": "Bank Nifty levels" if video_id == OWN else None, "description": "Weekly market outlook",
        "permalink_url": f"/reel/{video_id}/", "created_time": "2026-10-01T10:00:00+0000", "length": 42.5, "from": owner,
        "likes": {"data": [], "summary": {"total_count": 120}}, "comments": {"data": [], "summary": {"total_count": 14}},
    })


def youtube(request: httpx.Request) -> httpx.Response:
    items = [{"id": v, "snippet": {"title": "YT"}, "statistics": {"viewCount": "500", "likeCount": "5", "commentCount": "1"}}
             for v in request.url.params["id"].split(",")]
    return httpx.Response(200, json={"items": items})


def instagram(request: httpx.Request) -> httpx.Response:
    media = [{"id": "1", "permalink": "https://www.instagram.com/reel/FbMixReel1/", "media_type": "VIDEO",
              "media_product_type": "REELS", "timestamp": "2026-09-01T10:00:00+0000", "like_count": 3,
              "comments_count": 1, "view_count": 70, "caption": "mixed upload reel"}]
    return httpx.Response(200, json={"business_discovery": {"id": "9", "username": "mixowner", "media": {"data": media}}})


def llm(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": '{"sentiment": "Neutral", "confidence": 0.7}'}}]})


def share_redirects(request: httpx.Request) -> httpx.Response:
    """Offline stand-in for Facebook's share-link redirect: every share link points at OWN_REEL."""
    return httpx.Response(302, headers={"location": f"https://www.facebook.com/reel/{OWN_REEL}/?fs=e"})


async def public_ip(_host: str) -> list[str]:
    return ["157.240.1.35"]


def mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def fb_settings(**overrides):
    return get_settings().model_copy(update={"meta_access_token": SYSTEM_TOKEN, "meta_facebook_page_id": PAGE_ID, **overrides})


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        settings = fb_settings()
        app.state.video_tracker = VideoTracker(
            settings,
            youtube=YouTubeVideoClient(settings, client=mock(youtube)),
            instagram=InstagramVideoClient(settings, client=mock(instagram)),
            facebook=FacebookVideoClient(settings, client=mock(graph)),
            sentiment=VideoSentimentAnalyzer(settings, LLMClient(settings, client=mock(llm))),
        )
        app.state.share_resolver = ShareLinkResolver(client=mock(share_redirects), resolve_host=public_ip)
        yield client
        app.state.share_resolver = None


def videos_by_id(api) -> dict[str, dict]:
    items = api.get("/api/video-performance/videos", params={"page_size": 100}).json()["items"]
    return {v["video_identifier"]: v for v in items}


def wait_idle(api, timeout=10) -> dict[str, dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        videos = videos_by_id(api)
        if all(v["status"] not in ("Pending", "Processing") for v in videos.values()):
            return videos
        time.sleep(0.05)
    raise AssertionError("videos still processing")


# ------------------------------------------------------------------ URL parsing
@pytest.mark.parametrize("link, identifier, url", [
    (f"https://www.facebook.com/reel/{OWN}/?mibextid=abc", OWN, f"https://www.facebook.com/reel/{OWN}/"),
    (f"https://www.facebook.com/CreatorIntel/videos/{OWN}/", OWN, f"https://www.facebook.com/watch/?v={OWN}"),
    (f"https://m.facebook.com/CreatorIntel/videos/market-update/{OWN}/?ref=share", OWN, f"https://www.facebook.com/watch/?v={OWN}"),
    (f"https://www.facebook.com/watch/?v={OWN}&t=10", OWN, f"https://www.facebook.com/watch/?v={OWN}"),
    (f"https://web.facebook.com/video.php?v={OWN}", OWN, f"https://www.facebook.com/watch/?v={OWN}"),
    (f"facebook.com/reel/{OWN}", OWN, f"https://www.facebook.com/reel/{OWN}/"),
])
def test_facebook_video_links(link, identifier, url):
    parsed = parse_video_link(link)
    assert (parsed.platform, parsed.identifier, parsed.url) == ("facebook", identifier, url)


@pytest.mark.parametrize("link, reason", [
    ("https://www.facebook.com/share/v/1BgbFi3ptu/?mibextid=wwXIfr", "share links"),
    ("https://fb.watch/abcDEF123/", "fb.watch"),
    ("https://www.facebook.com/CreatorIntel/posts/pfbid02abc", "post links"),
    ("https://www.facebook.com/CreatorIntel/", "not a video"),
    ("https://www.facebook.com/reel/not-a-number/", "not valid"),
])
def test_unsupported_facebook_links_explain_why(link, reason):
    parsed = parse_video_link(link)
    assert not parsed.ok and reason in parsed.error


def test_youtube_and_instagram_parsing_unchanged():
    assert parse_video_link("https://youtu.be/aaaaaaaaaaa?si=x").identifier == "aaaaaaaaaaa"
    reel = parse_video_link("https://www.instagram.com/reels/DcD3zvtzLzO/?utm_source=ig")
    assert (reel.platform, reel.identifier) == ("instagram", "DcD3zvtzLzO")


# ------------------------------------------------------------------ client
def test_page_token_is_derived_once_and_metrics_are_read():
    STATE.update(page_token_calls=0, insights=True)
    client = FacebookVideoClient(fb_settings(), client=mock(graph))

    async def run():
        return await asyncio.gather(client.video(OWN), client.video(OWN_REEL))

    own, reel = asyncio.run(run())
    assert STATE["page_token_calls"] == 1  # derived once, cached in memory
    assert (own.views, own.likes, own.comments, own.title, own.channel_title) == (2000, 120, 14, "Bank Nifty levels", "CreatorIntel")
    assert reel.views == 900 and reel.title is None and reel.caption == "Weekly market outlook"
    assert own.published_at is not None and own.notes == []


def test_missing_view_insights_is_null_not_invented():
    STATE.update(insights=False)
    try:
        metrics = asyncio.run(FacebookVideoClient(fb_settings(), client=mock(graph)).video(OWN))
    finally:
        STATE.update(insights=True)
    assert metrics.views is None and metrics.likes == 120
    assert any("view count" in note for note in metrics.notes)


@pytest.mark.parametrize("video_id, code, text", [
    (GONE, PlatformError.NOT_FOUND, "not found"),
    (OTHER, PlatformError.UNSUPPORTED, "another Facebook Page"),
    (FOREIGN, PlatformError.UNSUPPORTED, "not on your Facebook Page"),
])
def test_not_found_and_other_page(video_id, code, text):
    with pytest.raises(PlatformError) as caught:
        asyncio.run(FacebookVideoClient(fb_settings(), client=mock(graph)).video(video_id))
    assert caught.value.code == code and text in caught.value.message


def test_config_token_and_rate_limit_errors():
    with pytest.raises(PlatformError) as caught:
        asyncio.run(FacebookVideoClient(fb_settings(meta_facebook_page_id=""), client=mock(graph)).video(OWN))
    assert caught.value.code == PlatformError.CONFIG and "META_FACEBOOK_PAGE_ID" in caught.value.message

    with pytest.raises(PlatformError) as caught:  # System User cannot derive a Page token
        asyncio.run(FacebookVideoClient(fb_settings(meta_access_token="EAAwrong"), client=mock(graph)).video(OWN))
    assert caught.value.code == PlatformError.AUTH and "System User" in caught.value.message

    STATE.update(rate_limit=True)
    try:
        with pytest.raises(PlatformError) as caught:
            asyncio.run(FacebookVideoClient(fb_settings(http_max_retries=0), client=mock(graph)).video(OWN))
    finally:
        STATE.update(rate_limit=False)
    assert caught.value.code == PlatformError.RATE_LIMITED


def test_revoked_page_token_is_re_derived_once():
    STATE.update(page_token_calls=0)
    client = FacebookVideoClient(fb_settings(), client=mock(graph))
    asyncio.run(client.video(OWN))
    STATE.update(revoke_page_token_once=True)
    asyncio.run(client.video(OWN))
    assert STATE["page_token_calls"] == 2


# ------------------------------------------------------------------ end-to-end in Video Performance
def test_mixed_upload_daily_refresh_history_retry_pause(api, caplog):
    caplog.set_level(logging.DEBUG)
    STATE.update(views={OWN: 2000, OWN_REEL: 900}, insights=True, deleted=set())
    frame = pd.DataFrame([
        ["", "YouTube", "https://www.youtube.com/watch?v=fbmixYT0001", ""],
        ["", "Instagram", "https://www.instagram.com/reel/FbMixReel1/", "mixowner"],
        ["", "Facebook", f"https://www.facebook.com/CreatorIntel/videos/{OWN}/", ""],
        ["", "fb", f"https://www.facebook.com/reel/{OWN_REEL}/?mibextid=x", ""],
        ["", "Facebook", f"https://www.facebook.com/reel/{OTHER}/", ""],
        ["", "Facebook", "https://www.facebook.com/share/v/1BgbFi3ptu/", ""],
    ], columns=["Creator Name", "Platform", "Video Link", "Username"])
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    res = api.post("/api/video-performance/uploads", files={"file": ("mixed.xlsx", buffer.getvalue(), "application/octet-stream")})
    assert res.status_code == 201, res.text
    upload = res.json()["upload"]
    # the share link resolves (mocked redirect) to OWN_REEL, which is already in the file -> duplicate, not invalid
    assert (upload["added"], upload["duplicates"], upload["invalid"]) == (5, 1, 0)
    share_row = next(r for r in res.json()["rows"] if r["row"] == 7)
    assert share_row["status"] == "duplicate" and f"reel/{OWN_REEL}/" in share_row["message"]

    videos = wait_idle(api)
    assert videos["fbmixYT0001"]["status"] == "Tracking" and videos["FbMixReel1"]["status"] == "Tracking"
    own = videos[OWN]
    assert own["platform"] == "facebook" and own["status"] == "Tracking"
    assert (own["current_views"], own["likes"], own["comments"]) == (2000, 120, 14)
    assert own["creator_name"] == "CreatorIntel" and own["video_url"] == f"https://www.facebook.com/watch/?v={OWN}"
    assert videos[OTHER]["status"] == "Unsupported" and "another Facebook Page" in videos[OTHER]["status_reason"]

    # Day 2: views rise to 2500 -> second snapshot and day-over-day growth
    with session_scope() as db:
        for snap in db.scalars(select(VtViewSnapshot)):
            snap.captured_at -= timedelta(days=1)
    STATE["views"][OWN] = 2500
    assert api.post("/api/video-performance/jobs/metrics_refresh/run").status_code == 202
    time.sleep(0.3)
    own = wait_idle(api)[OWN]
    assert (own["current_views"], own["previous_views"], own["views_gained"], own["growth_pct"]) == (2500, 2000, 500, 25.0)
    history = api.get(f"/api/video-performance/history/{own['id']}").json()["snapshots"]
    assert [s["views"] for s in history][:2] == [2500, 2000]

    # Platform filter, dashboard and export include Facebook
    only_fb = api.get("/api/video-performance/videos", params={"platform": "facebook", "page_size": 100}).json()
    assert {v["video_identifier"] for v in only_fb["items"]} == {OWN, OWN_REEL, OTHER}
    dash = api.get("/api/video-performance/dashboard").json()
    assert dash["facebook_videos"] >= 3 and any(g["name"] == "facebook" for g in dash["sentiment_by_platform"])
    export = api.get("/api/video-performance/exports/csv", params={"platform": "facebook"}).content.decode("utf-8-sig")
    assert ",Facebook," in export

    # Deleted later -> Video Down (it was tracked before); retry after it is back
    STATE["deleted"].add(OWN)
    assert api.post(f"/api/video-performance/videos/{own['id']}/refresh").status_code == 202
    time.sleep(0.2)
    assert wait_idle(api)[OWN]["status"] == "Video Down"
    STATE["deleted"].clear()
    assert api.post(f"/api/video-performance/videos/{own['id']}/retry").status_code == 202
    time.sleep(0.2)
    assert wait_idle(api)[OWN]["status"] == "Tracking"

    # Pause / resume
    assert api.post(f"/api/video-performance/videos/{own['id']}/pause").status_code == 200
    assert videos_by_id(api)[OWN]["status"] == "Paused"
    assert api.post(f"/api/video-performance/videos/{own['id']}/resume").status_code == 202
    time.sleep(0.2)
    assert wait_idle(api)[OWN]["status"] == "Tracking"

    # Tokens never reach logs or API responses
    everything = caplog.text + str(videos_by_id(api)) + str(api.get("/api/config/status").json())
    assert SYSTEM_TOKEN not in everything and PAGE_TOKEN not in everything
    with session_scope() as db:
        assert db.scalar(select(VtVideo).where(VtVideo.video_identifier == OWN)).status == VideoStatus.TRACKING
