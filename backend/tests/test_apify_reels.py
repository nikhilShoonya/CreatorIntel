"""No live scrapes: official SDK request shapes exercised through httpx.MockTransport."""

import asyncio
import json
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
from apify_client import ApifyClientAsync
from sqlalchemy import delete, select

from app.config.settings import get_settings
from app.models.db import session_scope
from app.utils.logging import redact
from app.utils.time import utcnow
from app.video_performance import apify_repository as jobs, queries, repository as repo
from app.video_performance.apify_reels import ApifyReelClient, count
from app.video_performance.ingest import parse_import
from app.video_performance.models import VtApifyRun, VtApifyWork, VtVideo, VtViewSnapshot, VideoStatus
from app.video_performance.platforms import VideoMetrics
from app.video_performance.tracker import VideoTracker
from app.video_performance.urls import parse_video_link
from tests.test_vp_reel_owner import FakeInstagram, NoSentiment, _video, api, media  # noqa: F401


def code():
    return "Ap" + uuid4().hex[:12]


def item(shortcode, **extra):
    return {"shortCode": shortcode, "url": f"https://www.instagram.com/p/{shortcode}/",
            "inputUrl": f"https://www.instagram.com/reel/{shortcode}/", "type": "Video", "productType": "clips",
            "ownerUsername": "posting_owner", "caption": "Useful caption", "timestamp": "2026-10-20T10:00:00Z",
            "videoPlayCount": 1000, "igPlayCount": 999, "videoViewCount": 500, "likesCount": 100, "commentsCount": 10,
            "sharesCount": 7, "videoDuration": 15.5, **extra}


class ApiFailure(Exception):
    def __init__(self, status_code):
        self.status_code = status_code
        super().__init__("never expose apify_api_secretTokenForTest")


class ActorServer:
    def __init__(self, transform=None, failure=None, status="SUCCEEDED", dataset=True):
        self.starts, self.start_params, self.runs, self.datasets = [], [], {}, {}
        self.transform, self.failure, self.status, self.has_dataset = transform, failure, status, dataset

    def handle(self, request):
        path = request.url.path
        if request.method == "POST" and path.endswith("/runs"):
            payload = json.loads(request.content)
            self.starts.append(payload)
            self.start_params.append(dict(request.url.params))
            assert path.endswith("/acts/apify~instagram-reel-scraper/runs")
            assert request.url.params["timeout"] == "600"
            if self.failure:
                raise self.failure
            run_id = f"remote{len(self.starts)}"
            codes = [parse_video_link(url).identifier for url in payload["username"]]
            output = [item(c) for c in reversed(codes)]
            self.datasets[run_id] = self.transform(output) if self.transform else output
            run = {"id": run_id, "status": self.status, "chargedEventCounts": {"reel": len(codes)}, "usageTotalUsd": 0.01}
            if self.has_dataset:
                run["defaultDatasetId"] = run_id
            self.runs[run_id] = run
            return httpx.Response(201, json={"data": run})
        if "/actor-runs/" in path:
            return httpx.Response(200, json={"data": self.runs[path.rsplit("/", 1)[-1]]})
        if path.endswith("/items"):
            run_id = path.split("/")[-2]
            data = self.datasets[run_id]
            return httpx.Response(200, json=data, headers={
                "x-apify-pagination-total": str(len(data)), "x-apify-pagination-offset": "0",
                "x-apify-pagination-limit": "100", "x-apify-pagination-desc": "false",
            })
        raise AssertionError(f"Unexpected SDK request: {request.method} {path}")

    def sdk(self):
        sdk = ApifyClientAsync("test-apify-token", max_retries=0)

        async def call(*, method, url, **kwargs):
            # SDK networking is replaced in its entirety, never allowing real API traffic.
            async with httpx.AsyncClient(transport=httpx.MockTransport(self.handle)) as transport:
                return await transport.request(method, url, content=kwargs.get("data"), headers=kwargs.get("headers"),
                                               params={k: v for k, v in (kwargs.get("params") or {}).items() if v is not None})

        sdk.http_client.call = call
        return sdk


@pytest.fixture(autouse=True)
def clean_apify_jobs(api):
    def clear():
        with session_scope() as db:
            db.execute(delete(VtApifyWork))
            db.execute(delete(VtApifyRun))
    clear()
    yield
    clear()


@pytest.fixture
def settings():
    return get_settings().model_copy(update={"apify_instagram_reel_enabled": True, "apify_api_token": "test-apify-token"})


@pytest.fixture
def videos():
    ids = []

    def add(shortcode=None, path="reel"):
        shortcode = shortcode or code()
        vid = repo.add_video(parse_video_link(f"https://www.instagram.com/{path}/{shortcode}/"), None, None, "manual")
        ids.append(vid)
        return vid

    yield add
    repo.delete_videos(ids)


@pytest.fixture(autouse=True)
def no_known_accounts(monkeypatch):
    """Meta fallback owner search sees no known accounts unless a test sets them."""
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])


def make_tracker(settings, server, **extra):
    extra.setdefault("instagram", FakeInstagram({}))  # Meta fallback never reaches the network in tests
    return VideoTracker(settings, apify=ApifyReelClient(settings, server.sdk()), sentiment=NoSentiment(), **extra)


def process(tracker, ids):
    async def run():
        try:
            return await tracker.process_videos(ids)
        finally:
            await tracker.shutdown()
    return asyncio.run(run())


def test_upload_batch_shortcodes_order_and_duplicate_membership(api, settings, monkeypatch):
    codes = [code(), code(), code()]
    server = ActorServer()
    settings = settings.model_copy(update={"apify_instagram_reel_batch_size": 2})
    tracker = make_tracker(settings, server)
    queued = []
    monkeypatch.setattr(tracker, "start_processing", lambda ids: queued.extend(ids))
    monkeypatch.setattr(api.app.state, "video_tracker", tracker)
    csv = "Video Link,Platform\n" + "\n".join([
        f"https://instagram.com/reels/{codes[0]}/?igsh=x,Instagram", f"{codes[1]},Instagram",
        f"https://instagram.com/reel/{codes[2]}/,", f"https://instagram.com/reel/{codes[0]}/,",
    ])
    response = api.post("/api/video-performance/uploads", files={"file": ("reels.csv", csv.encode(), "text/csv")})
    assert response.status_code == 201
    upload_id = response.json()["upload"]["id"]
    try:
        assert len(queued) == 3 and response.json()["upload"]["duplicates"] == 1
        assert process(tracker, queued) == (3, 0)
        assert [len(p["username"]) for p in server.starts] == [2, 1]
        for payload in server.starts:
            assert set(payload) == {"username", "includeSharesCount", "includeTranscript", "includeDownloadedVideo"}
            assert payload["includeSharesCount"] is payload["includeTranscript"] is payload["includeDownloadedVideo"] is False
        rows = api.get(f"/api/video-performance/uploads/{upload_id}/rows").json()["rows"]
        assert rows[1]["video_link"] == codes[1]  # original shortcode retained
        assert set(queries.upload_video_ids(upload_id)) == set(queued)
        for vid in queued:
            video = _video(vid)
            assert video.video_identifier in codes and video.owner_username == "posting_owner"
            assert video.current_views == 1000 and video.engagement_rate == 11
            assert video.metrics_source == "apify_instagram_reel"
            assert video.source_metrics["videoViewCount"] == 500 and "sharesCount" not in video.source_metrics
            assert video.previous_views is None and video.growth_pct is None
            assert api.get(f"/api/video-performance/videos/{vid}").json()["view_metric"] == "videoPlayCount"
        usage = jobs.usage(settings)
        assert usage["used_units"] == usage["returned_items"] == 3 and usage["active_runs"] == 0
        assert "test-apify-token" not in json.dumps(api.get("/api/config/apify-usage").json())
        exported = api.get(f"/api/video-performance/exports/csv?upload_id={upload_id}")
        assert exported.status_code == 200
    finally:
        repo.delete_upload(upload_id, True)


@pytest.mark.parametrize("value,hint,ok", [
    ("Dd9E0uFRFW4", True, True), ("Dd9E0uFRFW4", False, False),
    ("https://instagram.com/reels/Dd9E0uFRFW4/?igsh=x", False, True),
    ("https://instagram.com/owner/reel/Dd9E0uFRFW4/", False, True),
    ("https://instagram.com.evil.test/reel/Dd9E0uFRFW4/", True, False),
    ("https://instagram.com/reel/Dd9E0uFRFW4/extra", False, False),
    ("https://user@instagram.com/reel/Dd9E0uFRFW4/", False, False),
    ("https://instagram.com/reel/abc!", False, False),
])
def test_validated_identity(value, hint, ok):
    parsed = parse_video_link(value, instagram_reel_hint=hint)
    assert parsed.ok is ok
    if ok:
        assert parsed.identifier == "Dd9E0uFRFW4"
        assert parsed.url == "https://www.instagram.com/reel/Dd9E0uFRFW4/"


def test_explicit_header_and_ambiguous_shortcode():
    assert parse_import("a.csv", b"Instagram Reel URL\nDd9E0uFRFW4\n", 10000).rows[0].parsed.ok
    assert parse_import("a.csv", b"Video Link\nDd9E0uFRFW4\n", 10000).rows[0].status == "invalid"


@pytest.mark.parametrize("plays,ig,views,expected,basis", [(100, 90, 80, 100, "videoPlayCount"),
    (None, 90, 80, 90, "igPlayCount"), (None, None, 80, 80, "videoViewCount"),
    (0, 90, 80, 0, "videoPlayCount"), (None, None, None, None, None)])
def test_count_mapping_and_partial(settings, videos, plays, ig, views, expected, basis):
    vid = videos()
    server = ActorServer(lambda items: [dict(items[0], videoPlayCount=plays, igPlayCount=ig, videoViewCount=views,
                                           likesCount=10, commentsCount=1)])
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    video = _video(vid)
    assert (video.current_views, video.view_metric) == (expected, basis)
    assert video.status == (VideoStatus.PARTIAL if expected is None else VideoStatus.TRACKING)
    assert video.engagement_rate is None if expected in (None, 0) else video.engagement_rate is not None


@pytest.mark.parametrize("value", [True, -1, 1.5, "12x", "1.5", float("nan"), float("inf"), 2**64])
def test_malformed_counts_are_missing(value):
    assert count(value) is None


def test_partial_batch_item_errors_and_unmatched_results(settings, videos):
    ids = [videos() for _ in range(3)]
    codes = [_video(vid).video_identifier for vid in ids]
    def output(_):
        return [item(codes[1], error="not_found", errorDescription="private"), item(codes[0]),
                item(codes[2].swapcase()), {"shortCode": codes[2], "url": f"https://instagram.com/reel/{codes[0]}/"}]
    assert process(make_tracker(settings, ActorServer(output)), ids) == (1, 2)
    assert _video(ids[0]).status == VideoStatus.TRACKING
    assert _video(ids[1]).status == VideoStatus.VIDEO_DOWN  # Apify not_found and Meta cannot find it either
    assert _video(ids[2]).status == VideoStatus.FAILED and _video(ids[2]).last_checked_at is None


@pytest.mark.parametrize("failure", [ApiFailure(401), ApiFailure(429), TimeoutError()])
def test_failure_preserves_metrics_and_no_duplicate_submit(settings, videos, failure):
    vid = videos()
    server = ActorServer()
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    before = _video(vid)
    server.failure = failure
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    after = _video(vid)
    assert (after.current_views, after.likes, after.last_checked_at) == (before.current_views, before.likes, before.last_checked_at)
    assert after.status == VideoStatus.PARTIAL
    assert "apify_api_secretTokenForTest" not in after.status_reason
    if isinstance(failure, TimeoutError):
        # No run ID was received, so even a new tracker/process must not repeat the POST.
        assert process(make_tracker(settings, server), [vid]) == (0, 1)
        assert len(server.starts) == 2
        run = jobs.usage(settings)["recent_runs"][0]
        assert jobs.reconcile(run["id"], None)


@pytest.mark.parametrize("status,dataset", [("FAILED", True), ("TIMED-OUT", True), ("SUCCEEDED", False)])
def test_failed_actor_and_missing_dataset(settings, videos, status, dataset):
    vid = videos()
    server = ActorServer(lambda _: [], status=status, dataset=dataset)
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    assert _video(vid).status == VideoStatus.FAILED
    assert _video(vid).current_views is None


def test_failed_actor_partial_dataset_keeps_valid_results(settings, videos):
    vid = videos()
    assert process(make_tracker(settings, ActorServer(status="FAILED")), [vid]) == (1, 0)
    assert _video(vid).current_views == 1000


def test_monthly_reservation_and_persistent_duplicate_suppression(settings, videos):
    ids = [videos(), videos(), videos()]
    rows = [{"id": vid, "identifier": _video(vid).video_identifier} for vid in ids]
    settings = settings.model_copy(update={"apify_instagram_reel_monthly_result_limit": 2})
    first, refused, reason = jobs.reserve(rows, settings)
    assert len(first) == 1 and len(refused) == 1 and "monthly" in reason
    second, refused, _ = jobs.reserve(rows[:2], settings)
    assert len(second) == 1 and second[0]["id"] == first[0]["id"] and not second[0].get("new")
    assert jobs.usage(settings)["reserved_units"] == 2
    server = ActorServer()
    assert process(make_tracker(settings, server), ids[:2]) == (0, 2)
    assert server.starts == []  # uncertain pre-existing reservation cannot launch another billable run


def test_daily_growth_snapshot_idempotency_and_metric_basis(settings, videos, monkeypatch):
    vid = videos()
    server = ActorServer()
    tracker = make_tracker(settings, server)
    assert process(tracker, [vid]) == (1, 0)
    with session_scope() as db:
        snapshot = db.scalar(select(VtViewSnapshot).where(VtViewSnapshot.video_id == vid))
        snapshot.captured_at = utcnow() - timedelta(days=1)
    server.transform = lambda items: [dict(items[0], videoPlayCount=1500)]
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    video = _video(vid)
    assert (video.current_views, video.previous_views, video.views_gained, video.growth_pct) == (1500, 1000, 500, 50)
    assert video.engagement_rate == round(110 / 1500 * 100, 2)
    history = queries.history(vid)
    assert len(history) == 2 and history[0].source_metrics["videoViewCount"] == 500
    with session_scope() as db:
        snapshot = db.scalar(select(VtViewSnapshot).where(VtViewSnapshot.video_id == vid).order_by(VtViewSnapshot.id.desc()))
        run_id = snapshot.source_run_id
    assert repo.save_metrics(vid, VideoMetrics(identifier=video.video_identifier, views=1500, source_run_id=run_id))
    assert len(queries.history(vid)) == 2
    server.transform = lambda items: [dict(items[0], videoPlayCount=None, igPlayCount=None, videoViewCount=600)]
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    assert _video(vid).previous_views is None  # never compare plays with views


def test_known_remote_run_is_resumed_without_new_actor(settings, videos):
    vid = videos()
    shortcode = _video(vid).video_identifier
    runs, _, _ = jobs.reserve([{"id": vid, "identifier": shortcode}], settings)
    jobs.update_run(runs[0]["id"], remote_run_id="existing", status="running")
    server = ActorServer()
    server.runs["existing"] = {"id": "existing", "status": "SUCCEEDED", "defaultDatasetId": "existing"}
    server.datasets["existing"] = [item(shortcode)]
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    assert server.starts == []


def test_verified_owner_conflict_is_not_overwritten(settings, videos):
    vid = videos()
    server = ActorServer()
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    server.transform = lambda items: [dict(items[0], ownerUsername="wrong_owner")]
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    assert _video(vid).owner_username == "posting_owner" and _video(vid).current_views == 1000


def test_disabled_missing_token_and_posts_keep_meta(settings, videos):
    for enabled, token in [(False, "token"), (True, "")]:
        config = settings.model_copy(update={"apify_instagram_reel_enabled": enabled, "apify_api_token": token})
        assert config.apify_reels_configured is False
    vid = videos(path="p")
    class Meta:
        async def profile_media(self, username, **kwargs):
            pytest.fail("Ownerless post should not have an account supplied")
    server = ActorServer()
    # No known candidate accounts; regular Meta owner-unresolved path remains available for a post.
    from unittest.mock import patch
    with patch.object(repo, "instagram_owner_candidates", return_value=[]):
        assert process(make_tracker(settings, server, instagram=Meta()), [vid]) == (0, 1)
    assert server.starts == []


def test_shares_opt_in_and_token_redaction(settings, videos):
    vid = videos()
    settings = settings.model_copy(update={"apify_include_shares": True})
    server = ActorServer()
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    assert server.starts[0]["includeSharesCount"] is True and _video(vid).source_metrics["sharesCount"] == 7
    assert "apify_api_secretTokenForTest" not in redact("error apify_api_secretTokenForTest")


def test_retry_and_run_now_use_same_provider_and_sentiment(api, settings, videos, monkeypatch):
    from app.video_performance.scheduler import VideoTrackingScheduler
    from app.video_performance.sentiment import SentimentResult

    vid = videos()
    server = ActorServer(failure=ApiFailure(401))
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    captions = []

    class Sentiment:
        model_name = "mock-groq"

        async def analyze_many(self, rows):
            rows = list(rows)
            captions.extend(rows)
            return {row[0]: SentimentResult(sentiment="Positive", confidence=0.9, input_chars=len(row[2] or "")) for row in rows}

    server.failure = None
    tracker = make_tracker(settings, server)
    tracker.sentiment = Sentiment()
    queued = []
    monkeypatch.setattr(tracker, "start_processing", lambda ids: queued.extend(ids))
    monkeypatch.setattr(api.app.state, "video_tracker", tracker)
    assert api.post(f"/api/video-performance/videos/{vid}/retry").status_code == 202
    assert queued == [vid] and process(tracker, queued) == (1, 0)
    assert captions[0][2] == "Useful caption" and _video(vid).sentiment == "Positive"
    assert settings.video_tracking_auto_refresh is False
    assert "metrics_refresh" not in VideoTrackingScheduler(tracker, settings).times

    # Dashboard Run now and an explicitly enabled daily refresh share this metrics job.
    monkeypatch.setattr(repo, "videos_due_for_refresh", lambda *_args: [vid])
    async def run_job():
        await tracker.run_metrics_refresh("manual")
        await tracker.shutdown()
    asyncio.run(run_job())
    assert len(server.starts) == 3  # failed initial attempt, retry, explicit Run now


def test_concurrent_trackers_share_one_billable_run(settings, videos):
    vid = videos()
    server = ActorServer()
    a, b = make_tracker(settings, server), make_tracker(settings, server)

    async def run():
        try:
            await asyncio.gather(a.process_videos([vid]), b.process_videos([vid]))
        finally:
            await a.shutdown()
            await b.shutdown()
    asyncio.run(run())
    assert len(server.starts) == 1
    assert _video(vid).current_views == 1000
    assert len(queries.history(vid)) == 1


def test_missing_configuration_never_submits(settings):
    config = settings.model_copy(update={"apify_api_token": ""})
    server = ActorServer()
    result, associations = asyncio.run(ApifyReelClient(config, server.sdk()).videos([{"id": 1, "identifier": code()}]))
    assert result[1].code == "apify_error" and not associations and not server.starts


def test_missing_engagement_retains_views_but_not_a_stale_rate(settings, videos):
    vid = videos()
    server = ActorServer()
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    server.transform = lambda items: [dict(items[0], likesCount=None)]
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    row = _video(vid)
    assert row.current_views == 1000 and row.status == VideoStatus.PARTIAL and row.engagement_rate is None


def test_failed_actor_preserves_per_item_error_and_budget(settings, videos):
    vid = videos()
    server = ActorServer(lambda items: [dict(items[0], error="restricted")], status="FAILED")
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    assert _video(vid).status != VideoStatus.VIDEO_DOWN
    assert jobs.usage(settings)["used_units"] == 1


def test_invalid_settings_are_rejected():
    from pydantic import ValidationError
    from app.config.settings import Settings

    for fields in ({"apify_instagram_reel_batch_size": 0}, {"apify_instagram_reel_monthly_result_limit": -1},
                   {"apify_instagram_reel_actor_id": "https://evil.test/actor"}):
        with pytest.raises(ValidationError):
            Settings(**fields)


def test_database_preparation_failure_is_actionable_without_leaking_details(settings, videos, monkeypatch, caplog):
    from sqlalchemy.exc import ProgrammingError

    vid = videos()
    server = ActorServer()

    def broken_reservation(*_args):
        raise ProgrammingError("private SQL apify_api_secretTokenForTest", {}, Exception("missing column"))

    monkeypatch.setattr(jobs, "reserve", broken_reservation)
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    assert "alembic upgrade head" in _video(vid).status_reason
    assert "ProgrammingError" in caplog.text
    assert "private SQL" not in caplog.text and "secretTokenForTest" not in caplog.text
    assert not server.starts


# ------------------------------------------------------------------ fixes: run cost cap, rejections, Meta fallback
class TypedApiFailure(ApiFailure):
    def __init__(self, status_code, type_):
        super().__init__(status_code)
        self.type = type_


def test_single_reel_run_cost_cap_is_never_below_apifys_minimum(settings, videos):
    from app.video_performance.apify_reels import run_charge_cap

    assert run_charge_cap(1, settings) == 0.01  # 1 Reel x $0.005 + start would be $0.006: raised to the minimum
    assert run_charge_cap(25, settings) == 0.126
    assert run_charge_cap(1, settings.model_copy(update={"apify_include_shares": True})) == 0.016
    vid = videos()
    server = ActorServer()
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    assert float(server.start_params[0]["maxTotalChargeUsd"]) == 0.01


def test_clear_rejection_names_apifys_reason_and_frees_the_unit(settings, videos):
    vid = videos()
    server = ActorServer(failure=TypedApiFailure(400, "max-total-charge-usd-below-minimum"))
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    assert "HTTP 400, max-total-charge-usd-below-minimum" in _video(vid).status_reason
    usage = jobs.usage(settings)
    assert usage["used_units"] == 0 and usage["recent_runs"][0]["status"] == "failed"


def test_no_credits_is_a_definite_rejection_and_retry_is_not_stuck(settings, videos):
    vid = videos()
    server = ActorServer(failure=ApiFailure(402))
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    assert "no usage credits" in _video(vid).status_reason and "operator" not in _video(vid).status_reason
    server.failure = None  # credits added: Retry starts a new run instead of waiting for an operator
    assert process(make_tracker(settings, server), [vid]) == (1, 0)
    assert len(server.starts) == 2 and jobs.usage(settings)["active_runs"] == 0


def test_restart_repair_never_moves_apify_failures_into_metas_queue(settings, videos):
    vid = videos()
    with session_scope() as db:
        video = db.get(VtVideo, vid)
        video.status, video.status_reason = VideoStatus.FAILED, "Apify rate limit reached; no immediate automatic retry"
    repo.repair_temporary_failures()
    assert _video(vid).status == VideoStatus.FAILED and vid not in repo.meta_waiting_ids()


def test_reel_apify_cannot_read_falls_back_to_meta(settings, videos, monkeypatch):
    vid = videos()
    shortcode = _video(vid).video_identifier
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["posting_owner"])
    meta = FakeInstagram({"posting_owner": [media(shortcode, 4321)]})
    server = ActorServer(lambda items: [dict(items[0], error="restricted", errorDescription="restricted")])
    assert process(make_tracker(settings, server, instagram=meta), [vid]) == (1, 0)
    video = _video(vid)
    assert (video.status, video.current_views, video.owner_username) == (VideoStatus.TRACKING, 4321, "posting_owner")
    assert "metrics from Meta's official API" in video.status_reason and video.metrics_source is None
    assert meta.calls == ["posting_owner"]


def test_both_providers_failing_keeps_apify_rules_and_says_why(settings, videos):
    vid = videos()
    server = ActorServer(lambda items: [])  # Apify returns nothing; no known accounts for Meta's owner search
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    video = _video(vid)
    assert video.status == VideoStatus.FAILED and vid not in repo.meta_waiting_ids()
    assert "no exact matching result" in video.status_reason and "Meta fallback - Instagram owner unresolved" in video.status_reason


def test_meta_queue_continuation_uses_meta_without_a_new_apify_run(settings, videos, monkeypatch):
    from app.video_performance.platforms import META_WAIT

    vid = videos()
    shortcode = _video(vid).video_identifier
    with session_scope() as db:
        video = db.get(VtVideo, vid)
        video.status, video.status_reason = VideoStatus.PENDING, f"{META_WAIT} (owner search paused)"
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["posting_owner"])
    server = ActorServer()
    meta = FakeInstagram({"posting_owner": [media(shortcode, 777)]})
    assert process(make_tracker(settings, server, instagram=meta), [vid]) == (1, 0)
    assert server.starts == [] and _video(vid).current_views == 777


def test_reel_that_does_not_exist_is_video_down_and_not_auto_retried(settings, videos):
    vid = videos()
    server = ActorServer(lambda items: [dict(items[0], error="not_found", errorDescription="Post does not exist")])
    assert process(make_tracker(settings, server), [vid]) == (0, 1)
    video = _video(vid)
    assert video.status == VideoStatus.VIDEO_DOWN and "does not exist on Instagram" in video.status_reason
    assert vid not in repo.failed_video_ids()  # "Retry failed" does not pay for it again


def test_reel_that_does_not_exist_with_unknown_owner_spends_no_meta_calls(settings, videos, monkeypatch):
    vid = videos()
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["acct_a", "acct_b"])
    meta = FakeInstagram({"acct_a": [], "acct_b": []})
    server = ActorServer(lambda items: [dict(items[0], error="not_found", errorDescription="Post does not exist")])
    assert process(make_tracker(settings, server, instagram=meta), [vid]) == (0, 1)
    assert meta.calls == [] and _video(vid).status == VideoStatus.VIDEO_DOWN
