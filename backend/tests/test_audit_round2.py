"""Fixes from the second code audit: YouTube key health, non-Latin handles, VP paging / export, upload delete, Groq wait cap."""

import asyncio
import time
import weakref

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.youtube_agent import YouTubeCollector
from app.config.settings import Settings, get_settings
from app.main import app
from app.models.db import session_scope
from app.schemas.platform import CollectionError
from app.services import llm_client, youtube_keys
from app.services.api_usage import key_fingerprint
from app.services.llm_client import LLMError, LLMClient
from app.services.orchestrator import EnrichmentOrchestrator
from app.utils.url_parser import parse_channel_link
from app.video_performance.models import VideoStatus, VtVideo
from app.video_performance.platforms import PlatformError, YouTubeVideoClient
from tests.fakes import mock_client, youtube_handler

QUOTA = {"error": {"code": 403, "message": "quota", "errors": [{"reason": "quotaExceeded"}]}}
BAD_KEY = {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                     "errors": [{"reason": "badRequest"}], "details": [{"reason": "API_KEY_INVALID"}]}}
PRIVATE_PLAYLIST = {"error": {"code": 403, "message": "The playlist cannot be accessed.",
                              "errors": [{"reason": "playlistItemsNotAccessible"}]}}


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


def _keyed(bad: dict[str, dict], seen: list[str]):
    """YouTube fake that answers with an error for some keys and normally for the others."""
    def handler(request: httpx.Request):
        key = request.headers.get("X-Goog-Api-Key", "")
        seen.append(key)
        if key in bad:
            return httpx.Response(bad[key]["error"]["code"], json=bad[key])
        # the shared fake only knows the default test key; any working key gets the same answers
        request.headers["X-Goog-Api-Key"] = "test-youtube-key"
        return youtube_handler(request)
    return handler


def _collect(settings: Settings, handler):
    return asyncio.run(YouTubeCollector(settings, client=mock_client(handler))
                       .collect(parse_channel_link("https://www.youtube.com/@nitinnitro")))


# ------------------------------------------------------------------ YouTube keys
def test_quota_key_is_skipped_until_the_daily_reset_then_used_first_again():
    settings = Settings(youtube_api_key="key-one, test-youtube-key")
    seen: list[str] = []
    assert _collect(settings, _keyed({"key-one": QUOTA}, seen)).audience_count == 890_000
    assert seen.count("key-one") == 1  # one quota answer, then the second key for every call

    seen.clear()
    _collect(settings, _keyed({}, seen))
    assert "key-one" not in seen  # still skipped (quota resets at midnight Pacific)

    youtube_keys._skipped_until[key_fingerprint("key-one")] = time.time() - 1  # the daily reset has passed
    seen.clear()
    _collect(settings, _keyed({}, seen))
    assert set(seen) == {"key-one"}  # back to the first key


def test_invalid_key_is_skipped_for_an_hour():
    settings = Settings(youtube_api_key="broken-key, test-youtube-key")
    seen: list[str] = []
    _collect(settings, _keyed({"broken-key": BAD_KEY}, seen))
    until = youtube_keys._skipped_until[key_fingerprint("broken-key")]
    assert 3500 < until - time.time() <= youtube_keys.INVALID_KEY_RETRY_SECONDS
    assert youtube_keys.usable(settings.youtube_api_keys) == ["test-youtube-key"]


def test_private_playlist_403_is_not_a_key_error():
    seen: list[str] = []

    def handler(request: httpx.Request):
        seen.append(request.headers.get("X-Goog-Api-Key", ""))
        if request.url.path.endswith("/playlistItems"):
            return httpx.Response(403, json=PRIVATE_PLAYLIST)
        return youtube_handler(request)

    settings = Settings(youtube_api_key="test-youtube-key, second-key")
    profile = _collect(settings, handler)
    assert profile.items == []  # uploads not accessible -> no recent videos, not an error
    assert "second-key" not in seen and youtube_keys.usable(settings.youtube_api_keys) == settings.youtube_api_keys


def test_resource_403_is_reported_as_not_accessible():
    def handler(request: httpx.Request):
        return httpx.Response(403, json={"error": {"code": 403, "message": "Channel is closed", "errors": [{"reason": "forbidden"}]}})

    with pytest.raises(CollectionError) as err:
        _collect(Settings(youtube_api_key="test-youtube-key"), handler)
    assert err.value.code == CollectionError.NOT_ACCESSIBLE
    assert youtube_keys.usable(["test-youtube-key"]) == ["test-youtube-key"]


def test_video_performance_client_shares_key_health():
    seen: list[str] = []
    settings = get_settings().model_copy(update={"youtube_api_key": "key-one,test-youtube-key"})
    client = YouTubeVideoClient(settings, client=mock_client(_keyed({"key-one": QUOTA}, seen)))
    asyncio.run(client.videos(["vid00"]))
    assert seen == ["key-one", "test-youtube-key"]
    seen.clear()
    asyncio.run(YouTubeVideoClient(settings, client=mock_client(_keyed({}, seen))).videos(["vid00"]))
    assert seen == ["test-youtube-key"]  # a new client still skips the exhausted key

    youtube_keys.reset()
    with pytest.raises(PlatformError) as err:  # every key out of quota
        asyncio.run(YouTubeVideoClient(settings, client=mock_client(
            _keyed({"key-one": QUOTA, "test-youtube-key": QUOTA}, []))).videos(["vid00"]))
    assert err.value.code == PlatformError.RATE_LIMITED


# ------------------------------------------------------------------ handles
@pytest.mark.parametrize("handle", ["हिंदीचैनल", "日本語チャンネル", "café.creator", "a·b·c"])
def test_non_latin_youtube_handles_are_accepted(handle):
    parsed = parse_channel_link(f"https://www.youtube.com/@{handle}")
    assert parsed.is_supported and parsed.identifier_type == "handle" and parsed.identifier == handle.lower()


@pytest.mark.parametrize("handle", ["ab", "x" * 31, "bad handle", "semi;colon"])
def test_invalid_youtube_handles_are_rejected(handle):
    assert not parse_channel_link(f"https://www.youtube.com/@{handle}").is_supported


# ------------------------------------------------------------------ Video Performance list / export
def test_page_past_the_end_shows_the_last_page(api):
    with session_scope() as db:
        videos = [VtVideo(platform="youtube", video_identifier=f"pageEnd{n:04d}", video_url=f"https://youtu.be/pageEnd{n:04d}",
                          status=VideoStatus.TRACKING, creator_name="Paging Test Creator") for n in range(3)]
        db.add_all(videos)
        db.flush()
        ids = [v.id for v in videos]
    try:
        body = api.get("/api/video-performance/videos",
                       params={"creator": "Paging Test Creator", "page": 9999, "page_size": 10}).json()
        assert body["page"] == 1 and body["total"] == 3 and len(body["items"]) == 3
    finally:
        with session_scope() as db:
            for vid in ids:
                db.delete(db.get(VtVideo, vid))


def test_export_rejects_unknown_status(api):
    response = api.get("/api/video-performance/exports/csv", params={"status": "nonsense"})
    assert response.status_code == 422 and response.json()["detail"] == "Unknown status"


# ------------------------------------------------------------------ uploads
def test_cached_row_note_uses_the_configured_ttl(api, monkeypatch):
    from app.services import upload_service

    captured: list[str | None] = []
    original = upload_service.UploadRow

    def capture(**kwargs):
        captured.append(kwargs.get("message"))
        return original(**kwargs)

    monkeypatch.setattr(upload_service, "_is_fresh", lambda creator, ttl: True)
    monkeypatch.setattr(upload_service, "UploadRow", capture)
    csv = b"Channel Name,Channel Link\nNitin,https://www.youtube.com/@nitinnitro\n"
    api.post("/api/uploads", files={"file": ("ttl.csv", csv, "text/csv")})  # makes sure the creator exists
    settings = get_settings()
    monkeypatch.setattr(settings, "cache_ttl_hours", 6)
    api.post("/api/uploads", files={"file": ("ttl2.csv", csv, "text/csv")})
    assert "Recent data reused (fetched in the last 6 hours)" in captured


def test_upload_file_is_kept_when_the_delete_is_not_committed(api, monkeypatch):
    from app.api import uploads as uploads_api

    response = api.post("/api/uploads", files={"file": ("keep.csv", b"Channel Name,Channel Link\nBroken,not a link\n", "text/csv")})
    upload_id = response.json()["id"]
    for _ in range(100):
        if api.get(f"/api/uploads/{upload_id}").json()["status"] != "processing":
            break
        time.sleep(0.05)
    files_before = set(get_settings().upload_dir.iterdir())

    def failing_commit(self):
        raise RuntimeError("commit failed")

    monkeypatch.setattr(uploads_api.Session, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        api.delete(f"/api/uploads/{upload_id}")
    monkeypatch.undo()
    assert set(get_settings().upload_dir.iterdir()) == files_before  # file not removed for a failed delete

    assert api.delete(f"/api/uploads/{upload_id}").status_code == 200
    assert len(set(get_settings().upload_dir.iterdir())) == len(files_before) - 1


# ------------------------------------------------------------------ Groq wait cap
def test_one_request_never_waits_more_than_the_total_cap(monkeypatch):
    clock = {"t": 1_000_000.0}
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    monkeypatch.setattr(llm_client, "_sleep", fake_sleep)
    monkeypatch.setattr(llm_client, "_now", lambda: clock["t"])
    monkeypatch.setattr(llm_client, "_monotonic", lambda: clock["t"])

    def busy(_request):
        return httpx.Response(429, headers={"retry-after": "50"},
                              json={"error": {"message": "Rate limit reached ... on requests per minute (RPM). Please try again in 50s."}})

    settings = get_settings().model_copy(update={"groq_api_key": "wait-cap-key", "groq_api_key_1": "",
                                                 "groq_api_key_2": "", "groq_api_key_3": ""})
    client = LLMClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(busy)))
    with pytest.raises(LLMError) as err:
        asyncio.run(client.complete(system="s", user="u", schema_name="x", schema={"type": "object"}))
    assert err.value.quota and not err.value.retryable and "pending" in err.value.message
    assert sum(slept) <= llm_client.MAX_TOTAL_WAIT_SECONDS


# ------------------------------------------------------------------ orchestrator locks
def test_creator_locks_do_not_pile_up():
    orchestrator = EnrichmentOrchestrator.__new__(EnrichmentOrchestrator)
    orchestrator._locks = weakref.WeakValueDictionary()
    orchestrator._identity_locks = weakref.WeakValueDictionary()

    async def use():
        lock = orchestrator._lock_for(1)
        assert orchestrator._lock_for(1) is lock  # same lock while in use
        async with lock:
            pass

    asyncio.run(use())
    import gc

    gc.collect()
    assert len(orchestrator._locks) == 0
