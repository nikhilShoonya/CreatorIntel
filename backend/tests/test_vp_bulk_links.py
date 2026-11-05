"""Multiple Video Links: pasted links become one independent upload batch, processed by the normal tracker."""

import io
import time
from pathlib import Path

import httpx
import pandas as pd
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, or_, select, text

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.services.llm_client import LLMClient
from app.video_performance import repository as repo
from app.video_performance.ingest import MAX_ROWS, ImportError_, extract_links, parse_link_batch, preview_link_batch
from app.video_performance.models import VtUpload, VtUploadRow, VtVideo
from app.video_performance.facebook_apify import FacebookApifyClient
from app.video_performance.platforms import InstagramVideoClient, YouTubeVideoClient
from app.video_performance.sentiment import VideoSentimentAnalyzer
from app.video_performance.share_links import ShareLinkResolver
from app.video_performance.tracker import VideoTracker
from tests.fakes import sentiment_batch_response

OWNER = "bulkowner"
MISSING_YOUTUBE = "bulkytgone0"  # the platform no longer has this one
IG_CODES = [f"Cbulkreel{n:02d}" for n in range(12)]
BASE = "/api/video-performance"


def reel(code: str) -> str:
    return f"https://www.instagram.com/{OWNER}/reel/{code}/"


def yt(number: int) -> str:
    return f"https://youtu.be/bulkyt{number:05d}"


# ------------------------------------------------------------------ parsing (no database)
@pytest.mark.parametrize(
    "pasted",
    [
        "https://youtu.be/aaaaaaaaaaa\nhttps://www.instagram.com/reel/DcgYovoPOUL/",  # new lines
        "https://youtu.be/aaaaaaaaaaa\r\nhttps://www.instagram.com/reel/DcgYovoPOUL/\r\n",  # copied from an Excel column
        "https://youtu.be/aaaaaaaaaaa\thttps://www.instagram.com/reel/DcgYovoPOUL/",  # copied from an Excel row
        "https://youtu.be/aaaaaaaaaaa https://www.instagram.com/reel/DcgYovoPOUL/",  # spaces
        "https://youtu.be/aaaaaaaaaaa,https://www.instagram.com/reel/DcgYovoPOUL/",  # commas
        "\n\n   https://youtu.be/aaaaaaaaaaa   \n\n\t\n  https://www.instagram.com/reel/DcgYovoPOUL/  \n\n",  # blanks, extra spaces
        '"https://youtu.be/aaaaaaaaaaa"\n"https://www.instagram.com/reel/DcgYovoPOUL/"',  # quoted cells
        "Creator Name\tPlatform\tVideo Link\nAsha\tYouTube\thttps://youtu.be/aaaaaaaaaaa\nRavi\tInstagram\thttps://www.instagram.com/reel/DcgYovoPOUL/",  # table
        '=HYPERLINK("https://youtu.be/aaaaaaaaaaa","watch")\n=HYPERLINK("https://www.instagram.com/reel/DcgYovoPOUL/","reel")',
        "https://youtu.be/aaaaaaaaaaahttps://www.instagram.com/reel/DcgYovoPOUL/",  # two links with nothing between them
        "﻿https://youtu.be/aaaaaaaaaaa ​\nhttps://www.instagram.com/reel/DcgYovoPOUL/.",  # invisible characters, trailing dot
    ],
)
def test_extracts_the_links_however_they_were_pasted(pasted):
    assert extract_links(pasted) == ["https://youtu.be/aaaaaaaaaaa", "https://www.instagram.com/reel/DcgYovoPOUL/"]


def test_scheme_less_links_are_recognised():
    assert extract_links("www.youtube.com/watch?v=aaaaaaaaaaa\ninstagram.com/reel/DcgYovoPOUL/") == [
        "www.youtube.com/watch?v=aaaaaaaaaaa", "instagram.com/reel/DcgYovoPOUL/",
    ]


def test_every_link_gets_its_own_platform_and_canonical_form():
    rows = parse_link_batch(
        "https://www.youtube.com/shorts/aaaaaaaaaaa?si=x\nhttps://www.instagram.com/someone/reel/DcgYovoPOUL/?igsh=1\n"
        "https://www.facebook.com/somepage/videos/1742515786830028/"
    ).rows
    assert [(r.parsed.platform, r.parsed.url, r.status) for r in rows] == [
        ("youtube", "https://www.youtube.com/watch?v=aaaaaaaaaaa", "pending"),
        ("instagram", "https://www.instagram.com/reel/DcgYovoPOUL/", "pending"),
        ("facebook", "https://www.facebook.com/watch/?v=1742515786830028", "pending"),
    ]
    assert rows[1].owner_username == "someone"  # kept from the link, as a file row would
    assert rows[0].raw_link == "https://www.youtube.com/shorts/aaaaaaaaaaa?si=x"  # the row keeps what was pasted


def test_duplicates_are_found_on_the_video_not_the_text():
    rows = parse_link_batch(
        "https://youtu.be/aaaaaaaaaaa\nhttps://www.youtube.com/watch?v=aaaaaaaaaaa\nhttps://youtu.be/aaaaaaaaaaa?si=abc\n"
        "https://www.instagram.com/reel/DcgYovoPOUL/\nhttps://www.instagram.com/reels/DcgYovoPOUL/"
    ).rows
    assert [r.status for r in rows] == ["pending", "duplicate", "duplicate", "pending", "duplicate"]


def test_invalid_entries_are_reported_with_their_reason():
    rows = parse_link_batch(
        "https://youtu.be/aaaaaaaaaaa\nhttps://www.tiktok.com/@a/video/123\nhttps://www.instagram.com/someone/\nhello\nJust some words\n"
    ).rows
    assert [(r.raw_link, r.status) for r in rows] == [
        ("https://youtu.be/aaaaaaaaaaa", "pending"),
        ("https://www.tiktok.com/@a/video/123", "invalid"),
        ("https://www.instagram.com/someone/", "invalid"),
        ("hello", "invalid"),
        ("Just some words", "invalid"),
    ]
    assert "Unsupported platform" in rows[1].message and "Facebook" in rows[1].message
    assert "reel/post" in rows[2].message
    assert all(r.message for r in rows[1:])


def test_header_lines_are_not_reported_as_invalid_links():
    assert extract_links("Video Link\nhttps://youtu.be/aaaaaaaaaaa") == ["https://youtu.be/aaaaaaaaaaa"]
    assert extract_links("Title\tViews\tLink\nA reel\t10\thttps://youtu.be/aaaaaaaaaaa") == ["https://youtu.be/aaaaaaaaaaa"]


@pytest.mark.parametrize("pasted", ["", "   \n\t\n ", "Video Link\n"])
def test_nothing_to_upload_is_a_clear_error(pasted):
    with pytest.raises(ImportError_, match="No video links"):
        parse_link_batch(pasted)


def test_too_many_links_is_a_clear_error():
    with pytest.raises(ImportError_, match=f"more than {MAX_ROWS}"):
        parse_link_batch("\n".join(f"https://youtu.be/{n:011d}" for n in range(MAX_ROWS + 1)))


def test_preview_counts_a_share_link_as_valid_because_it_is_only_resolved_on_upload():
    preview = preview_link_batch(
        "https://www.facebook.com/share/v/AbCdEf123/\nhttps://www.facebook.com/share/v/AbCdEf123/\nhttps://youtu.be/aaaaaaaaaaa\nnope\n"
    )
    assert (preview.detected, preview.valid, preview.duplicates, preview.invalid) == (4, 2, 1, 1)
    assert preview.platforms == {"facebook": 1, "youtube": 1}
    assert preview.invalid_links[0].link == "nope"


# ------------------------------------------------------------------ API (mock platforms, temporary database)
def youtube(request: httpx.Request) -> httpx.Response:
    items = [{"id": vid, "snippet": {"title": f"Title {vid}"}, "statistics": {"viewCount": "100", "likeCount": "1", "commentCount": "1"}}
             for vid in request.url.params["id"].split(",") if vid != MISSING_YOUTUBE]
    return httpx.Response(200, json={"items": items})


def instagram(request: httpx.Request) -> httpx.Response:
    media = [{
        "id": code, "permalink": f"https://www.instagram.com/reel/{code}/", "media_type": "VIDEO", "media_product_type": "REELS",
        "timestamp": "2026-09-01T10:00:00+0000", "like_count": 5, "comments_count": 1, "view_count": 200, "caption": code,
    } for code in IG_CODES]
    return httpx.Response(200, json={"business_discovery": {
        "id": "1784", "username": OWNER, "name": "Bulk Owner", "followers_count": 10, "media": {"data": media},
    }})


def llm(request: httpx.Request) -> httpx.Response:
    return sentiment_batch_response(request, "Positive", 0.9)


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        settings = get_settings()
        app.state.video_tracker = VideoTracker(
            settings,
            youtube=YouTubeVideoClient(settings, client=client_for(youtube)),
            instagram=InstagramVideoClient(settings, client=client_for(instagram)),
            facebook=FacebookApifyClient(settings),  # disabled in tests: Facebook videos fail with a stated reason
            sentiment=VideoSentimentAnalyzer(settings, LLMClient(settings, client=client_for(llm))),
        )
        yield client
        # The test database is shared with the other modules, some of which count every video of a platform.
        with session_scope() as db:
            ours = or_(VtVideo.video_identifier.like("bulk%"), VtVideo.video_identifier.like("Cbulk%"), VtVideo.video_identifier.like("17425157868%"))
            repo.delete_videos(list(db.scalars(select(VtVideo.id).where(ours))))


def post_links(api, links: str, **extra) -> dict:
    res = api.post(f"{BASE}/uploads/links", json={"links": links, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def wait_done(api, upload_id: str, timeout: float = 15) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = api.get(f"{BASE}/uploads/{upload_id}/rows").json()
        if body["upload"]["videos_in_progress"] == 0:
            return body
        time.sleep(0.05)
    raise AssertionError("batch still processing")


def batch_videos(api, upload_id: str) -> list[dict]:
    return api.get(f"{BASE}/videos", params={"upload_id": upload_id, "page_size": 100}).json()["items"]


def test_ten_instagram_reels_become_one_processed_batch(api):
    links = [reel(code) for code in IG_CODES[:10]]
    result = post_links(api, "\n".join(links))
    upload = result["upload"]
    assert upload["upload_type"] == "links"
    assert upload["filename"].startswith("Bulk links ")
    assert (upload["total_rows"], upload["added"], upload["invalid"], upload["duplicates"]) == (10, 10, 0, 0)
    assert upload["file_available"] is False

    done = wait_done(api, upload["id"])["upload"]
    assert (done["videos_total"], done["videos_processed"], done["videos_failed"]) == (10, 10, 0)
    assert done["processing_status"] == "completed"
    videos = batch_videos(api, upload["id"])
    assert [v["video_identifier"] for v in videos] == IG_CODES[:10]  # in the pasted order
    assert all(v["platform"] == "instagram" and v["current_views"] == 200 and v["source"] == "upload" for v in videos)


def test_multiple_youtube_links_with_blanks_duplicates_and_invalid_rows(api):
    pasted = f"\n\n  {yt(1)}  \n\n{yt(2)}\n{yt(1)}?si=again\nnot a link\nhttps://vimeo.com/123\n"
    result = post_links(api, pasted, name="  Campaign / launch.xlsx ")
    upload = result["upload"]
    assert upload["filename"] == "launch.xlsx"  # same cleaning as Rename
    assert (upload["total_rows"], upload["added"], upload["duplicates"], upload["invalid"]) == (5, 2, 1, 2)
    assert [r["status"] for r in result["rows"]] == ["added", "added", "duplicate", "invalid", "invalid"]

    body = wait_done(api, upload["id"])
    assert body["upload"]["processing_status"] == "partial"  # everything fetched, but some links were unusable
    assert (body["upload"]["videos_total"], body["upload"]["videos_processed"]) == (2, 2)
    assert [r["outcome"] for r in body["rows"]] == ["processed", "processed", "duplicate", "invalid", "invalid"]
    assert [v["video_identifier"] for v in batch_videos(api, upload["id"])] == ["bulkyt00001", "bulkyt00002"]


def test_mixed_platforms_with_partial_failures_keep_going_and_say_why(api):
    upload = post_links(api, "\n".join([
        yt(3),
        f"https://youtu.be/{MISSING_YOUTUBE}",  # deleted on the platform
        reel(IG_CODES[10]),
        "https://www.facebook.com/somepage/videos/1742515786830999/",  # the platform errors
        "https://example.com/video.mp4",  # never reaches a platform
    ]))["upload"]
    body = wait_done(api, upload["id"])
    done = body["upload"]
    assert (done["videos_total"], done["videos_processed"], done["videos_failed"], done["invalid"]) == (4, 2, 2, 1)
    assert done["processing_status"] == "partial"

    by_link = {r["video_link"]: r for r in body["rows"]}
    assert by_link[yt(3)]["outcome"] == "processed"
    assert by_link[reel(IG_CODES[10])]["outcome"] == "processed"
    gone = by_link[f"https://youtu.be/{MISSING_YOUTUBE}"]
    assert (gone["outcome"], gone["video_status"]) == ("failed", "Video Down") and gone["video_status_reason"]
    broken = by_link["https://www.facebook.com/somepage/videos/1742515786830999/"]
    assert broken["outcome"] == "failed" and broken["platform"] == "facebook" and broken["video_status_reason"]
    assert by_link["https://example.com/video.mp4"]["outcome"] == "invalid"
    assert {v["platform"] for v in batch_videos(api, upload["id"])} == {"youtube", "instagram", "facebook"}


def test_batches_stay_separate_and_a_shared_video_is_not_fetched_again(api):
    first = post_links(api, f"{yt(10)}\n{yt(11)}")["upload"]["id"]
    wait_done(api, first)
    checked_before = next(v for v in batch_videos(api, first) if v["video_identifier"] == "bulkyt00011")["last_checked_at"]

    second_result = post_links(api, f"{yt(11)}\n{yt(12)}")
    second = second_result["upload"]["id"]
    assert [r["status"] for r in second_result["rows"]] == ["already_tracked", "added"]
    wait_done(api, second)

    assert [v["video_identifier"] for v in batch_videos(api, first)] == ["bulkyt00010", "bulkyt00011"]
    assert [v["video_identifier"] for v in batch_videos(api, second)] == ["bulkyt00011", "bulkyt00012"]
    assert next(v for v in batch_videos(api, second) if v["video_identifier"] == "bulkyt00011")["last_checked_at"] == checked_before

    # The batch's filters, totals and download stay inside it.
    totals = api.get(f"{BASE}/uploads/{first}/platform-totals").json()["platforms"]
    assert next(p for p in totals if p["platform"] == "youtube") == {"platform": "youtube", "total_views": 200, "videos": 2, "videos_with_views": 2, "videos_rounded": 0}
    assert api.get(f"{BASE}/videos", params={"upload_id": first, "q": "bulkyt00012"}).json()["total"] == 0


def test_batches_appear_in_upload_history_alongside_files_and_can_be_managed(api):
    batch = post_links(api, yt(20), name="Renamed later")["upload"]["id"]
    wait_done(api, batch)

    buffer = io.BytesIO()
    pd.DataFrame([["", "", yt(21), ""]], columns=["Creator Name", "Platform", "Video Link", "Username"]).to_excel(buffer, index=False)
    file_upload = api.post(f"{BASE}/uploads", files={"file": ("plain.xlsx", buffer.getvalue(), "application/octet-stream")}).json()["upload"]

    listed = {u["id"]: u for u in api.get(f"{BASE}/uploads").json()}
    assert listed[batch]["upload_type"] == "links" and listed[batch]["processing_status"] == "completed"
    # An Excel / CSV upload answers exactly as before: no batch status of its own.
    assert listed[file_upload["id"]]["upload_type"] == "file"
    assert listed[file_upload["id"]]["processing_status"] is None and listed[file_upload["id"]]["file_available"] is True
    assert [v["video_identifier"] for v in batch_videos(api, file_upload["id"])] == ["bulkyt00021"]

    assert api.patch(f"{BASE}/uploads/{batch}", json={"filename": "New name"}).json()["filename"] == "New name"
    assert api.post(f"{BASE}/uploads/{batch}/refresh").status_code == 202
    wait_done(api, batch)
    assert api.get(f"{BASE}/uploads/{batch}/file").status_code == 404
    exported = api.get(f"{BASE}/exports/csv", params={"upload_id": batch})
    assert exported.status_code == 200 and "New name_tracking_" in exported.headers["content-disposition"]
    assert api.get(f"{BASE}/uploads/{batch}/rows/export", params={"format": "csv"}).status_code == 200

    # A deleted video leaves the batch's table and counts; deleting the batch leaves other videos alone.
    vid = batch_videos(api, batch)[0]["id"]
    assert api.delete(f"{BASE}/videos/{vid}").status_code == 200
    assert batch_videos(api, batch) == []
    assert api.get(f"{BASE}/uploads/{batch}/rows").json()["upload"]["videos_total"] == 0
    assert api.delete(f"{BASE}/uploads/{batch}").status_code == 200
    assert api.get(f"{BASE}/uploads/{batch}/rows").status_code == 404
    assert [v["video_identifier"] for v in batch_videos(api, file_upload["id"])] == ["bulkyt00021"]


def test_file_rows_now_point_at_their_video_too(api):
    buffer = io.BytesIO()
    pd.DataFrame([["", "", yt(30), ""], ["", "", "nope", ""]], columns=["Creator Name", "Platform", "Video Link", "Username"]).to_excel(buffer, index=False)
    upload = api.post(f"{BASE}/uploads", files={"file": ("linked.xlsx", buffer.getvalue(), "application/octet-stream")}).json()["upload"]
    rows = api.get(f"{BASE}/uploads/{upload['id']}/rows").json()["rows"]
    assert rows[0]["video_id"] is not None and rows[1]["video_id"] is None


def test_a_hundred_links_are_all_processed(api):
    upload = post_links(api, "\n".join(yt(number) for number in range(1000, 1100)))["upload"]
    assert upload["added"] == 100
    done = wait_done(api, upload["id"], timeout=30)["upload"]
    assert (done["videos_total"], done["videos_processed"], done["videos_failed"]) == (100, 100, 0)
    assert api.get(f"{BASE}/videos", params={"upload_id": upload["id"], "page_size": 10}).json()["total"] == 100


def test_a_batch_where_every_video_fails_says_so(api):
    upload = post_links(api, "https://www.facebook.com/somepage/videos/1742515786830111/")["upload"]
    done = wait_done(api, upload["id"])["upload"]
    assert (done["videos_total"], done["videos_processed"], done["videos_failed"]) == (1, 0, 1)
    assert done["processing_status"] == "failed"


def test_facebook_share_links_are_resolved_like_in_a_file(api):
    real = "https://www.facebook.com/reel/1742515786830222/"

    def redirects(request: httpx.Request) -> httpx.Response:
        if "AbCgone" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(302, headers={"location": real})

    async def public_ip(_host: str) -> list[str]:
        return ["157.240.1.35"]

    app.state.share_resolver = ShareLinkResolver(client=client_for(redirects), resolve_host=public_ip)
    try:
        result = post_links(api, f"https://www.facebook.com/share/r/AbCok/\n{real}\nhttps://www.facebook.com/share/v/AbCgone/\n{yt(60)}")
    finally:
        app.state.share_resolver = None
    assert [r["status"] for r in result["rows"]] == ["added", "duplicate", "invalid", "added"]  # same video, found through the redirect
    assert "did not redirect" in result["rows"][2]["message"]
    assert result["rows"][0]["video_url"] == real
    videos = batch_videos(api, result["upload"]["id"])
    assert [v["video_identifier"] for v in videos] == ["1742515786830222", "bulkyt00060"]


def test_a_batch_with_nothing_usable_is_rejected_without_a_history_entry(api):
    before = len(api.get(f"{BASE}/uploads").json())
    for pasted, expected in (("  \n ", "No video links"), ("https://vimeo.com/1\nhello", "None of the")):
        res = api.post(f"{BASE}/uploads/links", json={"links": pasted})
        assert res.status_code == 422 and expected in res.json()["detail"]
    assert len(api.get(f"{BASE}/uploads").json()) == before


def test_preview_endpoint_saves_nothing(api):
    before = len(api.get(f"{BASE}/uploads").json())
    res = api.post(f"{BASE}/uploads/links/preview", json={"links": f"{yt(40)}\n{yt(40)}\nhttps://www.instagram.com/reel/Cbulkprev1/\nnope"})
    assert res.status_code == 200
    assert res.json() == {
        "detected": 4, "valid": 2, "duplicates": 1, "invalid": 1,
        "platforms": {"youtube": 1, "instagram": 1},
        "invalid_links": [{"link": "nope", "message": "Video link is not a valid URL"}],
    }
    assert len(api.get(f"{BASE}/uploads").json()) == before
    assert api.get(f"{BASE}/videos", params={"q": "bulkyt00040"}).json()["total"] == 0


def test_single_link_add_video_is_unchanged_and_stays_out_of_history(api):
    before = len(api.get(f"{BASE}/uploads").json())
    res = api.post(f"{BASE}/videos", json={"video_url": yt(50)})
    assert res.status_code == 201 and res.json()["source"] == "manual"
    assert api.post(f"{BASE}/videos", json={"video_url": yt(50)}).status_code == 409
    assert len(api.get(f"{BASE}/uploads").json()) == before


# ------------------------------------------------------------------ migration
def test_upgrade_keeps_existing_uploads_as_files(tmp_path):
    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    engine = create_engine(f"sqlite:///{(tmp_path / 'installed.db').as_posix()}")
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "0007_apify_work_finished_at")
            connection.execute(text(
                "INSERT INTO video_tracking_uploads (id, filename, total_rows, added, already_tracked, duplicates, invalid, created_at) "
                "VALUES ('old', 'old.xlsx', 1, 1, 0, 0, 0, '2026-01-01')"
            ))
            connection.execute(text(
                "INSERT INTO video_tracking_upload_rows (upload_id, row_number, video_link, status) VALUES ('old', 1, 'https://youtu.be/aaaaaaaaaaa', 'added')"
            ))
            command.upgrade(config, "head")
            assert connection.execute(text(f"SELECT upload_type FROM {VtUpload.__tablename__}")).scalar_one() == "file"
            assert connection.execute(text(f"SELECT video_id FROM {VtUploadRow.__tablename__}")).scalar_one() is None
            command.downgrade(config, "0007_apify_work_finished_at")
            assert "upload_type" not in {c["name"] for c in inspect(connection).get_columns(VtUpload.__tablename__)}
            assert "video_id" not in {c["name"] for c in inspect(connection).get_columns(VtUploadRow.__tablename__)}
            assert connection.execute(text("SELECT filename FROM video_tracking_uploads")).scalar_one() == "old.xlsx"
            assert connection.execute(text("SELECT video_link FROM video_tracking_upload_rows")).scalar_one() == "https://youtu.be/aaaaaaaaaaa"
    finally:
        engine.dispose()
