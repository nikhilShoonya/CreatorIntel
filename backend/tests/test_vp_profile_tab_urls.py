"""Regression: profile-tab links (instagram.com/<user>/reels/, /<user>/tagged/) in a Channel Link / Username column
identify the reel owner again - e.g. the uploaded "testing (1).xlsx" whose Channel Link column used this shape."""

import pytest

from app.video_performance.ingest import parse_import
from app.video_performance.urls import parse_instagram_handle, parse_video_link


@pytest.mark.parametrize("value, owner", [
    # profile tabs (the regression)
    ("https://www.instagram.com/swamiss_/reels/", "swamiss_"),
    ("https://www.instagram.com/trading_bourg/reels", "trading_bourg"),
    ("instagram.com/dee_mindset/reels/", "dee_mindset"),
    ("https://www.instagram.com/Trade_With_Krishna02/REELS/", "trade_with_krishna02"),
    ("https://www.instagram.com/fin.pranjal/tagged/", "fin.pranjal"),
    ("https://www.instagram.com/swamiss_/reels/?igsh=abc123", "swamiss_"),
    # bare profile URLs and handles (unchanged)
    ("https://www.instagram.com/basic.gyaan", "basic.gyaan"),
    ("https://www.instagram.com/salonigandhi10/", "salonigandhi10"),
    ("@acute_facts_", "acute_facts_"),
    ("acute_facts_", "acute_facts_"),
    # username-bearing reel URL (unchanged)
    ("https://www.instagram.com/swamiss_/reel/Db0hmergU9p/", "swamiss_"),
])
def test_owner_is_read_from_profile_and_profile_tab_urls(value, owner):
    assert parse_instagram_handle(value) == owner


@pytest.mark.parametrize("value", [
    # reserved Instagram paths are never an owner
    "https://www.instagram.com/reels/Db0hmergU9p/",  # a reel link without owner, not a profile tab
    "https://www.instagram.com/reel/Db0hmergU9p/",
    "https://www.instagram.com/p/Db0hmergU9p/",
    "https://www.instagram.com/explore/tagged/",
    "https://www.instagram.com/reels/reels/",
    "https://www.instagram.com/stories/someone/",
    "https://www.instagram.com/accounts/login/",
    # other tabs / deeper paths are not accepted
    "https://www.instagram.com/swamiss_/followers/",
    "https://www.instagram.com/swamiss_/reels/Db0hmergU9p/extra/",  # deeper than any profile or media URL
    # security validations unchanged: host, scheme, credentials, port
    "https://evil.example.com/swamiss_/reels/",
    "https://instagram.com.evil.example/swamiss_/reels/",
    "ftp://www.instagram.com/swamiss_/reels/",
    "https://user:pass@www.instagram.com/swamiss_/reels/",
    "https://www.instagram.com:8443/swamiss_/reels/",
    # invalid usernames
    "https://www.instagram.com/bad..name/reels/",
    "https://www.instagram.com/.dotstart/reels/",
    "", "nan", "not a url at all",
])
def test_invalid_and_reserved_values_are_rejected(value):
    assert parse_instagram_handle(value) is None


def test_reel_links_still_parse_with_and_without_owner():
    plain = parse_video_link("https://www.instagram.com/reel/Db0hmergU9p/?igsh=MTVzZWE")
    assert (plain.platform, plain.identifier, plain.owner_username) == ("instagram", "Db0hmergU9p", None)
    reels = parse_video_link("https://www.instagram.com/reels/Db0hmergU9p/")
    assert (reels.identifier, reels.owner_username) == ("Db0hmergU9p", None)
    owned = parse_video_link("https://www.instagram.com/swamiss_/reel/Db0hmergU9p/")
    assert (owned.identifier, owned.owner_username) == ("Db0hmergU9p", "swamiss_")


def test_channel_link_column_supplies_the_owner_like_testing_1_xlsx():
    """The exact shape of the earlier working upload: Channel Link (profile / Reels tab) + Video link."""
    csv = (
        "Channel Link,Video link\n"
        "https://www.instagram.com/swamiss_/reels/,https://www.instagram.com/reel/Db0hmergU9p/\n"
        "https://www.instagram.com/trading_bourg/reels/,https://www.instagram.com/reel/Dbs0SJVJR67/?igsh=cXMwN2M\n"
        "https://www.instagram.com/salonigandhi10/,https://www.instagram.com/reel/DbvD682TcWr/\n"
        "https://www.instagram.com/basic.gyaan,https://www.instagram.com/reel/DbqYa3eN8qc/\n"
    ).encode()
    rows = parse_import("testing.csv", csv, 10_000_000).rows
    assert [r.owner_username for r in rows] == ["swamiss_", "trading_bourg", "salonigandhi10", "basic.gyaan"]
    assert all(r.status != "invalid" for r in rows)
