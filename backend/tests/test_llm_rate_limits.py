"""Groq quota handling: pacing, per-minute waits, daily-limit fallback, "AI pending", batching, skipping unchanged content."""

import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.content_analysis_agent import ContentAnalyzer
from app.agents.instagram_agent import InstagramCollector
from app.agents.youtube_agent import YouTubeCollector
from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.services import llm_client
from app.services.llm_client import LLMClient, LLMError, _parse_wait
from app.services.orchestrator import EnrichmentOrchestrator
from app.video_performance import repository as repo
from app.video_performance.models import VtSentiment, VtVideo
from app.video_performance.sentiment import VideoSentimentAnalyzer
from app.video_performance.tracker import VideoTracker
from tests.fakes import instagram_handler, llm_handler, mock_client, sentiment_batch_response, youtube_handler

PRIMARY, FALLBACK = "test/primary-model", "test/fallback-model"
SAMPLE = {"platform": "youtube", "display_name": "X", "bio": "", "items": [{"title": "Market ka next move kya hoga?"}], "tags": []}
OK = {"genre": "Finance & Investment", "sub_genre": "Stock Market & Trading", "language": "Hinglish",
      "secondary_language": "None", "sentiment": "Neutral", "sentiment_score": 0.1, "genre_confidence": 0.9,
      "language_confidence": 0.9, "sentiment_confidence": 0.9, "evidence_topics": ["Nifty"]}


@pytest.fixture(autouse=True)
def fresh_budgets():
    llm_client.reset_budgets()
    yield
    llm_client.reset_budgets()


@pytest.fixture
def clock(monkeypatch):
    """Fake time: asyncio.sleep advances it instantly, so waits are measured without slowing the tests."""
    state = {"t": 1000.0, "waits": []}

    async def fake_sleep(seconds):
        state["waits"].append(seconds)
        state["t"] += seconds

    monkeypatch.setattr(llm_client, "_monotonic", lambda: state["t"])
    monkeypatch.setattr(llm_client, "_now", lambda: 1_700_000_000 + state["t"])
    monkeypatch.setattr(llm_client, "_sleep", fake_sleep)
    return state


def settings(**overrides):
    return get_settings().model_copy(update={"groq_model": PRIMARY, "groq_fallback_models": FALLBACK,
                                             "groq_requests_per_minute": 30, "groq_tokens_per_minute": 8000, **overrides})


def ok(payload=OK, tokens=900):
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}], "usage": {"total_tokens": tokens}})


def limit(scope: str, wait: str):
    return httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "type": "tokens", "message":
        f"Rate limit reached for model `x` on tokens {scope}: Limit 8000, Used 7900, Requested 900. Please try again in {wait}."}})


def run(client: LLMClient):
    return asyncio.run(client.complete(system="s", user="u", schema_name="n", schema={"type": "object"}))


def test_parse_wait():
    assert _parse_wait("Please try again in 7m12.5s. Visit ...") == pytest.approx(432.5)
    assert _parse_wait("try again in 12.7s") == pytest.approx(12.7)
    assert _parse_wait("try again in 1h2m") == pytest.approx(3720)
    assert _parse_wait("try again in 250ms") == pytest.approx(0.25)
    assert _parse_wait("17") == 17.0 and _parse_wait(None) is None


def test_minute_limit_waits_instead_of_retry_storm(clock):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content)["model"])
        return limit("per minute (TPM)", "12.7s") if len(calls) == 1 else ok()

    result = run(LLMClient(settings(), client=mock_client(handler)))
    assert result.model == PRIMARY and calls == [PRIMARY, PRIMARY]  # exactly one retry, after waiting
    assert any(w == pytest.approx(12.7) for w in clock["waits"])


def test_daily_limit_switches_to_fallback_model_and_remembers(clock):
    calls = []

    def handler(request):
        model = json.loads(request.content)["model"]
        calls.append(model)
        return limit("per day (TPD)", "7m12.5s") if model == PRIMARY else ok()

    client = LLMClient(settings(), client=mock_client(handler))
    assert run(client).model == FALLBACK
    assert run(client).model == FALLBACK
    assert calls == [PRIMARY, FALLBACK, FALLBACK]  # the exhausted model is not asked again until its reset


def test_all_models_exhausted_is_a_quota_error_and_stops_calling(clock):
    calls = []

    def handler(request):
        calls.append(1)
        return limit("per day (RPD)", "3h0m0s")

    client = LLMClient(settings(), client=mock_client(handler))
    with pytest.raises(LLMError) as caught:
        run(client)
    assert caught.value.quota and not caught.value.retryable and caught.value.retry_at is not None
    assert "will run again automatically" in caught.value.message
    assert client.quota_available() is False
    with pytest.raises(LLMError):
        run(client)
    assert len(calls) == 2  # one per model; nothing more while exhausted


def test_requests_are_paced_below_the_minute_limits(clock):
    client = LLMClient(settings(groq_requests_per_minute=3), client=mock_client(lambda _r: ok(tokens=100)))
    for _ in range(3):
        run(client)
    assert sum(clock["waits"]) > 0  # 90% of 3/min = 2 immediately, the third waits for the window
    assert all(w <= 65 for w in clock["waits"])


def test_creator_analysis_marks_quota_as_pending_without_retrying(clock):
    calls = []

    def handler(request):
        calls.append(1)
        return limit("per day (TPD)", "5h0m0s")

    cfg = settings(ai_max_retries=2)
    outcome = asyncio.run(ContentAnalyzer(LLMClient(cfg, client=mock_client(handler)), settings=cfg).analyze(SAMPLE))
    assert outcome.pending and outcome.result is None and outcome.error.startswith("AI analysis pending")
    assert len(calls) == 2  # primary + fallback once each - no 3x analysis retries on top


def test_video_sentiment_is_batched_and_pending_is_not_saved(clock):
    requests = []

    def handler(request):
        requests.append(request)
        return sentiment_batch_response(request, "Positive", 0.9)

    cfg = settings(ai_sentiment_batch_size=10)
    analyzer = VideoSentimentAnalyzer(cfg, LLMClient(cfg, client=mock_client(handler)))
    videos = [(i, f"Video {i} title about markets", "Detailed caption text") for i in range(1, 13)] + [(99, "hi", None)]
    results = asyncio.run(analyzer.analyze_many(videos))
    assert len(requests) == 2  # 12 videos in 2 requests (10 + 2); the too-short one is never sent
    assert all(results[i].sentiment == "Positive" and results[i].model == f"groq:{PRIMARY}" for i in range(1, 13))
    assert results[99].error == "Not enough text content to analyse" and not results[99].pending

    # a batch answer that misses a video leaves that one pending (retried next run, nothing invented)
    def partial(request):
        response = json.loads(sentiment_batch_response(request).content)
        content = json.loads(response["choices"][0]["message"]["content"])
        content["results"] = content["results"][:1]
        return ok(content)

    analyzer = VideoSentimentAnalyzer(cfg, LLMClient(cfg, client=mock_client(partial)))
    out = asyncio.run(analyzer.analyze_many([(1, "First video title here", None), (2, "Second video title here", None)]))
    assert out[1].sentiment == "Positive" and out[2].pending and out[2].sentiment is None

    # daily quota -> pending; the tracker does not save a sentiment row, so the next run analyses it
    with TestClient(app), session_scope() as db:  # app startup creates the tables
        video = VtVideo(platform="youtube", video_identifier="quotaVid001", video_url="https://youtu.be/quotaVid001",
                        title="Bank Nifty outlook for the week ahead", status="Tracking")
        db.add(video)
        db.flush()
        video_id = video.id
    exhausted = LLMClient(cfg, client=mock_client(lambda _r: limit("per day (TPD)", "2h0m0s")))
    tracker = VideoTracker(cfg, sentiment=VideoSentimentAnalyzer(cfg, exhausted))
    asyncio.run(tracker._analyse([video_id]))
    with session_scope() as db:
        assert db.query(VtSentiment).filter(VtSentiment.video_id == video_id).count() == 0
    assert repo.video_contents([video_id])[video_id][0] == "Bank Nifty outlook for the week ahead"
    with session_scope() as db:  # leave the shared test database as it was
        db.delete(db.get(VtVideo, video_id))


def test_refresh_with_unchanged_content_reuses_ai_result():
    calls = []

    def counting_llm(request):
        calls.append(1)
        return llm_handler(request)

    with TestClient(app) as client:
        cfg = get_settings()
        app.state.orchestrator = EnrichmentOrchestrator(
            cfg, youtube=YouTubeCollector(cfg, client=mock_client(youtube_handler)),
            instagram=InstagramCollector(cfg, client=mock_client(instagram_handler)),
            analyzer=ContentAnalyzer(LLMClient(cfg, client=mock_client(counting_llm)), settings=cfg),
        )
        for creator in client.get("/api/creators", params={"page_size": 100}).json()["items"]:
            client.delete(f"/api/creators/{creator['id']}")
        created = client.post("/api/creators", json={"channel_name": "Nitin", "channel_link": "https://youtube.com/@nitinnitro"})
        assert created.status_code == 201, created.text
        creator_id = created.json()["id"]

        def final():
            deadline = time.time() + 10
            while time.time() < deadline:
                data = client.get(f"/api/creators/{creator_id}").json()
                if data["status"] not in ("Pending", "Processing"):
                    return data
                time.sleep(0.05)
            raise AssertionError("creator did not finish")

        first = final()
        assert first["genre"] == "Finance & Investment" and len(calls) == 1
        assert client.post(f"/api/creators/{creator_id}/retry").status_code == 202  # "Refresh Data"
        time.sleep(0.1)
        second = final()
        assert len(calls) == 1  # same content -> no new Groq request
        assert (second["genre"], second["language"], second["sentiment"]) == (first["genre"], first["language"], first["sentiment"])

        # pending creators are found by the background backlog
        with session_scope() as db:
            from app.models.entities import Creator
            creator = db.get(Creator, creator_id)
            creator.analyzed_at = None
            creator.issues = ["AI analysis pending: Groq daily limit reached - it will run again automatically after 10 Oct 05:30"]
        assert creator_id in EnrichmentOrchestrator._pending_ai_creators(10)
        client.delete(f"/api/creators/{creator_id}")
