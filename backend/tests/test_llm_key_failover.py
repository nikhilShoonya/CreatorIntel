"""Groq API key failover: next key only when the active key is unusable; never to get around rate/daily limits."""

import asyncio
import json
import logging

import httpx
import pytest

from app.agents.content_analysis_agent import ContentAnalyzer
from app.config.settings import get_settings
from app.services import llm_client
from app.services.llm_client import KEY_COOLDOWN_SECONDS, LLMClient, LLMError
from tests.fakes import mock_client

KEY1, KEY2, KEY3 = "gsk_test_key_one_SECRET", "gsk_test_key_two_SECRET", "gsk_test_key_three_SECRET"
MODEL = "openai/gpt-oss-20b"
OK = {"genre": "Finance & Investment", "sub_genre": "Stock Market & Trading", "language": "Hinglish",
      "secondary_language": "None", "sentiment": "Neutral", "sentiment_score": 0.1, "genre_confidence": 0.9,
      "language_confidence": 0.9, "sentiment_confidence": 0.9, "evidence_topics": ["Nifty"]}
SAMPLE = {"platform": "youtube", "display_name": "X", "bio": "", "items": [{"title": "Market ka next move kya hoga?"}], "tags": []}


@pytest.fixture(autouse=True)
def fresh_state():
    llm_client.reset_budgets()
    yield
    llm_client.reset_budgets()


@pytest.fixture
def clock(monkeypatch):
    state = {"t": 1000.0, "waits": []}

    async def fake_sleep(seconds):
        state["waits"].append(seconds)
        state["t"] += seconds

    monkeypatch.setattr(llm_client, "_monotonic", lambda: state["t"])
    monkeypatch.setattr(llm_client, "_now", lambda: 1_700_000_000 + state["t"])
    monkeypatch.setattr(llm_client, "_sleep", fake_sleep)
    return state


def settings(**overrides):
    return get_settings().model_copy(update={
        "groq_api_key": "", "groq_api_key_1": KEY1, "groq_api_key_2": KEY2, "groq_api_key_3": KEY3,
        "groq_model": MODEL, "groq_fallback_models": "", **overrides})


class Groq:
    """Fake Groq API: per-key behaviour, records (key, model, body) of every request."""

    def __init__(self, **behaviour):
        self.behaviour = behaviour  # key -> callable(attempt) -> httpx.Response
        self.calls: list[tuple[str, str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = request.headers["authorization"].removeprefix("Bearer ")
        body = json.loads(request.content)
        self.calls.append((key, body["model"], json.dumps(body["messages"])))
        attempt = sum(1 for k, _, _ in self.calls if k == key)
        handler = self.behaviour.get(key)
        return handler(attempt) if handler else ok()

    def keys(self):
        return [k for k, _, _ in self.calls]


def ok():
    return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"x": 1})}}], "usage": {"total_tokens": 300}})


def unauthorized(_attempt=1):
    return httpx.Response(401, json={"error": {"message": "Invalid API Key", "type": "invalid_request_error", "code": "invalid_api_key"}})


def restricted(_attempt=1):
    return httpx.Response(403, json={"error": {"message": "Organization has been restricted.", "type": "permission_error"}})


def limit(scope, wait):
    return lambda _attempt=1: httpx.Response(429, json={"error": {"code": "rate_limit_exceeded", "message":
        f"Rate limit reached for model `{MODEL}` on tokens {scope}: Limit 8000, Used 7990. Please try again in {wait}."}})


def run(client: LLMClient, user="u"):
    return asyncio.run(client.complete(system="s", user=user, schema_name="n", schema={"type": "object"}))


def test_keys_are_read_in_order_with_single_key_fallback():
    cfg = get_settings()
    assert cfg.model_copy(update={"groq_api_key_1": "a", "groq_api_key_2": "", "groq_api_key_3": "c"}).groq_api_keys == ["a", "c"]
    assert cfg.model_copy(update={"groq_api_key_1": "a", "groq_api_key_2": "a"}).groq_api_keys == ["a"]  # duplicates once
    assert cfg.model_copy(update={"groq_api_key": "single", "groq_api_key_1": "", "groq_api_key_2": "",
                                  "groq_api_key_3": ""}).groq_api_keys == ["single"]  # existing GROQ_API_KEY still works
    assert cfg.model_copy(update={"groq_api_key": "", "groq_api_key_1": "", "groq_api_key_2": "",
                                  "groq_api_key_3": ""}).ai_configured is False


def test_invalid_key_fails_over_to_next_key_with_same_model_and_request(clock):
    groq = Groq(**{KEY1: unauthorized})
    client = LLMClient(settings(), client=mock_client(groq))
    result = run(client, user="identical request")
    assert result.model == MODEL
    assert groq.keys() == [KEY1, KEY2]
    assert groq.calls[0][1:] == groq.calls[1][1:]  # same model, same messages
    run(client)
    assert groq.keys() == [KEY1, KEY2, KEY2]  # the unusable key is skipped while it cools down


def test_restricted_account_fails_over_too(clock):
    groq = Groq(**{KEY1: restricted, KEY2: unauthorized})
    run(LLMClient(settings(), client=mock_client(groq)))
    assert groq.keys() == [KEY1, KEY2, KEY3]


def test_key_recovers_after_cooldown(clock):
    state = {"broken": True}
    groq = Groq(**{KEY1: lambda _a: unauthorized() if state["broken"] else ok()})
    client = LLMClient(settings(), client=mock_client(groq))
    run(client)
    assert groq.keys() == [KEY1, KEY2]
    state["broken"] = False  # e.g. the key was re-enabled on its account
    clock["t"] += KEY_COOLDOWN_SECONDS + 1
    run(client)
    assert groq.keys() == [KEY1, KEY2, KEY1]  # back to the first key in order


def test_minute_limit_waits_on_the_same_key_never_switches(clock):
    groq = Groq(**{KEY1: lambda attempt: limit("per minute (TPM)", "7.5s")() if attempt == 1 else ok()})
    run(LLMClient(settings(), client=mock_client(groq)))
    assert groq.keys() == [KEY1, KEY1]
    assert any(w == pytest.approx(7.5) for w in clock["waits"])  # Retry-After respected


def test_daily_limit_marks_pending_and_does_not_use_other_keys(clock):
    groq = Groq(**{KEY1: limit("per day (TPD)", "6h0m0s")})
    client = LLMClient(settings(), client=mock_client(groq))
    with pytest.raises(LLMError) as caught:
        run(client)
    assert caught.value.quota and not caught.value.retryable and "will run again automatically" in caught.value.message
    assert groq.keys() == [KEY1]  # KEY2/KEY3 are NOT used to get around the limit
    assert client.quota_available() is False
    with pytest.raises(LLMError):
        run(client)
    assert groq.keys() == [KEY1]  # no new request while exhausted - no retry loop


def test_server_errors_do_not_switch_keys(clock):
    groq = Groq(**{KEY1: lambda _a: httpx.Response(503, json={"error": {"message": "Service Unavailable"}})})
    with pytest.raises(LLMError) as caught:
        run(LLMClient(settings(), client=mock_client(groq)))
    assert caught.value.retryable and not caught.value.quota
    assert groq.keys() == [KEY1]


def test_all_keys_unusable_is_a_graceful_pending_error_without_loops(clock):
    groq = Groq(**{KEY1: unauthorized, KEY2: restricted, KEY3: unauthorized})
    client = LLMClient(settings(), client=mock_client(groq))
    with pytest.raises(LLMError) as caught:
        run(client)
    assert caught.value.quota and caught.value.retry_at is not None
    assert "No usable Groq API key" in caught.value.message
    assert groq.keys() == [KEY1, KEY2, KEY3]  # each key once
    with pytest.raises(LLMError):
        run(client)
    assert len(groq.calls) == 3  # nothing more until a key's cooldown ends

    # the analysis is marked pending (retried automatically later), not failed
    cfg = settings()
    outcome = asyncio.run(ContentAnalyzer(LLMClient(cfg, client=mock_client(groq)), settings=cfg).analyze(SAMPLE))
    assert outcome.pending and outcome.error.startswith("AI analysis pending")


def test_keys_never_appear_in_logs_or_errors(clock, caplog):
    caplog.set_level(logging.DEBUG)
    groq = Groq(**{KEY1: unauthorized, KEY2: restricted, KEY3: limit("per day (TPD)", "3h0m0s")})
    client = LLMClient(settings(), client=mock_client(groq))
    with pytest.raises(LLMError) as caught:
        run(client)
    text = caplog.text + caught.value.message
    assert "#1" in caplog.text and "#2" in caplog.text  # keys are identified by position only
    for key in (KEY1, KEY2, KEY3):
        assert key not in text and key[-6:] not in text


# ------------------------------------------------------------------ Settings page: usage bar + key states
def test_groq_usage_summary_counts_tokens_and_shows_key_states(clock):
    from fastapi.testclient import TestClient

    from app.main import app
    from app.services import api_usage
    from app.services.groq_usage import groq_usage

    with TestClient(app):
        cfg = settings(groq_tokens_per_day=1000)
        api_usage.flush()
        groq = Groq(**{KEY1: unauthorized})
        run(LLMClient(cfg, client=mock_client(groq)))  # key 1 unusable -> key 2 answers (300 tokens)
        usage = groq_usage(cfg)
        assert usage.configured and usage.model == MODEL and usage.active_slot == 2
        assert usage.tokens_today >= 300 and usage.percent == round(min(usage.tokens_today / 1000 * 100, 100), 1)
        assert [(k.slot, k.state) for k in usage.keys] == [(1, "unusable"), (2, "active"), (3, "standby")]
        assert "invalid or revoked" in usage.keys[0].message and usage.keys[0].until is not None
        assert not usage.daily_limit_reached and usage.requests_source == "counted"

        # Groq's own daily request counter is used once it has been reported
        api_usage.record_groq_headers(KEY2, {"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "990"})
        usage = groq_usage(cfg)
        assert (usage.requests_today, usage.requests_per_day, usage.requests_source) == (10, 1000, "groq")

        # daily limit on the active key -> bar full, reset time, other keys untouched
        groq2 = Groq(**{KEY2: limit("per day (TPD)", "4h0m0s")})
        with pytest.raises(LLMError):
            run(LLMClient(cfg, client=mock_client(groq2)))
        usage = groq_usage(cfg)
        assert usage.daily_limit_reached and usage.percent == 100.0 and usage.resets_at is not None
        assert [(k.slot, k.state) for k in usage.keys] == [(1, "unusable"), (2, "daily_limit"), (3, "standby")]
        assert all(key not in usage.model_dump_json() for key in (KEY1, KEY2, KEY3))
