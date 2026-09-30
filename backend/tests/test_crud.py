"""Create / update / delete for creators and uploads."""

import time

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
        # start from an empty database for this module
        for creator in test_client.get("/api/creators", params={"page_size": 100}).json()["items"]:
            test_client.delete(f"/api/creators/{creator['id']}")
        for upload in test_client.get("/api/uploads").json():
            test_client.delete(f"/api/uploads/{upload['id']}")
        yield test_client


def _wait_final(client, creator_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/creators/{creator_id}").json()
        if data["status"] not in ("Pending", "Processing"):
            return data
        time.sleep(0.05)
    raise AssertionError("creator did not finish")


def test_add_edit_delete_creator(client):
    created = client.post("/api/creators", json={"channel_name": "Nitin", "channel_link": "https://youtube.com/@nitinnitro"})
    assert created.status_code == 201, created.text
    creator_id = created.json()["id"]
    assert _wait_final(client, creator_id)["subscriber_count"] == 890_000

    # duplicate channel is rejected
    dup = client.post("/api/creators", json={"channel_name": "Again", "channel_link": "https://www.youtube.com/@NitinNitro/videos"})
    assert dup.status_code == 409

    # invalid link is rejected with a clear message
    bad = client.post("/api/creators", json={"channel_name": "X", "channel_link": "https://twitter.com/x"})
    assert bad.status_code == 422 and "Unsupported" in bad.json()["detail"]

    # rename only: data is kept, no re-enrichment
    renamed = client.patch(f"/api/creators/{creator_id}", json={"channel_name": "Nitin Nitro"})
    assert renamed.status_code == 200
    assert renamed.json()["channel_name"] == "Nitin Nitro"
    assert renamed.json()["subscriber_count"] == 890_000

    # change link: old data is cleared and the new channel is enriched
    moved = client.patch(f"/api/creators/{creator_id}", json={"channel_link": "https://www.instagram.com/tradingtech31/"})
    assert moved.status_code == 200
    assert moved.json()["platform"] == "instagram"
    assert moved.json()["subscriber_count"] is None
    final = _wait_final(client, creator_id)
    assert final["followers_count"] == 1_200_000 and final["status"] == "Completed"

    assert client.delete(f"/api/creators/{creator_id}").status_code == 200
    assert client.get(f"/api/creators/{creator_id}").status_code == 404


def test_add_creator_into_upload_and_delete_upload(client):
    upload = client.post(
        "/api/uploads",
        files={"file": ("list.csv", b"Channel Name,Channel Link\nBroken,not a link\n", "text/csv")},
    ).json()
    for _ in range(100):
        if client.get(f"/api/uploads/{upload['id']}").json()["status"] != "processing":
            break
        time.sleep(0.05)

    # fix the broken row by editing its link
    broken = client.get("/api/creators", params={"upload_id": upload["id"]}).json()["items"][0]
    assert broken["platform"] == "invalid"
    fixed = client.patch(f"/api/creators/{broken['id']}", json={"channel_link": "https://www.youtube.com/@nitinnitro"})
    assert fixed.status_code == 200 and fixed.json()["platform"] == "youtube"
    assert _wait_final(client, broken["id"])["status"] == "Completed"

    # add another creator directly into this upload
    added = client.post(
        "/api/creators",
        json={"channel_name": "Trading Tech", "channel_link": "instagram.com/tradingtech31", "upload_id": upload["id"]},
    )
    assert added.status_code == 201, added.text
    _wait_final(client, added.json()["id"])
    detail = client.get(f"/api/uploads/{upload['id']}").json()
    assert detail["total_rows"] == 2 and detail["items"][1]["position"] == 2

    # bulk delete one, then delete the upload together with its remaining creator
    assert client.post("/api/creators/bulk-delete", json={"ids": [added.json()["id"]]}).json()["deleted"] == 1
    removed = client.delete(f"/api/uploads/{upload['id']}", params={"delete_creators": True})
    assert removed.status_code == 200, removed.text
    assert client.get(f"/api/uploads/{upload['id']}").status_code == 404
    assert client.get(f"/api/creators/{broken['id']}").status_code == 404
