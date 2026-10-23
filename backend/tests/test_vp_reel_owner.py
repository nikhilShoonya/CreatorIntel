"""Reels uploaded without their owner's username are found on Instagram accounts the app already knows
(exact shortcode match, official Business Discovery only)."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.video_performance import repository as repo
from app.video_performance import tracker as tracker_module
from app.video_performance.models import VideoStatus, VtVideo
from app.video_performance.platforms import InstagramMedia, PlatformError
from app.video_performance.tracker import VideoTracker


def media(code: str, views: int) -> InstagramMedia:
    return InstagramMedia(shortcode=code, url=f"https://www.instagram.com/reel/{code}/", is_video=True, views=views,
                          likes=10, comments=2, caption=f"caption {code}", published_at=None)


class FakeInstagram:
    """Business Discovery stand-in: accounts -> their latest media; records every account read."""

    def __init__(self, accounts: dict[str, list[InstagramMedia]], rate_limited: set[str] = frozenset()):
        self.accounts, self.rate_limited, self.calls = accounts, rate_limited, []

    async def profile_media(self, username: str, pages: int = 1, **_kwargs):
        budget = _kwargs.get("request_budget")
        if budget is not None:
            budget[0] -= 1
        self.calls.append(username)
        if username in self.rate_limited:
            raise PlatformError(PlatformError.RATE_LIMITED, "Instagram API rate limit reached, try again later")
        if username not in self.accounts:
            raise PlatformError(PlatformError.UNSUPPORTED, "Instagram data unavailable: not a Professional account")
        return {"username": username, "_scan_complete": True}, self.accounts[username]


class NoSentiment:
    """Sentiment is not part of these tests (and must not reach the network)."""

    model_name = "test"

    async def analyze_many(self, videos):
        list(videos)
        return {}


def make_tracker(instagram: FakeInstagram) -> VideoTracker:
    return VideoTracker(get_settings(), instagram=instagram, sentiment=NoSentiment())


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def reels(api):
    def reel(code, owner=None):
        return VtVideo(platform="instagram", video_identifier=code, video_url=f"https://www.instagram.com/reel/{code}/",
                       owner_username=owner, status=VideoStatus.PENDING, source="upload")

    rows = {
        "known": reel("OwnKnown001", "alpha_trader"),  # owner given: alpha is read anyway
        "free": reel("OwnFree0001"),  # no owner - it is among alpha's posts (no extra call)
        "other": reel("OwnOther001"),  # no owner - on another known account
        "collab": reel("OwnCollab01", "alpha_trader"),  # wrong owner given - really posted by beta
        "nowhere": reel("OwnNowhere1"),  # no owner, on no known account
    }
    with session_scope() as db:
        db.add_all(rows.values())
        db.flush()
        ids = {name: row.id for name, row in rows.items()}
    yield ids
    repo.delete_videos(list(ids.values()))


def _video(video_id: int) -> VtVideo:
    with session_scope() as db:
        video = db.get(VtVideo, video_id)
        db.expunge(video)
        return video


def test_unknown_owners_are_found_on_known_accounts(reels, monkeypatch):
    instagram = FakeInstagram({
        "alpha_trader": [media("OwnKnown001", 1_000), media("OwnFree0001", 2_000)],
        "beta_invest": [media("OwnOther001", 3_000), media("OwnCollab01", 4_000)],
        "gamma_money": [media("Unrelated01", 5)],
    })
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["alpha_trader", "personal_acct", "beta_invest", "gamma_money"])
    tracker = make_tracker(instagram)

    async def run():
        await tracker.process_videos(list(reels.values()))
        await tracker.shutdown()

    asyncio.run(run())

    # alpha read once (its own reels + the free match), the private account skipped, beta found both, then stop
    assert instagram.calls == ["alpha_trader", "personal_acct", "beta_invest", "gamma_money"]
    free, other, collab, nowhere = (_video(reels[k]) for k in ("free", "other", "collab", "nowhere"))
    assert (free.owner_username, free.current_views, free.status) == ("alpha_trader", 2_000, VideoStatus.TRACKING)
    assert (other.owner_username, other.current_views) == ("beta_invest", 3_000)
    assert (collab.owner_username, collab.current_views) == ("beta_invest", 4_000)  # wrong username corrected
    assert nowhere.status == VideoStatus.FAILED and nowhere.current_views is None
    assert "search incomplete" in nowhere.status_reason  # one account could not be read
    assert _video(reels["known"]).current_views == 1_000


def test_search_stops_when_meta_rate_limits(reels, monkeypatch):
    instagram = FakeInstagram({"alpha_trader": [media("OwnKnown001", 1)]}, rate_limited={"beta_invest"})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["beta_invest", "gamma_money", "delta"])
    tracker = make_tracker(instagram)

    async def run():
        await tracker.process_videos([reels["known"], reels["free"]])
        await tracker.shutdown()

    asyncio.run(run())
    assert instagram.calls == ["alpha_trader", "beta_invest"]  # nothing more after Meta refused
    free = _video(reels["free"])
    assert free.status == VideoStatus.PENDING and "finding this reel's owner continues automatically" in free.status_reason
    assert reels["free"] in repo.meta_waiting_ids()  # continued by the Meta queue, even after a restart


def test_owner_search_waits_when_meta_refused_earlier_in_the_run(reels, monkeypatch):
    instagram = FakeInstagram({}, rate_limited={"alpha_trader"})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["beta_invest"])
    tracker = make_tracker(instagram)

    async def run():
        await tracker.process_videos([reels["known"], reels["free"], reels["other"]])
        await tracker.shutdown()

    asyncio.run(run())
    assert instagram.calls == ["alpha_trader"]  # no owner search while Meta refuses
    assert {reels["free"], reels["other"]} <= set(repo.meta_waiting_ids())  # the search continues with the queue
    assert _video(reels["free"]).status == VideoStatus.PENDING


def test_search_is_capped_per_run(reels, monkeypatch):
    many = [f"acct_{n}" for n in range(100)]
    instagram = FakeInstagram({name: [] for name in many})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: many)
    tracker = make_tracker(instagram)

    async def run():
        await tracker.process_videos([reels["nowhere"]])
        await tracker.shutdown()

    asyncio.run(run())
    assert len(instagram.calls) == tracker_module.OWNER_SEARCH_MAX_ACCOUNTS


def test_candidates_are_known_professional_accounts(reels):
    from app.models.entities import Creator, CreatorStatus

    with session_scope() as db:
        db.add_all([
            Creator(channel_name="CA ok", platform="instagram", channel_url="https://www.instagram.com/ca_ok/",
                    normalized_identifier="ca_ok", status=CreatorStatus.COMPLETED),
            Creator(channel_name="CA failed", platform="instagram", channel_url="https://www.instagram.com/ca_failed/",
                    normalized_identifier="ca_failed", status=CreatorStatus.FAILED),
        ])
        seen = db.get(VtVideo, reels["known"])
        seen.current_views = 10  # alpha's reel was read successfully
    try:
        candidates = repo.instagram_owner_candidates()
        assert "alpha_trader" in candidates and "ca_ok" in candidates
        assert "ca_failed" not in candidates  # not readable (e.g. a personal account)
        assert candidates.index("alpha_trader") < candidates.index("ca_ok")
    finally:
        with session_scope() as db:
            for creator in db.query(Creator).filter(Creator.normalized_identifier.in_(("ca_ok", "ca_failed"))):
                db.delete(creator)
