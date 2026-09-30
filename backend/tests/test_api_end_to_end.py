"""Full pipeline through the HTTP API: upload -> enrichment -> table -> retry -> export."""

import io
import time

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.agents.content_analysis_agent import ContentAnalyzer
from app.agents.instagram_agent import InstagramCollector
from app.agents.youtube_agent import YouTubeCollector
from app.config.settings import get_settings
from app.main import app
from app.services.llm_client import LLMClient
from app.services.orchestrator import EnrichmentOrchestrator
from tests.fakes import instagram_handler, llm_handler, mock_client, youtube_handler


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        settings = get_settings()
        app.state.orchestrator = EnrichmentOrchestrator(
            settings,
            youtube=YouTubeCollector(settings, client=mock_client(youtube_handler)),
            instagram=InstagramCollector(settings, client=mock_client(instagram_handler)),
            analyzer=ContentAnalyzer(LLMClient(settings, client=mock_client(llm_handler)), settings=settings),
        )
        yield test_client


def _xlsx(rows):
    buffer = io.BytesIO()
    pd.DataFrame(rows, columns=["Channel Name", "Channel Link"]).to_excel(buffer, index=False)
    return buffer.getvalue()


def _wait(client, upload_id, timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/uploads/{upload_id}").json()
        if data["status"] != "processing":
            return data
        time.sleep(0.1)
    raise AssertionError("upload did not finish")


def test_full_pipeline(client):
    content = _xlsx([
        ["Trading Tech", "https://www.instagram.com/tradingtech31/"],
        ["Nitin Nitro", "https://www.youtube.com/@nitinnitro"],
        ["Nitin Duplicate", "https://youtube.com/@NitinNitro?si=abc"],
        ["Personal Account", "https://www.instagram.com/some_personal_acct/"],
        ["Ghost Channel", "https://www.youtube.com/@doesnotexist"],
        ["Broken Row", "hello world"],
    ])
    response = client.post("/api/uploads", files={"file": ("creators.xlsx", content, "application/octet-stream")})
    assert response.status_code == 202, response.text
    upload = response.json()
    assert upload["total_rows"] == 5
    assert upload["duplicate_rows"] == 1

    done = _wait(client, upload["id"])
    assert done["status"] == "completed"
    assert done["processed_rows"] == 5
    assert done["completed_rows"] == 2
    assert done["failed_rows"] == 3

    table = client.get("/api/creators", params={"upload_id": upload["id"], "page_size": 10}).json()
    assert table["total"] == 5
    rows = {row["channel_name"]: row for row in table["items"]}

    yt = rows["Nitin Nitro"]
    assert yt["status"] == "Completed"
    assert yt["audience_count"] == 890_000
    assert yt["average_views"] == pytest.approx(550_000)  # latest 10 of 12 videos
    assert yt["top_video_views"] == 1_200_000
    assert yt["top_video_url"] == "https://www.youtube.com/watch?v=vid11"
    assert yt["engagement_rate"] == pytest.approx(5.0)
    assert yt["genre"] == "Finance & Investment"
    assert yt["language"] == "Hinglish"
    assert yt["sentiment"] == "Positive"

    ig = rows["Trading Tech"]
    assert ig["status"] == "Completed"
    assert ig["engagement_rate_basis"] == "views"
    assert ig["top_video_url"].startswith("https://www.instagram.com/reel/")

    personal = rows["Personal Account"]
    assert personal["status"] == "Failed"
    assert "Instagram data unavailable" in personal["error_message"]
    assert personal["audience_count"] is None and personal["average_views"] is None

    assert rows["Broken Row"]["platform"] == "invalid"
    assert rows["Ghost Channel"]["error_message"] == "YouTube channel not found"

    # detail + provenance
    detail = client.get(f"/api/creators/{yt['id']}").json()
    assert detail["provenance"]["average_views"] == "calculated_from_10_videos"
    assert detail["provenance"]["genre"].startswith("llm_analysis")

    # filters / search / sorting
    assert client.get("/api/creators", params={"platform": "youtube", "upload_id": upload["id"]}).json()["total"] == 2
    assert client.get("/api/creators", params={"status": "Failed", "upload_id": upload["id"]}).json()["total"] == 3
    assert client.get("/api/creators", params={"q": "trading"}).json()["total"] >= 1
    sorted_rows = client.get(
        "/api/creators", params={"sort_by": "audience", "sort_dir": "desc", "upload_id": upload["id"]}
    ).json()["items"]
    assert sorted_rows[0]["channel_name"] == "Trading Tech"

    # retry a single failed creator; invalid links cannot be retried
    ghost_id = rows["Ghost Channel"]["id"]
    assert client.post(f"/api/creators/{ghost_id}/retry").status_code == 202
    for _ in range(100):
        if client.get(f"/api/creators/{ghost_id}").json()["status"] == "Failed":
            break
        time.sleep(0.05)
    assert client.get(f"/api/creators/{ghost_id}").json()["status"] == "Failed"
    assert client.post(f"/api/creators/{rows['Broken Row']['id']}/retry").status_code == 400

    # bulk retry of an upload's failed rows (invalid links are skipped)
    bulk = client.post(f"/api/uploads/{upload['id']}/retry-failed")
    assert bulk.status_code == 202, bulk.text
    assert bulk.json()["status"] == "processing"
    after = _wait(client, upload["id"])
    assert after["status"] == "completed" and after["failed_rows"] == 3

    # re-analyze
    assert client.post(f"/api/creators/{yt['id']}/reanalyze").status_code == 202
    for _ in range(100):
        if client.get(f"/api/creators/{yt['id']}").json()["status"] == "Completed":
            break
        time.sleep(0.05)
    assert client.get(f"/api/creators/{yt['id']}").json()["genre"] == "Finance & Investment"

    # exports
    csv = client.get("/api/exports/csv", params={"upload_id": upload["id"]})
    assert csv.status_code == 200
    assert "Top Video URL" in csv.content.decode("utf-8-sig")
    excel = client.get("/api/exports/excel", params={"upload_id": upload["id"]})
    exported = pd.read_excel(io.BytesIO(excel.content))
    assert len(exported) == 5
    assert "Engagement Rate (%)" in exported.columns

    # second upload of the same creator reuses cached data
    again = client.post(
        "/api/uploads",
        files={"file": ("again.csv", b"Channel Name,Channel Link\nNitin,https://www.youtube.com/@nitinnitro\n", "text/csv")},
    ).json()
    assert again["items"][0]["from_cache"] is True


def test_upload_validation_errors(client):
    bad = client.post("/api/uploads", files={"file": ("x.csv", b"Name,Followers\nA,1\n", "text/csv")})
    assert bad.status_code == 422
    assert "Channel Link" in bad.json()["detail"]
    wrong_type = client.post("/api/uploads", files={"file": ("x.pdf", b"%PDF", "application/pdf")})
    assert wrong_type.status_code == 422


def test_config_status_never_exposes_secrets(client):
    body = client.get("/api/config/status").text
    assert "test-youtube-key" not in body and "test-groq-key" not in body and "test-meta-token" not in body
