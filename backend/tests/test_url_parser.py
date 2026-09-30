import pytest

from app.utils.url_parser import parse_channel_link


@pytest.mark.parametrize(
    "link, username",
    [
        ("https://www.instagram.com/tradingtech31/", "tradingtech31"),
        ("http://instagram.com/hariom_speaks_/reels/", "hariom_speaks_"),
        ("https://www.instagram.com/Fin.Pranjal/?igsh=abc123&utm_source=qr", "fin.pranjal"),
        ("instagram.com/cfpreetika", "cfpreetika"),
        ("https://m.instagram.com/logical4you", "logical4you"),
    ],
)
def test_instagram_profiles(link, username):
    parsed = parse_channel_link(link)
    assert parsed.platform == "instagram"
    assert parsed.identifier == username
    assert parsed.normalized_identifier == username
    assert parsed.canonical_url == f"https://www.instagram.com/{username}/"


@pytest.mark.parametrize(
    "link, id_type, identifier, key",
    [
        ("https://www.youtube.com/@nitinnitro", "handle", "nitinnitro", "@nitinnitro"),
        ("https://youtube.com/@NitinNitro/videos?si=xyz", "handle", "nitinnitro", "@nitinnitro"),
        ("https://www.youtube.com/channel/UC1234567890abcdefghijkl", "channel_id", "UC1234567890abcdefghijkl",
         "channel:UC1234567890abcdefghijkl"),
        ("https://www.youtube.com/c/SomeCreator", "custom", "somecreator", "c:somecreator"),
        ("https://www.youtube.com/user/OldName", "username", "oldname", "user:oldname"),
    ],
)
def test_youtube_channels(link, id_type, identifier, key):
    parsed = parse_channel_link(link)
    assert parsed.platform == "youtube"
    assert parsed.identifier_type == id_type
    assert parsed.identifier == identifier
    assert parsed.normalized_identifier == key


@pytest.mark.parametrize(
    "link, platform",
    [
        ("", "invalid"),
        ("not a url", "invalid"),
        ("javascript:alert(1)", "invalid"),
        ("https://www.instagram.com/p/Cxyz123/", "invalid"),
        ("https://www.youtube.com/watch?v=abc", "invalid"),
        ("https://youtu.be/abc", "invalid"),
        ("https://www.youtube.com/channel/notvalid", "invalid"),
        ("https://twitter.com/someone", "unsupported"),
        ("https://instagram.com.evil.example/user", "unsupported"),
    ],
)
def test_invalid_or_unsupported(link, platform):
    parsed = parse_channel_link(link)
    assert parsed.platform == platform
    assert parsed.error
    assert parsed.normalized_identifier is None


@pytest.mark.parametrize(
    "link",
    [
        "https://youtu.be/PhIHq0MLMF0",
        "https://youtu.be/PhIHq0MLMF0?si=tracking",
        "https://www.youtube.com/watch?v=PhIHq0MLMF0&t=30s",
        "https://www.youtube.com/shorts/PhIHq0MLMF0",
    ],
)
def test_youtube_video_links_resolve_later_to_channel(link):
    parsed = parse_channel_link(link)
    assert parsed.platform == "youtube"
    assert parsed.identifier_type == "video"
    assert parsed.identifier == "PhIHq0MLMF0"  # video IDs are case-sensitive
    assert parsed.normalized_identifier == "video:PhIHq0MLMF0"
