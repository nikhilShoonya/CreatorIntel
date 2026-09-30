import asyncio

import httpx
import pytest

from app.agents.instagram_agent import InstagramCollector
from app.agents.youtube_agent import YouTubeCollector
from app.config.settings import Settings
from app.schemas.platform import CollectionError
from app.utils.url_parser import parse_channel_link
from tests.fakes import instagram_handler, mock_client, youtube_handler


def run(coro):
    return asyncio.run(coro)


def test_youtube_collects_channel_uploads_and_video_stats():
    collector = YouTubeCollector(Settings(), client=mock_client(youtube_handler))
    profile = run(collector.collect(parse_channel_link("https://www.youtube.com/@NitinNitro")))
    assert profile.platform_id == "UCaaaaaaaaaaaaaaaaaaaaaa"
    assert profile.audience_count == 890_000
    assert len(profile.items) == 12
    assert profile.items[0].id == "vid00"  # newest first
    assert profile.items[0].url == "https://www.youtube.com/watch?v=vid00"


def test_youtube_unknown_channel_is_not_found():
    collector = YouTubeCollector(Settings(), client=mock_client(youtube_handler))
    with pytest.raises(CollectionError) as err:
        run(collector.collect(parse_channel_link("https://www.youtube.com/@doesnotexist")))
    assert err.value.code == CollectionError.NOT_FOUND


def test_youtube_quota_error_is_structured():
    def handler(_request):
        return httpx.Response(403, json={"error": {"code": 403, "errors": [{"reason": "quotaExceeded"}]}})

    collector = YouTubeCollector(Settings(), client=mock_client(handler))
    with pytest.raises(CollectionError) as err:
        run(collector.collect(parse_channel_link("https://www.youtube.com/@x_channel")))
    assert err.value.code == CollectionError.QUOTA_EXCEEDED


def test_youtube_not_configured():
    collector = YouTubeCollector(Settings(youtube_api_key=""), client=mock_client(youtube_handler))
    with pytest.raises(CollectionError) as err:
        run(collector.collect(parse_channel_link("https://www.youtube.com/@nitinnitro")))
    assert err.value.code == CollectionError.CONFIG_MISSING


def test_instagram_professional_account():
    collector = InstagramCollector(Settings(), client=mock_client(instagram_handler))
    profile = run(collector.collect(parse_channel_link("https://www.instagram.com/tradingtech31/")))
    assert profile.account_access == "professional_account"
    assert profile.audience_count == 1_200_000
    assert profile.items[0].views == 200_000
    assert profile.items[0].title == "Bank Nifty Analysis day 0"
    assert profile.items[0].url.startswith("https://www.instagram.com/reel/")


def test_instagram_consumer_account_is_reported_not_scraped():
    collector = InstagramCollector(Settings(), client=mock_client(instagram_handler))
    with pytest.raises(CollectionError) as err:
        run(collector.collect(parse_channel_link("https://www.instagram.com/some_personal_acct/")))
    assert err.value.code == CollectionError.NOT_ACCESSIBLE
    assert err.value.account_access == "consumer_or_unavailable"


def test_instagram_view_field_fallback():
    calls = []

    def handler(request):
        calls.append(request.url.params.get("fields"))
        if "view_count" in request.url.params.get("fields", ""):
            return httpx.Response(400, json={"error": {"code": 100, "message": "Tried accessing nonexisting field (view_count)"}})
        return instagram_handler(request)

    collector = InstagramCollector(Settings(), client=mock_client(handler))
    profile = run(collector.collect(parse_channel_link("https://www.instagram.com/tradingtech31/")))
    assert len(calls) == 2
    assert all(item.views is None for item in profile.items)
    assert any("View counts are not available" in note for note in profile.notes)


def test_youtube_switches_to_next_key_when_one_is_invalid():
    used = []

    def handler(request):
        used.append(request.headers["X-Goog-Api-Key"])
        if request.headers["X-Goog-Api-Key"] != "test-youtube-key":
            return httpx.Response(400, json={"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                                       "errors": [{"reason": "badRequest"}]}})
        return youtube_handler(request)

    settings = Settings(youtube_api_key="bad-key-1, test-youtube-key")
    assert settings.youtube_api_keys == ["bad-key-1", "test-youtube-key"]
    profile = run(YouTubeCollector(settings, client=mock_client(handler)).collect(parse_channel_link("https://www.youtube.com/@nitinnitro")))
    assert profile.audience_count == 890_000
    assert used[0] == "bad-key-1" and set(used[1:]) == {"test-youtube-key"}


def test_instagram_login_token_is_rejected_with_guidance():
    def handler(_request):
        raise AssertionError("no request should be made with an Instagram Login token")

    collector = InstagramCollector(Settings(meta_access_token="IGAAexample"), client=mock_client(handler))
    with pytest.raises(CollectionError) as err:
        run(collector.collect(parse_channel_link("https://www.instagram.com/tradingtech31/")))
    assert err.value.code == CollectionError.AUTH_ERROR
    assert "EAA" in err.value.message


def test_youtube_video_link_resolves_channel():
    calls = []

    def handler(request):
        path = request.url.path.rsplit("/", 1)[-1]
        calls.append((path, dict(request.url.params)))
        if path == "videos" and request.url.params.get("part") == "snippet":
            return httpx.Response(200, json={"items": [{"id": "PhIHq0MLMF0", "snippet": {"channelId": "UCaaaaaaaaaaaaaaaaaaaaaa"}}]})
        if path == "channels" and request.url.params.get("id") == "UCaaaaaaaaaaaaaaaaaaaaaa":
            return httpx.Response(200, json={"items": [{
                "id": "UCaaaaaaaaaaaaaaaaaaaaaa",
                "snippet": {"title": "Kranthi Vlogger", "description": "Vlogs", "customUrl": "@kranthivlogger"},
                "statistics": {"subscriberCount": "1500000"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UUaaaaaaaaaaaaaaaaaaaaaa"}},
            }]})
        return youtube_handler(request)

    collector = YouTubeCollector(Settings(), client=mock_client(handler))
    profile = run(collector.collect(parse_channel_link("https://youtu.be/PhIHq0MLMF0")))
    assert profile.audience_count == 1_500_000
    assert profile.resolved_channel_url == "https://www.youtube.com/@kranthivlogger"
    assert calls[0][0] == "videos" and calls[1][0] == "channels"
