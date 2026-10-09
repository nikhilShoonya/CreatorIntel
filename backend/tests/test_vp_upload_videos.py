"""Uploads tab "View data": the tracking table and download are scoped to exactly one uploaded file."""

import csv
import io

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import app
from app.services.llm_client import LLMClient
from app.video_performance.platforms import InstagramVideoClient, YouTubeVideoClient
from app.video_performance.sentiment import VideoSentimentAnalyzer
from app.video_performance.tracker import VideoTracker

V1, V2, V3, V4 = "upldvid0001", "upldvid0002", "upldvid0003", "upldvid0004"


def youtube(request: httpx.Request) -> httpx.Response:
    items = [{"id": vid, "snippet": {"title": f"Title {vid}"}, "statistics": {"viewCount": "100", "likeCount": "1", "commentCount": "1"}}
             for vid in request.url.params["id"].split(",")]
    return httpx.Response(200, json={"items": items})


def no_network(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={})


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        settings = get_settings()
        app.state.video_tracker = VideoTracker(
            settings,
            youtube=YouTubeVideoClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(youtube))),
            instagram=InstagramVideoClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(no_network))),
            sentiment=VideoSentimentAnalyzer(settings, LLMClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(no_network)))),
        )
        yield client


def upload(api, name: str, links: list[str]) -> str:
    buffer = io.BytesIO()
    pd.DataFrame([["", "", link, ""] for link in links], columns=["Creator Name", "Platform", "Video Link", "Username"]).to_excel(buffer, index=False)
    res = api.post("/api/video-performance/uploads", files={"file": (name, buffer.getvalue(), "application/octet-stream")})
    assert res.status_code == 201, res.text
    return res.json()["upload"]["id"]


def test_upload_view_and_download_match_the_file(api):
    upload(api, "first.xlsx", [f"https://youtu.be/{V1}", f"https://youtu.be/{V2}"])
    second = upload(api, "second.xlsx", [
        f"https://www.youtube.com/watch?v={V3}",
        f"https://youtu.be/{V1}?si=abc",  # already tracked from the first file -> still part of this file
        f"https://youtu.be/{V3}",  # duplicate row in this file -> one video
        "not a link",  # invalid -> no tracked video
        f"https://www.youtube.com/shorts/{V4}",
    ])

    body = api.get("/api/video-performance/videos", params={"upload_id": second, "page_size": 100}).json()
    assert [v["video_identifier"] for v in body["items"]] == [V3, V1, V4]  # exactly this file, in file order
    assert body["total"] == 3

    res = api.get("/api/video-performance/exports/csv", params={"upload_id": second})
    assert res.status_code == 200 and "second_tracking_" in res.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(res.content.decode("utf-8-sig"))))
    assert [r["Video URL"].rsplit("=", 1)[-1] for r in rows] == [V3, V1, V4]

    # The table's own filters still apply inside one file.
    only_v1 = api.get("/api/video-performance/videos", params={"upload_id": second, "q": "upldvid0001", "page_size": 100}).json()
    assert [v["video_identifier"] for v in only_v1["items"]] == [V1]

    # A video removed from tracking disappears from the file's table and download.
    v4_id = next(v["id"] for v in body["items"] if v["video_identifier"] == V4)
    assert api.delete(f"/api/video-performance/videos/{v4_id}").status_code == 200
    after = api.get("/api/video-performance/videos", params={"upload_id": second, "page_size": 100}).json()
    assert [v["video_identifier"] for v in after["items"]] == [V3, V1]


def test_unknown_upload_is_404(api):
    assert api.get("/api/video-performance/videos", params={"upload_id": "nope"}).status_code == 404
    assert api.get("/api/video-performance/exports/excel", params={"upload_id": "nope"}).status_code == 404
