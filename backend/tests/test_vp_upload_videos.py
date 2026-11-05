"""Uploads tab "View data": the tracking table and download are scoped to exactly one uploaded file."""

import csv
import io

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.services.llm_client import LLMClient
from app.video_performance.models import VtUpload, VtUploadRow, VtVideo
from app.video_performance.platforms import InstagramVideoClient, YouTubeVideoClient
from app.video_performance.sentiment import VideoSentimentAnalyzer
from app.video_performance.tracker import VideoTracker
from app.video_performance.urls import parse_video_link

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


def seed_file(name: str, videos: list[tuple[str, int | None, str]], extra_rows: list[tuple[str, str]] = ()) -> str:
    """A saved upload whose rows point at videos with known view counts (no background tracking involved)."""
    with session_scope() as db:
        upload = VtUpload(filename=name)
        db.add(upload)
        db.flush()
        for number, (link, views, status) in enumerate(videos, start=1):
            parsed = parse_video_link(link)
            assert parsed.ok, link
            db.add(VtVideo(platform=parsed.platform, video_identifier=parsed.identifier, video_url=link, current_views=views, status=status))
            db.add(VtUploadRow(upload_id=upload.id, row_number=number, video_link=link, platform=parsed.platform, status="added"))
        for offset, (link, row_status) in enumerate(extra_rows, start=len(videos) + 1):
            db.add(VtUploadRow(upload_id=upload.id, row_number=offset, video_link=link, status=row_status))
        db.flush()
        return upload.id


def platform_totals(api, upload_id: str) -> tuple[dict[str, dict], int]:
    res = api.get(f"/api/video-performance/uploads/{upload_id}/platform-totals")
    assert res.status_code == 200, res.text
    body = res.json()
    return {p["platform"]: p for p in body["platforms"]}, body["in_progress"]


def test_platform_totals_sum_only_this_files_videos(api):
    yt_a, yt_b, yt_c = "https://youtu.be/totalsvid01", "https://youtu.be/totalsvid02", "https://youtu.be/totalsvid03"
    reel_ok, reel_bad = "https://www.instagram.com/someone/reel/Ctotals0001/", "https://www.instagram.com/someone/reel/Ctotals0002/"
    facebook = "https://www.facebook.com/watch/?v=1742515786830028"
    file_id = seed_file(
        "totals.xlsx",
        [
            (yt_a, 100, "Tracking"),
            (yt_b, 250, "Tracking"),
            (yt_c, None, "Pending"),  # not checked yet -> counted as a video, adds no views
            (reel_ok, 5000, "Tracking"),
            (reel_bad, -1, "Tracking"),  # an impossible count must not lower the total
            (facebook, None, "Failed"),
        ],
        extra_rows=[("not a link", "invalid"), (f"{yt_a}?si=abc", "duplicate")],  # invalid adds nothing; duplicate is one video
    )
    seed_file("other.xlsx", [("https://youtu.be/totalsvid04", 999_999, "Tracking")])  # another file must not leak in

    totals, in_progress = platform_totals(api, file_id)
    assert totals["youtube"] == {"platform": "youtube", "total_views": 350, "videos": 3, "videos_with_views": 2, "videos_rounded": 0}
    assert totals["instagram"] == {"platform": "instagram", "total_views": 5000, "videos": 2, "videos_with_views": 1, "videos_rounded": 0}
    assert totals["facebook"] == {"platform": "facebook", "total_views": 0, "videos": 1, "videos_with_views": 0, "videos_rounded": 0}
    assert in_progress == 1

    # Totals follow the data: a new count and a removed video both change them.
    listed = api.get("/api/video-performance/videos", params={"upload_id": file_id, "page_size": 100}).json()["items"]
    with session_scope() as db:
        db.get(VtVideo, next(v["id"] for v in listed if v["video_url"] == yt_c)).current_views = 50
    assert api.delete(f"/api/video-performance/videos/{next(v['id'] for v in listed if v['video_url'] == yt_a)}").status_code == 200
    totals, _ = platform_totals(api, file_id)
    assert (totals["youtube"]["total_views"], totals["youtube"]["videos"]) == (300, 2)


def test_platform_totals_list_every_platform_even_when_absent(api):
    only_youtube = seed_file("youtube-only.xlsx", [("https://youtu.be/totalsvid05", 7, "Tracking")])
    totals, in_progress = platform_totals(api, only_youtube)
    assert list(totals) == ["youtube", "instagram", "facebook"]
    assert totals["instagram"] == {"platform": "instagram", "total_views": 0, "videos": 0, "videos_with_views": 0, "videos_rounded": 0}
    assert in_progress == 0


def test_unknown_upload_is_404(api):
    assert api.get("/api/video-performance/videos", params={"upload_id": "nope"}).status_code == 404
    assert api.get("/api/video-performance/exports/excel", params={"upload_id": "nope"}).status_code == 404
    assert api.get("/api/video-performance/uploads/nope/platform-totals").status_code == 404
