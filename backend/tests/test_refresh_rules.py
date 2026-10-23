"""Refresh rules: uploads reuse everything already known (no platform / AI calls), only new creators and
videos are fetched; Refresh Profile re-fetches one creator's platform data without the AI; no automatic
daily metrics refresh."""

import asyncio
import time
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.content_analysis_agent import ContentAnalyzer
from app.agents.instagram_agent import InstagramCollector
from app.agents.youtube_agent import YouTubeCollector
from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.models.entities import Creator, CreatorStatus, UploadItem, UploadRow
from app.schemas.platform import ChannelProfile, ContentItem
from app.services.llm_client import LLMClient
from app.services.orchestrator import EnrichmentOrchestrator
from app.utils.time import as_utc, utcnow
from app.video_performance import repository as vt_repo
from app.video_performance.models import VideoStatus, VtVideo
from tests.fakes import instagram_handler, llm_handler, mock_client, youtube_handler

OLD = utcnow() - timedelta(days=60)  # "old" data must still be reused


class Calls:
    def __init__(self):
        self.urls: list[str] = []

    def wrap(self, handler):
        def recorded(request: httpx.Request):
            self.urls.append(str(request.url))
            return handler(request)
        return recorded


@pytest.fixture(scope="module")
def calls():
    return {"youtube": Calls(), "instagram": Calls(), "groq": Calls()}


@pytest.fixture(scope="module")
def api(calls):
    with TestClient(app) as client:
        settings = get_settings()
        app.state.orchestrator = EnrichmentOrchestrator(
            settings,
            youtube=YouTubeCollector(settings, client=mock_client(calls["youtube"].wrap(youtube_handler))),
            instagram=InstagramCollector(settings, client=mock_client(calls["instagram"].wrap(instagram_handler))),
            analyzer=ContentAnalyzer(LLMClient(settings, client=mock_client(calls["groq"].wrap(llm_handler))), settings=settings),
        )
        yield client


def _creator(name, platform, identifier, status, **fields) -> Creator:
    youtube = platform == "youtube"
    url = f"https://www.youtube.com/@{identifier}" if youtube else f"https://www.instagram.com/{identifier}/"
    # stored like an upload stores them: YouTube handles keep their "@"
    return Creator(channel_name=name, platform=platform, channel_url=url,
                   normalized_identifier=f"@{identifier}" if youtube else identifier,
                   identifier_type="handle" if youtube else "username", status=status, data_fetched_at=OLD, **fields)


KNOWN_IDS = ("reuse_ig_old", "reuse_ig_partial", "@reuseytfailed", "@brandnewchannel777")


@pytest.fixture
def existing(api):
    rows = [
        _creator("Old complete", "instagram", "reuse_ig_old", CreatorStatus.COMPLETED, followers_count=5_000,
                 genre="Finance & Investment", analyzed_at=OLD),
        _creator("Old partial", "instagram", "reuse_ig_partial", CreatorStatus.PARTIAL, error_message="AI analysis unavailable"),
        _creator("Old failed", "youtube", "reuseytfailed", CreatorStatus.FAILED, error_message="YouTube API quota exceeded"),
    ]
    with session_scope() as db:
        for old in db.query(Creator).filter(Creator.normalized_identifier.in_(KNOWN_IDS)):  # leftovers
            db.delete(old)
        db.flush()
        db.add_all(rows)
        db.flush()
        ids = [r.id for r in rows]
    yield ids
    with session_scope() as db:
        known = [c.id for c in db.query(Creator).filter(Creator.normalized_identifier.in_(KNOWN_IDS))]
        db.query(UploadItem).filter(UploadItem.creator_id.in_(known)).delete(synchronize_session=False)
        db.query(Creator).filter(Creator.id.in_(known)).delete(synchronize_session=False)


def _wait_upload(api, upload_id):
    deadline = time.time() + 15
    while time.time() < deadline and api.get(f"/api/uploads/{upload_id}").json()["status"] == "processing":
        time.sleep(0.05)


def test_upload_reuses_existing_creators_without_any_api_call(api, calls, existing):
    for c in calls.values():
        c.urls.clear()
    csv = (
        "Channel Name,Channel Link\n"
        "Old complete,https://www.instagram.com/reuse_ig_old/\n"
        "Old partial,https://www.instagram.com/reuse_ig_partial/\n"
        "Old failed,https://www.youtube.com/@reuseytfailed\n"
        "Old complete again,https://www.instagram.com/reuse_ig_old/\n"  # duplicate within the file
        "Brand new,https://www.youtube.com/@brandnewchannel777\n"
    ).encode()
    upload = api.post("/api/uploads", files={"file": ("reuse.csv", csv, "text/csv")}).json()
    _wait_upload(api, upload["id"])

    every_url = " ".join(u for c in calls.values() for u in c.urls)
    for identifier in ("reuse_ig_old", "reuse_ig_partial", "reuseytfailed"):
        assert identifier not in every_url  # zero platform calls for known creators, however old / failed
    assert "brandnewchannel777" in every_url  # the genuinely new creator is fetched
    assert calls["instagram"].urls == [] and calls["groq"].urls == []  # no Meta / AI calls at all here

    with session_scope() as db:
        for cid, status in zip(existing, (CreatorStatus.COMPLETED, CreatorStatus.PARTIAL, CreatorStatus.FAILED)):
            creator = db.get(Creator, cid)
            assert creator.status == status  # not retried
            assert abs((as_utc(creator.data_fetched_at) - OLD).total_seconds()) < 1  # stored data untouched
        rows = db.query(UploadRow).filter(UploadRow.upload_id == upload["id"]).order_by(UploadRow.row_number).all()
    outcomes = [(r.outcome, r.message or "") for r in rows]
    assert outcomes[0][0] == "cached" and "existing data reused" in outcomes[0][1]
    assert outcomes[1][0] == "cached" and "(Partial) - existing data reused, not retried" in outcomes[1][1]
    assert outcomes[2][0] == "cached" and "(Failed)" in outcomes[2][1]
    assert any(o == "duplicate" for o, _ in outcomes)  # de-duplication within the upload is kept
    assert sum(1 for o, _ in outcomes if o == "queued") == 1  # only the new creator was queued


# ------------------------------------------------------------------ Refresh Profile
class FakeCollector:
    def __init__(self):
        self.calls = 0

    async def collect(self, parsed):
        self.calls += 1
        items = [ContentItem(id=f"v{i}", url=f"https://www.youtube.com/watch?v=v{i}", published_at=None, is_video=True,
                             views=10_000, likes=300, comments=20, title=f"New video {i}") for i in range(5)]
        return ChannelProfile(platform="youtube", platform_id="UCprofilerefresh000000001", display_name="Refreshed",
                              bio="new bio", audience_count=77_000, account_access="public_channel", items=items, source="t")


class NoAI:
    async def analyze(self, *_args, **_kwargs):
        raise AssertionError("Refresh Profile must not run the AI")


def _seed_profile_creator(with_ai: bool) -> int:
    with session_scope() as db:
        for old in db.query(Creator).filter(Creator.normalized_identifier == "@profilerefresh1"):
            db.delete(old)
        db.flush()
        creator = _creator("Profile refresh", "youtube", "profilerefresh1", CreatorStatus.COMPLETED, subscriber_count=1,
                           genre="Finance & Investment" if with_ai else None, language="English" if with_ai else None,
                           sentiment="Neutral" if with_ai else None, analyzed_at=OLD if with_ai else None)
        db.add(creator)
        db.flush()
        return creator.id


@pytest.mark.parametrize("with_ai", [True, False])
def test_refresh_profile_fetches_platform_data_only(api, with_ai):
    creator_id = _seed_profile_creator(with_ai)
    collector = FakeCollector()
    orchestrator = EnrichmentOrchestrator(get_settings(), youtube=collector, instagram=collector, analyzer=NoAI())
    try:
        asyncio.run(orchestrator.enrich_creator(creator_id, profile_only=True))
        with session_scope() as db:
            creator = db.get(Creator, creator_id)
            assert collector.calls == 1 and creator.subscriber_count == 77_000  # platform data updated
            assert creator.average_views == 10_000
            if with_ai:  # stored AI analysis kept as it was
                assert (creator.genre, creator.language, creator.sentiment) == ("Finance & Investment", "English", "Neutral")
                assert abs((as_utc(creator.analyzed_at) - OLD).total_seconds()) < 1
                assert creator.status == CreatorStatus.COMPLETED
            else:  # nothing to keep: partial success, AI left to Re-analyze
                assert creator.genre is None and creator.status == CreatorStatus.PARTIAL
                assert "use Re-analyze" in (creator.error_message or "")
    finally:
        with session_scope() as db:
            db.delete(db.get(Creator, creator_id))


def test_refresh_profile_endpoint_queues_only_that_creator(api, monkeypatch):
    creator_id = _seed_profile_creator(True)
    started: list[int] = []
    monkeypatch.setattr(app.state.orchestrator, "start_profile_refresh", started.append)
    monkeypatch.setattr(app.state.orchestrator, "start_retry", lambda cid: pytest.fail("must not run the full pipeline"))
    try:
        response = api.post(f"/api/creators/{creator_id}/refresh-profile")
        assert response.status_code == 202 and response.json()["message"] == "Refreshing profile data"
        assert started == [creator_id]
        assert api.post(f"/api/creators/{creator_id}/refresh-profile").status_code == 409  # already queued
    finally:
        with session_scope() as db:
            db.delete(db.get(Creator, creator_id))


# ------------------------------------------------------------------ Video Performance
def test_video_upload_fetches_only_new_videos(api, monkeypatch):
    with session_scope() as db:
        for vid in db.query(VtVideo).filter(VtVideo.video_identifier.in_(("reuseOld001", "reuseNew001"))):
            db.delete(vid)
        db.flush()
        old = VtVideo(platform="youtube", video_identifier="reuseOld001", video_url="https://youtu.be/reuseOld001",
                      status=VideoStatus.FAILED, current_views=None, last_checked_at=OLD)
        db.add(old)
        db.flush()
        old_id = old.id
    started: list[list[int]] = []
    monkeypatch.setattr(app.state.video_tracker, "start_processing", lambda ids: started.append(list(ids)))
    csv = b"Video Link\nhttps://youtu.be/reuseOld001\nhttps://youtu.be/reuseNew001\n"
    result = api.post("/api/video-performance/uploads", files={"file": ("vids.csv", csv, "text/csv")}).json()
    try:
        assert [r["status"] for r in result["rows"]] == ["already_tracked", "added"]
        with session_scope() as db:
            new_id = db.query(VtVideo).filter(VtVideo.video_identifier == "reuseNew001").one().id
            old = db.get(VtVideo, old_id)
            assert old.status == VideoStatus.FAILED and old.current_views is None  # old / incomplete data kept as is
        assert started == [[new_id]]  # only the new video is fetched

        started.clear()
        assert api.post(f"/api/video-performance/videos/{old_id}/refresh").status_code == 202
        assert started == [[old_id]]  # the per-video Refresh still works, for that video only
    finally:
        vt_repo.delete_videos([v for v in (old_id,)])
        with session_scope() as db:
            for vid in db.query(VtVideo).filter(VtVideo.video_identifier == "reuseNew001"):
                db.delete(vid)
        api.delete(f"/api/video-performance/uploads/{result['upload']['id']}")
