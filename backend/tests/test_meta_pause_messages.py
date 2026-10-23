"""Owner-search pauses say whether CreatorIntel's own Meta safety stop paused the search or Meta itself refused,
and the usage card gets Meta's three reported percentages (call count, CPU time, total time) plus the resume time."""

import asyncio

import httpx

from app.services import api_usage, meta_budget
from app.video_performance import repository as repo
from app.video_performance.models import VideoStatus
from app.video_performance.platforms import META_WAIT, meta_wait_error
from app.video_performance.tracker import OWNER_SEARCH_PAUSED
from tests.test_vp_owner_search_memory import _ownerless_reel
from tests.test_vp_reel_owner import FakeInstagram, _video, api, make_tracker  # noqa: F401 (fixtures)


def _meta_reports(app_usage: str, business: str | None = None) -> None:
    """A Graph API response carrying Meta's usage headers (no request is sent)."""
    headers = {"x-app-usage": app_usage}
    if business:
        headers["x-business-use-case-usage"] = business
    api_usage.record_meta_usage(httpx.Response(200, headers=headers))


class BudgetedInstagram(FakeInstagram):
    """Every account read goes through the Meta budget first, like the real client."""

    async def profile_media(self, username: str, pages: int = 1, **kwargs):
        try:
            meta_budget.take()
        except meta_budget.MetaBudgetExhausted as exc:
            raise meta_wait_error(exc) from exc
        return await super().profile_media(username, pages, **kwargs)


def _run(instagram, vid: int) -> None:
    tracker = make_tracker(instagram)

    async def run():
        await tracker.process_videos([vid])
        await tracker.shutdown()

    asyncio.run(run())


def _owners(monkeypatch, names: list[str]) -> None:
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: list(names))


def test_app_safety_stop_names_the_metric_and_resume_time(api, monkeypatch):
    vid = _ownerless_reel("SafetyStop01")
    _owners(monkeypatch, ["acct_a", "acct_b"])
    _meta_reports('{"call_count":28,"total_cputime":0,"total_time":80}')  # the 16:08 reading
    instagram = BudgetedInstagram({"acct_a": [], "acct_b": []})
    try:
        _run(instagram, vid)
        assert instagram.calls == []  # nothing was sent to Meta
        video = _video(vid)
        reason = video.status_reason
        assert video.status == VideoStatus.PENDING and reason.startswith(META_WAIT)
        assert "CreatorIntel safety stop, not a Meta refusal" in reason
        assert "80% safety stop (call count 28%, CPU time 0%, total time 80%)" in reason
        assert "finding this reel's owner continues after " in reason
        assert "Instagram API rate limit reached" not in reason
        assert vid in repo.meta_waiting_ids()  # still continued automatically by the Meta queue
    finally:
        repo.delete_videos([vid])
        _meta_reports('{"call_count":0,"total_cputime":0,"total_time":0}')


def test_genuine_meta_refusal_keeps_the_existing_message(api, monkeypatch):
    vid = _ownerless_reel("MetaRefused01")
    _owners(monkeypatch, ["acct_limited"])
    instagram = FakeInstagram({}, rate_limited={"acct_limited"})  # Meta answers "rate limit reached"
    try:
        _run(instagram, vid)
        video = _video(vid)
        assert video.status == VideoStatus.PENDING and OWNER_SEARCH_PAUSED in video.status_reason
        assert "safety stop" not in video.status_reason
    finally:
        repo.delete_videos([vid])


def test_usage_shows_each_meta_metric_and_resume_time(api):
    _meta_reports('{"call_count":12,"total_cputime":3,"total_time":5}',
                  '{"1784":[{"type":"instagram","call_count":41,"total_cputime":2,"total_time":4}]}')
    usage = api_usage.instagram_usage()
    assert usage.metrics.model_dump() == {"call_count": 41.0, "total_cputime": 3.0, "total_time": 5.0}
    assert usage.percent == 41.0 and usage.resume_at is None and usage.safety_stop_percent == 80.0

    _meta_reports('{"call_count":28,"total_cputime":0,"total_time":80}')
    try:
        usage = api_usage.instagram_usage()
        assert usage.metrics.model_dump() == {"call_count": 28.0, "total_cputime": 0.0, "total_time": 80.0}
        assert usage.percent == 80.0 and usage.resume_at is not None  # the safety stop is on until resume_at
        api_usage.flush()  # read back from the database
        assert api_usage.instagram_usage().metrics.total_time == 80.0
        body = api.get("/api/config/api-usage").json()["instagram"]
        assert body["metrics"] == {"call_count": 28.0, "total_cputime": 0.0, "total_time": 80.0}
        assert body["resume_at"] and "200 requests" not in body["note"]
    finally:
        meta_budget.reset()
        _meta_reports('{"call_count":0,"total_cputime":0,"total_time":0}')
        api_usage.flush()
