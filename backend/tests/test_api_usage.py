"""YouTube quota usage counting (shared by both modules' YouTube clients)."""

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
