"""Regression tests for the bug-fix round (duplicates, cache, header rows, formats, races, job locks, migrations)."""

import io
import time

import pandas as pd
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.agents.content_analysis_agent import ContentAnalyzer
from app.agents.ingestion_agent import FileIngestionAgent
from app.agents.instagram_agent import InstagramCollector
from app.agents.metrics_agent import MetricsProcessor
from app.agents.youtube_agent import YouTubeCollector
from app.config.settings import get_settings
from app.main import app
from app.models.db import Base, engine
from app.schemas.platform import ChannelProfile, ContentItem
from app.services.llm_client import LLMClient
from app.services.orchestrator import EnrichmentOrchestrator
from app.video_performance import repository as vt_repo
from app.video_performance.ingest import parse_import
from app.video_performance.urls import parse_video_link
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
        _clear(test_client)
        yield test_client
        _clear(test_client)  # leave no data behind for other test modules


def _clear(test_client):
    for upload in test_client.get("/api/uploads").json():
        test_client.delete(f"/api/uploads/{upload['id']}", params={"delete_creators": True})
    for creator in test_client.get("/api/creators", params={"page_size": 100}).json()["items"]:
        test_client.delete(f"/api/creators/{creator['id']}")


def _wait(client, upload_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/uploads/{upload_id}").json()
        if data["status"] != "processing":
            return data
        time.sleep(0.05)
    raise AssertionError("upload did not finish")


def test_same_channel_via_handle_and_channel_id_is_merged(client):
    csv = (
        "Channel Name,Channel Link\n"
        "Nitin Nitro,https://www.youtube.com/@nitinnitro\n"
        "Nitin (channel id),https://www.youtube.com/channel/UCaaaaaaaaaaaaaaaaaaaaaa\n"
    ).encode()
    upload = client.post("/api/uploads", files={"file": ("dup.csv", csv, "text/csv")}).json()
    assert upload["total_rows"] == 2  # different links -> two rows before the API resolves them
    done = _wait(client, upload["id"])
    assert done["total_rows"] == 1  # merged into one creator after resolution
    creators = client.get("/api/creators", params={"q": "nitin"}).json()
    assert creators["total"] == 1
    assert creators["items"][0]["subscriber_count"] == 890_000


def test_file_rows_saved_with_duplicates(client):
    csv = (
        "Channel Name,Channel Link\n"
        "Trading Tech,https://www.instagram.com/tradingtech31/\n"
        "Trading Tech again,instagram.com/tradingtech31/reels/\n"
        "Broken,not a link\n"
    ).encode()
    upload = client.post("/api/uploads", files={"file": ("rows.csv", csv, "text/csv")}).json()
    _wait(client, upload["id"])
    rows = client.get(f"/api/uploads/{upload['id']}/rows").json()["rows"]
    assert [(r["row_number"], r["outcome"]) for r in rows] == [(2, "queued"), (3, "duplicate"), (4, "invalid")]
    assert rows[1]["message"] == "Same channel as row 2"


def test_header_row_below_title_rows():
    frame = pd.DataFrame(
        [["Creator list - September", ""], ["", ""], ["Channel Name", "Channel Link"], ["Nitin", "https://youtube.com/@nitinnitro"]]
    )
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, header=False)
    result = FileIngestionAgent().ingest("titled.xlsx", buffer.getvalue())
    assert [r.identifier for r in result.rows] == ["nitinnitro"]
    assert result.rows[0].source_row == 4

    vp = parse_import("v.csv", b"My tracked videos\n\nCreator,Video Link\nNitin,https://youtu.be/aaaaaaaaaaa\n", 10_000_000)
    assert vp.rows[0].parsed.identifier == "aaaaaaaaaaa" and vp.rows[0].row == 4


def test_partial_results_are_not_reused_from_cache():
    from datetime import datetime, timezone

    from app.models.entities import Creator, CreatorStatus
    from app.services.upload_service import _is_fresh

    now = datetime.now(timezone.utc)
    assert _is_fresh(Creator(status=CreatorStatus.COMPLETED, data_fetched_at=now), 24) is True
    assert _is_fresh(Creator(status=CreatorStatus.PARTIAL, data_fetched_at=now), 24) is False


def test_youtube_average_split_long_and_short_form():
    items = [
        ContentItem(id=str(i), url=f"https://www.youtube.com/watch?v={i}", published_at=None, is_video=True,
                    views=1000 if i % 2 else 100_000, likes=1, comments=1, duration_seconds=600 if i % 2 else 40)
        for i in range(8)
    ]
    profile = ChannelProfile(platform="youtube", platform_id="x", display_name="X", bio="", audience_count=1,
                             account_access="public_channel", items=items, source="t")
    result = MetricsProcessor(10).compute(profile)
    assert result.average_views_long == 1000 and result.average_views_long_count == 4
    assert result.average_views_short == 100_000 and result.average_views_short_count == 4


def test_concurrent_duplicate_insert_becomes_conflict(monkeypatch):
    def boom(*_args, **_kwargs):
        raise IntegrityError("insert", {}, Exception("duplicate key"))

    monkeypatch.setattr(vt_repo, "_add_video", boom)
    with pytest.raises(vt_repo.Conflict):
        vt_repo.add_video(parse_video_link("https://youtu.be/aaaaaaaaaaa"), None, None, "manual")


def test_job_lock_is_exclusive(client):
    assert vt_repo.acquire_job_lock("test_job", "process-a") is True
    assert vt_repo.acquire_job_lock("test_job", "process-b") is False  # held by another process
    vt_repo.release_job_lock("test_job", "process-a")
    assert vt_repo.acquire_job_lock("test_job", "process-b") is True
    vt_repo.release_job_lock("test_job", "process-b")


def test_models_and_migrations_are_in_sync(client):
    with engine.connect() as connection:
        diff = compare_metadata(MigrationContext.configure(connection), Base.metadata)
    assert diff == [], f"Model changes without a migration: {diff}"
