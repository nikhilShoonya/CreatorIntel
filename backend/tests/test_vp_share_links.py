"""Automatic Facebook share-link resolution (offline: Facebook's redirects are mocked)."""

import asyncio
import io

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import app
from app.services.llm_client import LLMClient
from app.video_performance import queries
from app.video_performance.platforms import FacebookVideoClient, InstagramVideoClient, YouTubeVideoClient
from app.video_performance.sentiment import VideoSentimentAnalyzer
from app.video_performance.share_links import (
    ShareLinkResolver,
    is_share_link,
    resolution_note,
    resolved_link_from_note,
)
from app.video_performance.tracker import VideoTracker

REEL = "934248642298198"
SEEN: list[httpx.Request] = []


async def public_ip(_host: str) -> list[str]:
    return ["157.240.1.35"]


async def private_ip(_host: str) -> list[str]:
    return ["10.0.0.5"]


def facebook(request: httpx.Request) -> httpx.Response:
    """Mimics Facebook: share codes redirect to the canonical Reel; unknown codes answer 200 without a redirect."""
    SEEN.append(request)
    path = request.url.path
    if path == "/share/v/1BgbFi3ptu/":
        return httpx.Response(302, headers={"location": f"https://www.facebook.com/reel/{REEL}/?fs=e&rdid=x"})
    if request.url.host == "m.facebook.com" and path == "/share/r/TwoHop123/":  # second hop -> the Reel
        return httpx.Response(302, headers={"location": "/reel/1027646239853010/"})
    if path == "/share/r/TwoHop123/":  # first hop sets a cookie and goes to the mobile host
        return httpx.Response(302, headers={"location": "https://m.facebook.com/share/r/TwoHop123/",
                                            "set-cookie": "fr=tracking; Domain=.facebook.com; Path=/"})
    if path == "/share/v/EvilRedirect/":
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    if path == "/share/v/Loop0000/":
        return httpx.Response(302, headers={"location": "https://www.facebook.com/share/v/Loop0000/"})
    if request.url.host == "fb.watch":
        return httpx.Response(301, headers={"location": "https://www.facebook.com/watch/?v=1713336039884094&ref=x"})
    return httpx.Response(200, text="<html>login</html>")


def resolver(handler=facebook, resolve_host=public_ip) -> ShareLinkResolver:
    return ShareLinkResolver(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), resolve_host=resolve_host)


# ------------------------------------------------------------------ detection
@pytest.mark.parametrize("link, expected", [
    ("https://www.facebook.com/share/v/1BgbFi3ptu/?mibextid=wwXIfr", True),
    ("https://m.facebook.com/share/r/1DYTez8U37/", True),
    ("facebook.com/share/v/1D8QHqh1P8", True),
    ("https://fb.watch/abcDEF123/", True),
    (f"https://www.facebook.com/reel/{REEL}/", False),  # already contains the video ID
    ("https://www.facebook.com/share/p/1ABCpost/", False),  # posts are not videos
    ("https://www.youtube.com/watch?v=aaaaaaaaaaa", False),
    ("https://evil.example/share/v/1BgbFi3ptu/", False),
    ("", False),
])
def test_is_share_link(link, expected):
    assert is_share_link(link) is expected


# ------------------------------------------------------------------ resolution
def test_one_hop_redirect_gives_the_canonical_reel():
    SEEN.clear()
    result = asyncio.run(resolver().resolve("https://www.facebook.com/share/v/1BgbFi3ptu/?mibextid=wwXIfr"))
    assert result.resolved_url == f"https://www.facebook.com/reel/{REEL}/" and result.error is None
    assert len(SEEN) == 1  # stops as soon as the redirect names a real video; the Reel page is not fetched


def test_two_hops_and_no_cookies_are_sent():
    SEEN.clear()
    result = asyncio.run(resolver().resolve("https://www.facebook.com/share/r/TwoHop123/"))
    assert result.resolved_url == "https://www.facebook.com/reel/1027646239853010/"
    assert len(SEEN) == 2 and all("cookie" not in r.headers for r in SEEN)
    assert SEEN[1].url.host == "m.facebook.com"


def test_fb_watch_short_link():
    result = asyncio.run(resolver().resolve("https://fb.watch/abcDEF123/"))
    assert result.resolved_url == "https://www.facebook.com/watch/?v=1713336039884094"


def test_redirect_to_a_non_facebook_or_private_address_is_not_followed():
    SEEN.clear()
    evil = asyncio.run(resolver().resolve("https://www.facebook.com/share/v/EvilRedirect/"))
    assert evil.resolved_url is None and "unexpected address" in evil.error
    assert all(r.url.host != "169.254.169.254" for r in SEEN)

    SEEN.clear()
    internal = asyncio.run(resolver(resolve_host=private_ip).resolve("https://www.facebook.com/share/v/1BgbFi3ptu/"))
    assert internal.resolved_url is None and SEEN == []  # nothing is requested when facebook.com resolves privately


def test_no_redirect_loops_and_network_errors_are_reported_not_guessed():
    unknown = asyncio.run(resolver().resolve("https://www.facebook.com/share/v/NoSuchCode/"))
    assert unknown.resolved_url is None and "did not redirect this share link to a video" in unknown.error

    loop = asyncio.run(resolver().resolve("https://www.facebook.com/share/v/Loop0000/"))
    assert loop.resolved_url is None and "too many times" in loop.error

    def offline(request):
        raise httpx.ConnectError("offline", request=request)

    down = asyncio.run(resolver(handler=offline).resolve("https://www.facebook.com/share/v/1BgbFi3ptu/"))
    assert down.resolved_url is None and "try again later" in down.error


def test_successes_are_cached_failures_are_retried():
    SEEN.clear()
    shared = resolver()

    async def run():
        await shared.resolve("https://www.facebook.com/share/v/1BgbFi3ptu/")
        await shared.resolve("https://www.facebook.com/share/v/1BgbFi3ptu/")
        await shared.resolve("https://www.facebook.com/share/v/NoSuchCode/")
        await shared.resolve("https://www.facebook.com/share/v/NoSuchCode/")

    asyncio.run(run())
    assert len(SEEN) == 3  # 1 for the cached success + 2 for the failure (not cached)


def test_resolution_note_round_trip():
    note = "Duplicate of an earlier row in this file; " + resolution_note(f"https://www.facebook.com/reel/{REEL}/") \
        + "; Platform column says 'yt', link is facebook - the link was used"
    assert resolved_link_from_note(note) == f"https://www.facebook.com/reel/{REEL}/"
    assert resolved_link_from_note("Already being tracked") is None and resolved_link_from_note(None) is None


# ------------------------------------------------------------------ API: upload, add, edit, View data
def graph_offline(_request):
    return httpx.Response(400, json={"error": {"code": 10, "message": "(#10) Application does not have permission"}})


def ok_youtube(request):
    items = [{"id": v, "snippet": {"title": "t"}, "statistics": {"viewCount": "1"}} for v in request.url.params["id"].split(",")]
    return httpx.Response(200, json={"items": items})


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        settings = get_settings().model_copy(update={"meta_facebook_page_id": "1361142767079677"})
        mock = lambda h: httpx.AsyncClient(transport=httpx.MockTransport(h))  # noqa: E731
        app.state.video_tracker = VideoTracker(
            settings, youtube=YouTubeVideoClient(settings, client=mock(ok_youtube)),
            instagram=InstagramVideoClient(settings, client=mock(graph_offline)),
            facebook=FacebookVideoClient(settings, client=mock(graph_offline)),
            sentiment=VideoSentimentAnalyzer(settings, LLMClient(settings, client=mock(graph_offline))),
        )
        app.state.share_resolver = resolver()
        yield client
        app.state.share_resolver = None


def test_upload_resolves_share_links_and_view_data_matches(api):
    frame = pd.DataFrame([
        ["https://www.facebook.com/share/v/1BgbFi3ptu/?mibextid=wwXIfr"],
        [f"https://www.facebook.com/reel/{REEL}/"],  # the same video as the share link -> duplicate
        ["https://www.facebook.com/share/v/NoSuchCode/"],  # does not redirect -> invalid with the reason
        ["https://www.youtube.com/watch?v=shareYT0001"],
    ], columns=["Video Link"])
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    res = api.post("/api/video-performance/uploads", files={"file": ("shares.xlsx", buffer.getvalue(), "application/octet-stream")})
    assert res.status_code == 201, res.text
    body = res.json()
    rows = {r["row"]: r for r in body["rows"]}
    assert rows[2]["status"] == "added" and rows[2]["video_url"] == f"https://www.facebook.com/reel/{REEL}/"
    assert resolved_link_from_note(rows[2]["message"]) == f"https://www.facebook.com/reel/{REEL}/"
    assert rows[3]["status"] == "duplicate"
    assert rows[4]["status"] == "invalid" and "did not redirect" in rows[4]["message"]
    assert (body["upload"]["added"], body["upload"]["duplicates"], body["upload"]["invalid"]) == (2, 1, 1)

    # the stored row keeps the original share link, and "View data" still maps it to the tracked Reel
    saved = api.get(f"/api/video-performance/uploads/{body['upload']['id']}/rows").json()["rows"]
    assert saved[0]["video_link"] == "https://www.facebook.com/share/v/1BgbFi3ptu/?mibextid=wwXIfr"
    tracked = api.get("/api/video-performance/videos", params={"upload_id": body["upload"]["id"], "page_size": 100}).json()
    assert [v["video_identifier"] for v in tracked["items"]] == [REEL, "shareYT0001"]
    assert queries.upload_video_ids(body["upload"]["id"]) == [v["id"] for v in tracked["items"]]


def test_add_and_edit_video_with_share_links(api):
    res = api.post("/api/video-performance/videos", json={"video_url": "https://fb.watch/abcDEF123/"})
    assert res.status_code == 201, res.text
    video = res.json()
    assert video["platform"] == "facebook" and video["video_identifier"] == "1713336039884094"
    assert video["video_url"] == "https://www.facebook.com/watch/?v=1713336039884094"

    bad = api.post("/api/video-performance/videos", json={"video_url": "https://www.facebook.com/share/v/NoSuchCode/"})
    assert bad.status_code == 422 and "did not redirect" in bad.json()["detail"]

    edited = api.put(f"/api/video-performance/videos/{video['id']}",
                     json={"video_url": "https://www.facebook.com/share/r/TwoHop123/"})
    assert edited.status_code == 200, edited.text
    assert edited.json()["video_identifier"] == "1027646239853010"
