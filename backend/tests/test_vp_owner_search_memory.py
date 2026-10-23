"""Only exhaustive owner searches are cached; truncated scans remain retryable."""

import asyncio

import pytest

from app.models.db import session_scope
from app.video_performance import repository as repo
from app.video_performance.models import VideoStatus, VtVideo
from tests.test_vp_reel_owner import FakeInstagram, _video, api, make_tracker, media  # noqa: F401 (fixtures)


def _ownerless_reel(code: str) -> int:
    with session_scope() as db:
        for old in db.query(VtVideo).filter(VtVideo.video_identifier == code):
            db.delete(old)
        db.flush()
        video = VtVideo(platform="instagram", video_identifier=code, video_url=f"https://www.instagram.com/reel/{code}/",
                        status=VideoStatus.PENDING, source="upload")
        db.add(video)
        db.flush()
        return video.id


def test_repeated_refresh_of_an_unfindable_reel_costs_no_calls(api, monkeypatch):
    vid = _ownerless_reel("NoOwnerReel01")
    known = ["acct_a", "acct_b", "acct_c"]
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: list(known))
    instagram = FakeInstagram({name: [media(f"Other{name}x", 1)] for name in [*known, "acct_new"]})

    def refresh():
        tracker = make_tracker(instagram)

        async def run():
            await tracker.process_videos([vid])
            await tracker.shutdown()

        asyncio.run(run())

    try:
        refresh()  # first time: every known account is read once
        assert sorted(instagram.calls) == known
        first = _video(vid)
        assert "no exact match" in first.status_reason and "3 of 3 known accounts checked" in first.status_reason

        refresh()  # Refresh again (what the user kept clicking): nothing new to search -> zero calls
        refresh()
        assert sorted(instagram.calls) == known

        known.append("acct_new")  # a new account becomes known: only it is read
        refresh()
        assert instagram.calls[-1] == "acct_new" and len(instagram.calls) == 4

        # the owner is in a newly known account: found, and the reel's search memory is cleared
        instagram.accounts["acct_found"] = [media("NoOwnerReel01", 4321)]
        known.append("acct_found")
        refresh()
        found = _video(vid)
        assert found.owner_username == "acct_found" and found.current_views == 4321
        assert repo.owner_search_history(["NoOwnerReel01"])["NoOwnerReel01"] == set()
    finally:
        repo.delete_videos([vid])


@pytest.mark.parametrize("reuse_owner_scan", [False, True])
def test_incomplete_scan_is_retried_and_can_find_an_older_reel(api, monkeypatch, reuse_owner_scan):
    code = "OlderOwnerReel01"
    vid = _ownerless_reel(code)
    sibling = None
    if reuse_owner_scan:
        with session_scope() as db:
            row = VtVideo(platform="instagram", video_identifier="RecentSibling01",
                          video_url="https://www.instagram.com/reel/RecentSibling01/",
                          owner_username="paged_owner", status=VideoStatus.PENDING, source="upload")
            db.add(row)
            db.flush()
            sibling = row.id

    class PagedInstagram(FakeInstagram):
        complete = False

        async def profile_media(self, username, **kwargs):
            profile, items = await super().profile_media(username, **kwargs)
            profile["_scan_complete"] = self.complete
            return profile, items

    instagram = PagedInstagram({"paged_owner": [media("RecentSibling01", 1)]})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["paged_owner"])

    def refresh(ids):
        tracker = make_tracker(instagram)

        async def run():
            await tracker.process_videos(ids)
            await tracker.shutdown()

        asyncio.run(run())

    try:
        refresh([sibling, vid] if sibling else [vid])
        assert "search incomplete" in _video(vid).status_reason
        assert repo.owner_search_history([code])[code] == set()

        # A later scan with a larger window reaches the older post.
        instagram.complete = True
        instagram.accounts["paged_owner"].append(media(code, 4321))
        refresh([vid])
        found = _video(vid)
        assert instagram.calls == ["paged_owner", "paged_owner"]
        assert (found.owner_username, found.current_views, found.status) == ("paged_owner", 4321, VideoStatus.TRACKING)
    finally:
        repo.delete_videos([vid, sibling] if sibling else [vid])


def test_legacy_partial_search_cache_does_not_block_recovery(api, monkeypatch):
    from app.models.usage import ApiRateStatus

    code = "LegacyOwnerReel01"
    vid = _ownerless_reel(code)
    with session_scope() as db:
        db.add(ApiRateStatus(service="vt_owner_search", details={code: ["actual_owner"]}))
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["actual_owner"])
    instagram = FakeInstagram({"actual_owner": [media(code, 321)]})
    tracker = make_tracker(instagram)

    async def run():
        await tracker.process_videos([vid])
        await tracker.shutdown()

    try:
        asyncio.run(run())
        assert instagram.calls == ["actual_owner"]
        assert _video(vid).current_views == 321
    finally:
        repo.delete_videos([vid])
        with session_scope() as db:
            db.delete(db.get(ApiRateStatus, "vt_owner_search"))
