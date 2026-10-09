"""API usage counting: YouTube quota (both modules) and Instagram calls + Meta rate-limit headers."""

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from app.agents.youtube_agent import YouTubeCollector
from app.config.settings import Settings, get_settings
from app.main import app
from app.services import api_usage
from app.utils.url_parser import parse_channel_link
from tests.fakes import mock_client, youtube_handler


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _key_usage(fingerprint: str) -> api_usage.KeyUsage | None:
    settings = get_settings().model_copy(update={"youtube_api_key": "test-youtube-key,second-key"})
    quota = api_usage.youtube_quota(settings)
    return next((k for k in quota.keys if k.fingerprint == fingerprint), None)


def test_calls_are_counted_and_flushed(client):
    fp = api_usage.key_fingerprint("test-youtube-key")
    api_usage.flush()
    before = _key_usage(fp).units
    for _ in range(3):
        api_usage.record_youtube_call("test-youtube-key")
    assert _key_usage(fp).units == before + 3  # pending (not yet flushed) counts are included
    api_usage.flush()
    assert _key_usage(fp).units == before + 3  # and survive the flush exactly once
    api_usage.record_youtube_quota_exceeded("test-youtube-key")
    api_usage.flush()
    assert _key_usage(fp).quota_exceeded is True
    assert _key_usage(api_usage.key_fingerprint("second-key")).units == 0


def test_collector_requests_and_retries_cost_quota(client):
    fp = api_usage.key_fingerprint("test-youtube-key")
    api_usage.flush()
    before = _key_usage(fp).units
    asyncio.run(YouTubeCollector(Settings(), client=mock_client(youtube_handler)).collect(
        parse_channel_link("https://www.youtube.com/@nitinnitro")
    ))
    assert _key_usage(fp).units == before + 3  # channels + playlistItems + videos

    attempts = []

    def flaky(request):
        attempts.append(1)
        return httpx.Response(503) if len(attempts) == 1 else youtube_handler(request)

    before = _key_usage(fp).units
    asyncio.run(YouTubeCollector(Settings(), client=mock_client(flaky)).collect(
        parse_channel_link("https://www.youtube.com/@nitinnitro")
    ))
    assert _key_usage(fp).units == before + 4  # the failed attempt also costs a unit


def test_quota_endpoint_never_returns_keys(client):
    body = client.get("/api/config/youtube-quota").json()
    assert body["daily_limit_per_key"] == 10000 and body["keys"][0]["label"] == "Key 1"
    assert "test-youtube-key" not in str(body)


def test_combined_youtube_total(client):
    settings = get_settings().model_copy(update={"youtube_api_key": "test-youtube-key,second-key"})
    quota = api_usage.youtube_quota(settings)
    assert quota.key_count == 2 and quota.total_limit == 20000
    assert quota.total_units == sum(k.units for k in quota.keys)
    assert quota.percent == round(min(quota.total_units / 20000 * 100, 100), 1)


def test_instagram_calls_and_meta_usage_headers(client):
    api_usage.flush()
    before = api_usage.instagram_usage().calls_today
    response = httpx.Response(200, headers={
        "x-app-usage": '{"call_count":12,"total_cputime":3,"total_time":5}',
        "x-business-use-case-usage": '{"17841400000000000":[{"type":"instagram","call_count":41,'
                                     '"total_cputime":2,"total_time":4,"estimated_time_to_regain_access":0}]}',
    })
    api_usage.record_instagram_call()
    api_usage.record_meta_usage(response)
    usage = api_usage.instagram_usage()
    assert usage.calls_today == before + 1 and usage.percent == 41.0 and not usage.stale
    api_usage.flush()
    usage = api_usage.instagram_usage()  # read back from the database
    assert usage.calls_today == before + 1 and usage.percent == 41.0
    api_usage.record_meta_usage(httpx.Response(200, headers={"x-app-usage": "not json"}))  # ignored, no crash
    assert api_usage.instagram_usage().percent == 41.0


def test_usage_endpoint_never_returns_secrets(client):
    body = client.get("/api/config/api-usage").json()
    assert set(body) == {"youtube", "instagram", "groq"}
    assert body["youtube"]["total_limit"] == body["youtube"]["daily_limit_per_key"] * body["youtube"]["key_count"]
    assert "test-youtube-key" not in str(body) and "17841400000000000" not in str(body)
    assert "test-groq-key" not in str(body) and body["groq"]["keys"][0]["slot"] == 1
