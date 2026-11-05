"""Facebook in Video Performance through the Apify Actor. Apify is mocked with the row and OUTPUT shapes of a real run."""

import asyncio
import io
import json
import logging
import random
import time
from datetime import datetime, timedelta

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.video_performance.facebook_apify as facebook_module
from app.config.settings import get_settings
from app.main import app
from app.models.db import init_db, session_scope
from app.services.llm_client import LLMClient
from app.video_performance import apify_repository, repository as repo
from app.video_performance import facebook_apify_repository as ledger
from app.video_performance.facebook_apify import FacebookApifyClient, match_rows, missing_row_error, row_outcome, run_charge_cap
from app.video_performance.models import VideoStatus, VtFacebookApifyRun, VtVideo, VtViewSnapshot
from app.video_performance.platforms import FACEBOOK_METRICS_SOURCE, InstagramVideoClient, PlatformError, VideoMetrics, YouTubeVideoClient
from app.video_performance.schemas import VideoOut
from app.video_performance.sentiment import VideoSentimentAnalyzer
from app.video_performance.share_links import ShareLinkResolver
from app.video_performance.tracker import VideoTracker
from app.video_performance.urls import parse_video_link
from tests.fakes import sentiment_batch_response

TOKEN = "apify_api_SECRET-fake-token"
OWN, OTHER, WATCH, GONE, BROKEN, REJECTED = (
    "2111111111111111", "2222222222222222", "2333333333333333", "2444444444444444", "2555555555555555", "2666666666666666",
)
RUN_COUNTER = {"n": 0}


def metric_status(precision="exact", **overrides):
    return [{"metric": name, "status": "present", "precision": overrides.get(name, precision), "source": "public_content"}
            for name in ("views", "plays", "reactions", "likes", "comments", "shares")]


def ok_row(input_url, *, views=3251, plays=13325, reactions=85, likes=84, comments=1, shares=6, **extra):
    video_id = input_url.rstrip("/").rsplit("/", 1)[-1].rsplit("=", 1)[-1]
    return {
        "platform": "facebook", "inputUrl": input_url, "contentUrl": f"https://www.facebook.com/somepage/videos/{video_id}/",
        "contentId": video_id, "contentType": "reel", "videoId": video_id, "publishedAt": "2026-10-26T06:31:03.000Z",
        "profileUrl": "https://www.facebook.com/somepage", "profileId": "102511951255425", "profileUsername": "somepage",
        "profileName": "Some Page", "description": "Direct vs regular mutual funds", "views": views, "plays": plays,
        "reactions": reactions, "likes": likes, "comments": comments, "shares": shares, "contentMetricStatus": metric_status(),
        "profileFollowers": 607862, "profilePageLikes": None,
        "profileMetricStatus": [
            {"metric": "profileFollowers", "status": "present", "precision": "exact_or_displayed", "source": "public_profile"},
            {"metric": "profilePageLikes", "status": "unavailable", "precision": "unavailable", "source": None, "statusReason": None},
        ],
        "status": "ok", "statusReason": None, "scrapedAt": "2026-11-05T10:15:39.296Z", **extra,
    }


def no_metrics_row(input_url):
    video_id = input_url.rstrip("/").rsplit("/", 1)[-1]
    return {"platform": "facebook", "inputUrl": input_url, "contentId": video_id, "contentType": "reel", "views": None, "plays": None,
            "reactions": None, "likes": None, "comments": None, "shares": None, "status": "resolved_no_public_metrics",
            "statusReason": "No public metric counts were exposed for this URL.", "scrapedAt": "2026-11-05T10:15:52.111Z"}


class FakeApify:
    """Enough of the Apify REST API: start a run, read it, read its dataset and OUTPUT."""

    def __init__(self, respond=None):
        self.respond = respond or ok_row
        self.starts: list[dict] = []
        self.requests: list[httpx.Request] = []
        self.runs: dict[str, dict] = {}
        self.start_errors: list = []  # next POST answers: status code, or an exception to raise
        self.dataset_errors = 0
        self.output_status = 200
        self.final_status = "SUCCEEDED"
        self.polls_before_done = 1

    def transport_client(self):
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/runs"):
            if self.start_errors:
                answer = self.start_errors.pop(0)
                if isinstance(answer, Exception):
                    raise answer
                return httpx.Response(answer, json={"error": {"type": "x", "message": "no"}})
            run_input = json.loads(request.content)
            self.starts.append({"input": run_input, "params": dict(request.url.params), "path": path})
            RUN_COUNTER["n"] += 1
            run_id = f"remote{RUN_COUNTER['n']}"
            rows = [row for url in run_input["startUrls"] if (row := self.respond(url)) is not None]
            random.Random(7).shuffle(rows)  # results come back in any order
            self.runs[run_id] = {"rows": rows, "polls": 0, "input": run_input}
            return httpx.Response(201, json={"data": {"id": run_id, "status": "RUNNING"}})
        if "/actor-runs/" in path:
            run_id = path.rsplit("/", 1)[-1]
            run = self.runs[run_id]
            run["polls"] += 1
            status = self.final_status if run["polls"] >= self.polls_before_done else "RUNNING"
            return httpx.Response(200, json={"data": {
                "id": run_id, "status": status, "defaultDatasetId": run_id, "defaultKeyValueStoreId": run_id,
                "usageTotalUsd": 0.004 * len(run["rows"]), "chargedEventCounts": {"apify-default-dataset-item": len(run["rows"])},
            }})
        if "/datasets/" in path:
            if self.dataset_errors:
                self.dataset_errors -= 1
                return httpx.Response(500, json={})
            run = self.runs[path.split("/datasets/")[1].split("/")[0]]
            offset, limit = int(request.url.params["offset"]), int(request.url.params["limit"])
            return httpx.Response(200, json=run["rows"][offset:offset + limit])
        if "/key-value-stores/" in path:
            if self.output_status != 200:
                return httpx.Response(self.output_status, json={})
            run = self.runs[path.split("/key-value-stores/")[1].split("/")[0]]
            return httpx.Response(200, json={"providedUrls": len(run["input"]["startUrls"]), "datasetRows": len(run["rows"])})
        return httpx.Response(404, json={})


def fb_settings(**overrides):
    return get_settings().model_copy(update={
        "apify_api_token": TOKEN, "apify_facebook_enabled": True, "apify_facebook_poll_seconds": 0.01, "http_max_retries": 0, **overrides,
    })


def run_client(fake, rows, **overrides):
    return asyncio.run(FacebookApifyClient(fb_settings(**overrides), client=fake.transport_client()).videos(rows))


def batch(*identifiers, seen=False, start=900000):
    return [{"id": start + n, "identifier": identifier, "video_url": f"https://www.facebook.com/reel/{identifier}/", "seen": seen}
            for n, identifier in enumerate(identifiers)]


@pytest.fixture(scope="module", autouse=True)
def _database():
    init_db()  # the first tests here need no app, only its tables


@pytest.fixture(autouse=True)
def _clean_ledger():
    yield
    with session_scope() as db:
        for run in db.scalars(select(VtFacebookApifyRun)):
            db.delete(run)


# ------------------------------------------------------------------ input
def test_actor_input_and_token_handling():
    fake = FakeApify()
    run_client(fake, batch(OWN, OTHER))
    start = fake.starts[0]
    assert start["path"] == "/v2/acts/chronometrica~facebook-metrics-scraper/runs"
    assert start["input"] == {
        "mode": "knownUrls",
        "startUrls": [f"https://www.facebook.com/reel/{OWN}/", f"https://www.facebook.com/reel/{OTHER}/"],
        "includePosts": False, "includeReels": True, "includeVideos": True, "includePhotos": False,
        "includeAuthorProfileMetrics": True, "includeFailedRows": True,
        "maxConcurrency": 3, "requestDelayMillis": 250, "maxFetchRetries": 3, "requestTimeoutSecs": 20,
    }
    assert float(start["params"]["maxTotalChargeUsd"]) == run_charge_cap(2, fb_settings())
    for request in fake.requests:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert TOKEN not in str(request.url)


def test_configurable_input_comes_from_settings():
    fake = FakeApify()
    run_client(fake, batch(OWN), apify_facebook_max_concurrency=7, apify_facebook_request_delay_ms=900, apify_facebook_max_fetch_retries=1,
               apify_facebook_request_timeout_secs=45, apify_facebook_include_author_profile_metrics=False, apify_facebook_include_failed_rows=False)
    sent = fake.starts[0]["input"]
    assert (sent["maxConcurrency"], sent["requestDelayMillis"], sent["maxFetchRetries"], sent["requestTimeoutSecs"]) == (7, 900, 1, 45)
    assert sent["includeAuthorProfileMetrics"] is False and sent["includeFailedRows"] is False


def test_disabled_or_tokenless_integration_starts_nothing():
    for overrides in ({"apify_facebook_enabled": False}, {"apify_api_token": "  "}):
        fake = FakeApify()
        outcome = run_client(fake, batch(OWN), **overrides)[900000]
        assert outcome.code == PlatformError.API and "disabled" in outcome.message
        assert fake.requests == []


# ------------------------------------------------------------------ batches and matching
def test_one_actor_run_for_a_batch_of_ten_and_every_row_reaches_its_own_video():
    identifiers = [f"30000000000000{n:02d}" for n in range(10)]
    fake = FakeApify(lambda url: ok_row(url, views=1000 + int(url.rstrip("/")[-2:])))
    results = run_client(fake, batch(*identifiers))
    assert len(fake.starts) == 1
    for n in range(10):
        assert results[900000 + n].views == 1000 + n and results[900000 + n].identifier == identifiers[n]


def test_results_are_matched_by_input_url_never_by_position():
    inputs = [{"id": 1, "identifier": OWN, "url": f"https://www.facebook.com/reel/{OWN}/"},
              {"id": 2, "identifier": WATCH, "url": f"https://www.facebook.com/watch/?v={WATCH}"}]
    rows = [
        {"inputUrl": f"https://www.facebook.com/watch?v={WATCH}", "marker": "watch"},  # same link, written differently
        {"inputUrl": f"https://www.facebook.com/reel/{OWN}", "marker": "reel"},
        {"inputUrl": "https://www.facebook.com/reel/7777777777777777/", "marker": "someone else's"},
        "not a row",
    ]
    matched = match_rows(inputs, rows)
    assert [r["marker"] for r in matched[1]] == ["reel"] and [r["marker"] for r in matched[2]] == ["watch"]


def test_a_row_without_input_url_is_not_guessed():
    fake = FakeApify(lambda url: {**ok_row(url), "inputUrl": None})
    outcome = run_client(fake, batch(OWN))[900000]
    assert isinstance(outcome, PlatformError) and "no result" in outcome.message


def test_batches_follow_the_configured_size():
    identifiers = [f"31000000000000{n:02d}" for n in range(25)]
    runs, refused, _ = ledger.reserve(batch(*identifiers), fb_settings(apify_facebook_batch_size=10))
    assert [len(run["inputs"]) for run in runs] == [10, 10, 5] and refused == []


# ------------------------------------------------------------------ metric mapping
def test_views_plays_likes_and_reactions_stay_separate():
    metrics = row_outcome(ok_row(f"https://www.facebook.com/reel/{OWN}/"), {"identifier": OWN}, "run1")
    assert (metrics.views, metrics.plays, metrics.likes, metrics.reactions, metrics.comments, metrics.shares) == (3251, 13325, 84, 85, 1, 6)
    assert metrics.view_metric == "views" and metrics.metrics_source == FACEBOOK_METRICS_SOURCE and metrics.source_run_id == "run1"
    assert (metrics.channel_title, metrics.owner_username, metrics.channel_id) == ("Some Page", "somepage", "102511951255425")
    assert metrics.caption == "Direct vs regular mutual funds" and metrics.published_at is not None
    assert (metrics.content_type, metrics.profile_followers, metrics.profile_page_likes) == ("reel", 607862, None)
    assert metrics.metric_status["profile"]["profilePageLikes"]["status"] == "unavailable"


def test_plays_stand_in_for_views_only_when_views_are_missing_and_are_never_added():
    row = ok_row(f"https://www.facebook.com/reel/{OWN}/", views=None, plays=500)
    row["contentMetricStatus"] = metric_status(plays="compact_rounded")
    metrics = row_outcome(row, {"identifier": OWN}, "run1")
    assert (metrics.views, metrics.plays, metrics.view_metric, metrics.views_precision) == (500, 500, "plays", "compact_rounded")
    assert any("uses plays" in note for note in metrics.notes)


def test_rounded_values_are_labelled_and_never_exact():
    row = ok_row(f"https://www.facebook.com/reel/{OWN}/", views=3300000)
    row["contentMetricStatus"] = metric_status(views="compact_rounded")
    metrics = row_outcome(row, {"identifier": OWN}, "run1")
    assert metrics.views == 3300000 and metrics.views_precision == "compact_rounded"
    assert any("rounded" in note for note in metrics.notes)
    assert metrics.metric_status["content"]["views"]["precision"] == "compact_rounded"
    assert metrics.metric_status["content"]["likes"]["precision"] == "exact"


@pytest.mark.parametrize("precision, approximate", [("exact", False), ("exact_or_displayed", False), ("compact_rounded", True), (None, False)])
def test_api_flags_only_real_approximations(precision, approximate):
    video = VtVideo(id=1, platform="facebook", video_identifier=OWN, video_url="u", source="upload", status="Tracking",
                    views_precision=precision, created_at=datetime(2026, 1, 1))
    assert VideoOut.model_validate(video).views_approximate is approximate


@pytest.mark.parametrize("value", [True, -5, 1.5, "abc", float("nan")])
def test_impossible_counts_are_treated_as_missing(value):
    metrics = row_outcome(ok_row(f"https://www.facebook.com/reel/{OWN}/", likes=value), {"identifier": OWN}, "run1")
    assert metrics.likes is None and any("engagement" in note for note in metrics.notes)


# ------------------------------------------------------------------ rows that have no metrics
def test_no_public_metrics_for_a_video_never_read_means_video_down():
    outcome = row_outcome(no_metrics_row(f"https://www.facebook.com/reel/{GONE}/"), {"identifier": GONE, "seen": False}, "r")
    assert outcome.code == PlatformError.NOT_FOUND and "private, deleted" in outcome.message


def test_no_public_metrics_for_a_video_read_before_is_temporary():
    outcome = row_outcome(no_metrics_row(f"https://www.facebook.com/reel/{GONE}/"), {"identifier": GONE, "seen": True}, "r")
    assert outcome.code == PlatformError.API


def test_status_ok_without_any_count_is_not_a_success():
    row = ok_row(f"https://www.facebook.com/reel/{OWN}/", views=None, plays=None, reactions=None, likes=None, comments=None, shares=None)
    assert isinstance(row_outcome(row, {"identifier": OWN, "seen": False}, "r"), PlatformError)


def test_failed_invalid_and_photo_rows():
    base = {"inputUrl": f"https://www.facebook.com/reel/{OWN}/"}
    failed = row_outcome({**base, "status": "failed", "statusReason": "Timed out token=abc123"}, {"identifier": OWN}, "r")
    assert failed.code == PlatformError.API and "Timed out" in failed.message and "abc123" not in failed.message
    invalid = row_outcome({**base, "status": "invalid_url", "statusReason": None}, {"identifier": OWN}, "r")
    assert invalid.code == PlatformError.UNSUPPORTED
    photo = row_outcome({**ok_row(base["inputUrl"]), "contentType": "photo"}, {"identifier": OWN}, "r")
    assert photo.code == PlatformError.UNSUPPORTED


@pytest.mark.parametrize("summary, code, text", [
    ({"skippedNoPublicMetricRows": 2}, PlatformError.NOT_FOUND, "no public metrics"),
    ({"skippedInvalidRows": 2}, PlatformError.UNSUPPORTED, "invalid"),
    ({"skippedFailedRows": 2}, PlatformError.API, "could not read"),
    ({"skippedNoPublicMetricRows": 1, "skippedFailedRows": 1}, PlatformError.API, "no result"),  # cannot tell which is which
    (None, PlatformError.API, "no result"),
])
def test_missing_rows_are_explained_only_when_output_accounts_for_them(summary, code, text):
    outcome = missing_row_error(2, summary, "SUCCEEDED", {"identifier": OWN, "seen": False})
    assert outcome.code == code and text in outcome.message


def test_batch_with_mixed_results_and_a_missing_row():
    def respond(url):
        if GONE in url:
            return no_metrics_row(url)
        return None if BROKEN in url else ok_row(url)

    results = run_client(FakeApify(respond), batch(OWN, GONE, BROKEN))
    assert isinstance(results[900000], VideoMetrics)
    assert results[900001].code == PlatformError.NOT_FOUND
    assert results[900002].code == PlatformError.API


def test_conflicting_rows_for_one_link_are_rejected():
    client = FacebookApifyClient(fb_settings(), client=FakeApify().transport_client())

    async def go():
        runs, _, _ = ledger.reserve(batch(OWN), client.settings)
        url = f"https://www.facebook.com/reel/{OWN}/"
        return await client._finish(runs[0], {"id": "r", "status": "SUCCEEDED"}, [ok_row(url, views=1), ok_row(url, views=2)], None)

    assert "conflicting" in asyncio.run(go())[900000].message


# ------------------------------------------------------------------ run problems
@pytest.mark.parametrize("status, text", [
    (401, "credentials"), (403, "credentials"), (402, "no usage credits"), (404, "unavailable"), (400, "refused"),
])
def test_rejected_starts_report_why_and_cost_nothing(status, text):
    fake = FakeApify()
    fake.start_errors = [status]
    outcome = run_client(fake, batch(OWN))[900000]
    assert outcome.code == PlatformError.API and text in outcome.message
    assert len([r for r in fake.requests if r.method == "POST"]) == 1
    with session_scope() as db:
        run = db.scalar(select(VtFacebookApifyRun))
        assert (run.status, run.budget_units) == ("failed", 0)


def test_rate_limited_start_is_retried_then_succeeds():
    fake = FakeApify()
    fake.start_errors = [429]
    assert isinstance(run_client(fake, batch(OWN))[900000], VideoMetrics)
    assert len(fake.starts) == 1


def test_start_that_stays_rate_limited_gives_up_without_cost():
    fake = FakeApify()
    fake.start_errors = [429, 429, 429]
    assert "rate limiting" in run_client(fake, batch(OWN))[900000].message
    assert len([r for r in fake.requests if r.method == "POST"]) == 3


@pytest.mark.parametrize("answer", [500, 503, httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
def test_unconfirmed_start_is_never_retried_automatically_and_is_counted(answer):
    fake = FakeApify()
    fake.start_errors = [answer]
    outcome = run_client(fake, batch(OWN))[900000]
    assert "did not confirm" in outcome.message
    assert len([r for r in fake.requests if r.method == "POST"]) == 1  # a lost answer may have started a paid run
    with session_scope() as db:
        run = db.scalar(select(VtFacebookApifyRun))
        assert (run.status, run.budget_units) == ("uncertain", 1)
    # It does not block the video: the next check starts normally.
    assert isinstance(run_client(fake, batch(OWN))[900000], VideoMetrics)


@pytest.mark.parametrize("final", ["FAILED", "TIMED-OUT", "ABORTED"])
def test_unsuccessful_run_keeps_its_partial_rows(final):
    fake = FakeApify(lambda url: ok_row(url) if OWN in url else None)
    fake.final_status = final
    results = run_client(fake, batch(OWN, OTHER))
    assert isinstance(results[900000], VideoMetrics)
    assert results[900001].code == PlatformError.API and final in results[900001].message
    with session_scope() as db:
        assert db.scalar(select(VtFacebookApifyRun)).status == "failed"


def test_dataset_failure_keeps_the_run_and_the_next_check_reads_it_without_paying_again():
    fake = FakeApify()
    fake.dataset_errors = 2  # outlasts the HTTP client's own retry
    rows = batch(OWN)
    first = run_client(fake, rows)[900000]
    assert first.code == PlatformError.API and "continues the same Actor run" in first.message
    assert len(fake.starts) == 1
    assert isinstance(run_client(fake, rows)[900000], VideoMetrics)
    assert len(fake.starts) == 1  # the same Actor run, read again


def test_missing_output_record_does_not_lose_the_rows():
    fake = FakeApify()
    fake.output_status = 404
    assert isinstance(run_client(fake, batch(OWN))[900000], VideoMetrics)


def test_status_read_errors_do_not_stop_following_the_run():
    fake = FakeApify()
    fake.polls_before_done = 3
    errors = {"left": 1}

    def flaky(request):
        if "/actor-runs/" in request.url.path and errors["left"]:
            errors["left"] -= 1
            return httpx.Response(503, json={})
        return fake(request)

    client = FacebookApifyClient(fb_settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(flaky)))
    assert isinstance(asyncio.run(client.videos(batch(OWN)))[900000], VideoMetrics)


def test_slow_run_times_out_and_is_continued_later(monkeypatch):
    fake = FakeApify()
    fake.polls_before_done = 10_000
    real_timeout = asyncio.timeout
    monkeypatch.setattr(facebook_module.asyncio, "timeout", lambda _seconds: real_timeout(0.2))
    assert "too long" in run_client(fake, batch(OWN))[900000].message
    with session_scope() as db:
        assert db.scalar(select(VtFacebookApifyRun)).status == "running"
    monkeypatch.setattr(facebook_module.asyncio, "timeout", real_timeout)
    fake.polls_before_done = 0
    assert isinstance(run_client(fake, batch(OWN))[900000], VideoMetrics)
    assert len(fake.starts) == 1


def test_interrupted_start_before_a_run_id_does_not_block_forever():
    runs, _, _ = ledger.reserve(batch(OWN), fb_settings())
    ledger.update_run(runs[0]["id"], status="starting")  # the server died here
    fake = FakeApify()
    first = run_client(fake, batch(OWN))[900000]
    assert "interrupted" in first.message and fake.starts == []
    assert isinstance(run_client(fake, batch(OWN))[900000], VideoMetrics)


# ------------------------------------------------------------------ limits and separation from Instagram
def test_monthly_limit_refuses_the_rest_of_the_batch():
    fake = FakeApify()
    results = run_client(fake, batch(OWN, OTHER, WATCH), apify_facebook_monthly_result_limit=2)
    assert isinstance(results[900000], VideoMetrics) and isinstance(results[900001], VideoMetrics)
    assert "monthly result limit" in results[900002].message
    assert len(fake.starts[0]["input"]["startUrls"]) == 2


def test_facebook_runs_do_not_touch_instagrams_apify_ledger_or_limit():
    settings = fb_settings(apify_instagram_reel_enabled=True)
    before = apify_repository.usage(settings)
    run_client(FakeApify(), batch(OWN, OTHER))
    after = apify_repository.usage(settings)
    assert (after["used_units"], after["remaining"], after["recent_runs"]) == (before["used_units"], before["remaining"], before["recent_runs"])
    facebook = ledger.usage(fb_settings())
    assert facebook["used_units"] == 2 and facebook["returned_items"] == 2 and facebook["limit"] == 900


def test_meta_budget_is_not_used_for_facebook():
    from app.services import meta_budget
    before = meta_budget.status()["calls_last_hour"]
    run_client(FakeApify(), batch(OWN))
    assert meta_budget.status()["calls_last_hour"] == before


# ------------------------------------------------------------------ settings
def test_facebook_status_depends_on_apify_not_on_a_page_id():
    assert fb_settings().facebook_configured is True
    assert fb_settings(apify_facebook_enabled=False).facebook_configured is False
    assert fb_settings(apify_api_token="").facebook_configured is False
    assert not hasattr(get_settings(), "meta_facebook_page_id")
    assert fb_settings(meta_access_token="").facebook_configured is True  # no Meta token needed


def test_instagram_settings_are_unchanged_by_the_facebook_flag():
    settings = fb_settings(apify_instagram_reel_enabled=False)
    assert settings.apify_reels_configured is False and settings.facebook_configured is True


# ------------------------------------------------------------------ URL parsing
@pytest.mark.parametrize("link, identifier, url", [
    (f"https://www.facebook.com/reel/{OWN}/?mibextid=abc", OWN, f"https://www.facebook.com/reel/{OWN}/"),
    (f"https://www.facebook.com/SomePage/videos/{OWN}/", OWN, f"https://www.facebook.com/watch/?v={OWN}"),
    (f"https://m.facebook.com/SomePage/videos/market-update/{OWN}/?ref=share", OWN, f"https://www.facebook.com/watch/?v={OWN}"),
    (f"https://www.facebook.com/watch/?v={OWN}&t=10", OWN, f"https://www.facebook.com/watch/?v={OWN}"),
    (f"https://web.facebook.com/video.php?v={OWN}", OWN, f"https://www.facebook.com/watch/?v={OWN}"),
    (f"facebook.com/reel/{OWN}", OWN, f"https://www.facebook.com/reel/{OWN}/"),
])
def test_facebook_video_links(link, identifier, url):
    parsed = parse_video_link(link)
    assert (parsed.platform, parsed.identifier, parsed.url) == ("facebook", identifier, url)


@pytest.mark.parametrize("link, reason", [
    ("https://www.facebook.com/share/v/1BgbFi3ptu/?mibextid=wwXIfr", "share links"),
    ("https://fb.watch/abcDEF123/", "fb.watch"),
    ("https://www.facebook.com/SomePage/posts/pfbid02abc", "post links"),
    ("https://www.facebook.com/SomePage/", "not a video"),
    ("https://www.facebook.com/reel/not-a-number/", "not valid"),
])
def test_unsupported_facebook_links_explain_why(link, reason):
    parsed = parse_video_link(link)
    assert not parsed.ok and reason in parsed.error


def test_youtube_and_instagram_parsing_unchanged():
    assert parse_video_link("https://youtu.be/aaaaaaaaaaa?si=x").identifier == "aaaaaaaaaaa"
    reel = parse_video_link("https://www.instagram.com/reels/DcD3zvtzLzO/?utm_source=ig")
    assert (reel.platform, reel.identifier) == ("instagram", "DcD3zvtzLzO")


# ------------------------------------------------------------------ end to end in Video Performance
def youtube(request: httpx.Request) -> httpx.Response:
    items = [{"id": v, "snippet": {"title": "YT"}, "statistics": {"viewCount": "500", "likeCount": "5", "commentCount": "1"}}
             for v in request.url.params["id"].split(",")]
    return httpx.Response(200, json={"items": items})


def instagram(request: httpx.Request) -> httpx.Response:
    media = [{"id": "1", "permalink": "https://www.instagram.com/reel/FbMixReel1/", "media_type": "VIDEO", "media_product_type": "REELS",
              "timestamp": "2026-09-01T10:00:00+0000", "like_count": 3, "comments_count": 1, "view_count": 70, "caption": "mixed upload reel"}]
    return httpx.Response(200, json={"business_discovery": {"id": "9", "username": "mixowner", "media": {"data": media}}})


def llm(request: httpx.Request) -> httpx.Response:
    return sentiment_batch_response(request, "Neutral", 0.7)


def share_redirects(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(302, headers={"location": f"https://www.facebook.com/reel/{OTHER}/?fs=e"})


async def public_ip(_host: str) -> list[str]:
    return ["157.240.1.35"]


def mock(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


STATE = {"views": {OWN: 2000, OTHER: 900, WATCH: 40}, "down": set(), "rounded": set()}


def respond(url: str):
    video_id = parse_video_link(url).identifier
    if video_id == GONE or video_id in STATE["down"]:
        return no_metrics_row(url)
    if video_id == REJECTED:
        return {"inputUrl": url, "status": "invalid_url", "statusReason": "Unsupported Facebook URL"}
    if video_id == BROKEN:
        return None
    row = ok_row(url, views=STATE["views"].get(video_id, 10), plays=7777, reactions=125, likes=120, comments=14, shares=3)
    if video_id in STATE["rounded"]:
        row["contentMetricStatus"] = metric_status(views="compact_rounded")
    return row


@pytest.fixture(scope="module")
def fake_apify():
    return FakeApify(respond)


@pytest.fixture(scope="module")
def api(fake_apify):
    with TestClient(app) as client:
        settings = fb_settings(http_max_retries=1)
        app.state.video_tracker = VideoTracker(
            settings,
            youtube=YouTubeVideoClient(settings, client=mock(youtube)),
            instagram=InstagramVideoClient(settings, client=mock(instagram)),
            facebook=FacebookApifyClient(settings, client=fake_apify.transport_client()),
            sentiment=VideoSentimentAnalyzer(settings, LLMClient(settings, client=mock(llm))),
        )
        app.state.share_resolver = ShareLinkResolver(client=mock(share_redirects), resolve_host=public_ip)
        yield client
        app.state.share_resolver = None
        with session_scope() as db:  # the test database is shared with other modules that count every video of a platform
            ids = list(db.scalars(select(VtVideo.id).where(VtVideo.platform == "facebook")))
        repo.delete_videos(ids)


def videos_by_id(api) -> dict[str, dict]:
    items = api.get("/api/video-performance/videos", params={"page_size": 100}).json()["items"]
    return {v["video_identifier"]: v for v in items}


def wait_idle(api, timeout=15) -> dict[str, dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        videos = videos_by_id(api)
        if all(v["status"] not in ("Pending", "Processing") for v in videos.values()):
            return videos
        time.sleep(0.05)
    raise AssertionError("videos still processing")


def test_upload_refresh_history_and_detail_end_to_end(api, fake_apify, caplog):
    caplog.set_level(logging.DEBUG)
    frame = pd.DataFrame([
        ["", "YouTube", "https://www.youtube.com/watch?v=fbmixYT0001", ""],
        ["", "Instagram", "https://www.instagram.com/reel/FbMixReel1/", "mixowner"],
        ["", "Facebook", f"https://www.facebook.com/SomePage/videos/{OWN}/", ""],  # a normal video
        ["", "fb", f"https://www.facebook.com/reel/{OTHER}/?mibextid=x", ""],  # a reel of another Page
        ["", "Facebook", f"https://www.facebook.com/watch/?v={WATCH}", ""],  # a Watch link
        ["", "Facebook", f"https://www.facebook.com/reel/{GONE}/", ""],  # private or deleted
        ["", "Facebook", f"https://www.facebook.com/reel/{REJECTED}/", ""],  # rejected by the Actor
        ["", "Facebook", f"https://www.facebook.com/reel/{BROKEN}/", ""],  # no row at all
        ["", "Facebook", "https://www.facebook.com/share/v/1BgbFi3ptu/", ""],  # share link of the same reel
        ["", "Facebook", "not a link", ""],
    ], columns=["Creator Name", "Platform", "Video Link", "Username"])
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False)
    res = api.post("/api/video-performance/uploads", files={"file": ("mixed.xlsx", buffer.getvalue(), "application/octet-stream")})
    assert res.status_code == 201, res.text
    upload = res.json()["upload"]
    assert (upload["added"], upload["duplicates"], upload["invalid"]) == (8, 1, 1)

    videos = wait_idle(api)
    assert len(fake_apify.starts) == 1  # one Actor run for the whole batch, not one per video
    assert videos["fbmixYT0001"]["status"] == "Tracking" and videos["FbMixReel1"]["status"] == "Tracking"  # other platforms untouched

    own = videos[OWN]
    assert own["status"] == "Tracking" and own["platform"] == "facebook" and own["metrics_source"] == FACEBOOK_METRICS_SOURCE
    assert (own["current_views"], own["plays"], own["likes"], own["reactions"], own["comments"], own["shares"]) == (2000, 7777, 120, 125, 14, 3)
    assert own["engagement_rate"] == round((120 + 14) / 2000 * 100, 2)  # likes + comments: reactions are not counted again
    assert (own["content_type"], own["profile_followers"], own["view_metric"]) == ("reel", 607862, "views")
    assert own["creator_name"] == "Some Page" and own["views_approximate"] is False
    assert own["metric_status"]["content"]["shares"]["precision"] == "exact"
    assert (videos[OTHER]["current_views"], videos[WATCH]["current_views"]) == (900, 40)  # each row on its own video
    assert videos[GONE]["status"] == "Video Down" and "private, deleted" in videos[GONE]["status_reason"]
    assert videos[REJECTED]["status"] == "Unsupported" and "Unsupported Facebook URL" in videos[REJECTED]["status_reason"]
    assert videos[BROKEN]["status"] == "Failed" and videos[BROKEN]["current_views"] is None  # nothing fabricated

    detail = api.get(f"/api/video-performance/videos/{own['id']}").json()
    assert detail["plays"] == 7777 and detail["caption_text"] == "Direct vs regular mutual funds"

    totals = {p["platform"]: p for p in api.get(f"/api/video-performance/uploads/{upload['id']}/platform-totals").json()["platforms"]}
    assert totals["facebook"]["total_views"] == 2000 + 900 + 40  # views only: plays are not summed in
    assert totals["facebook"]["videos_rounded"] == 0

    # Day 2: the daily refresh. Views rise, and one figure becomes rounded.
    with session_scope() as db:
        for snap in db.scalars(select(VtViewSnapshot)):
            snap.captured_at -= timedelta(days=1)
    STATE["views"][OWN] = 2500
    STATE["views"][OTHER] = 12000
    STATE["rounded"].add(OTHER)
    before = len(fake_apify.starts)
    assert api.post("/api/video-performance/jobs/metrics_refresh/run").status_code == 202
    time.sleep(0.3)
    videos = wait_idle(api)
    assert len(fake_apify.starts) > before
    own = videos[OWN]
    assert (own["current_views"], own["previous_views"], own["views_gained"], own["growth_pct"]) == (2500, 2000, 500, 25.0)
    other = videos[OTHER]
    assert other["views_approximate"] is True and other["views_gained_approximate"] is True and "rounded" in other["status_reason"]
    assert api.get(f"/api/video-performance/uploads/{upload['id']}/platform-totals").json()["platforms"][2]["videos_rounded"] == 1

    history = api.get(f"/api/video-performance/history/{own['id']}").json()["snapshots"]
    assert [s["views"] for s in history][:2] == [2500, 2000]
    assert history[0]["plays"] == 7777 and history[0]["reactions"] == 125 and history[0]["shares"] == 3
    assert history[0]["metrics_source"] == FACEBOOK_METRICS_SOURCE and history[0]["views_approximate"] is False

    # Filters, dashboard and exports keep working.
    only_fb = api.get("/api/video-performance/videos", params={"platform": "facebook", "page_size": 100}).json()
    assert {v["video_identifier"] for v in only_fb["items"]} == {OWN, OTHER, WATCH, GONE, REJECTED, BROKEN}
    assert api.get("/api/video-performance/dashboard").json()["facebook_videos"] >= 6
    assert ",Facebook," in api.get("/api/video-performance/exports/csv", params={"platform": "facebook"}).content.decode("utf-8-sig")

    # A temporary Apify failure keeps the last good numbers and reports the latest error.
    fake_apify.start_errors = [500]
    assert api.post(f"/api/video-performance/videos/{own['id']}/refresh").status_code == 202
    time.sleep(0.2)
    kept = wait_idle(api)[OWN]
    assert kept["status"] == "Tracking" and kept["current_views"] == 2500 and "did not confirm" in kept["status_reason"]

    # No public metrics on a video read before: numbers stay, with a note; it recovers by itself.
    STATE["down"].add(OWN)
    assert api.post(f"/api/video-performance/videos/{own['id']}/refresh").status_code == 202
    time.sleep(0.2)
    kept = wait_idle(api)[OWN]
    assert kept["status"] == "Tracking" and kept["current_views"] == 2500 and "no public metrics" in kept["status_reason"]
    STATE["down"].clear()

    # Retry of a video that was down, then pause and resume.
    assert api.post(f"/api/video-performance/videos/{videos[GONE]['id']}/retry").status_code == 202
    assert api.post(f"/api/video-performance/videos/{own['id']}/pause").status_code == 200
    assert videos_by_id(api)[OWN]["status"] == "Paused"
    assert api.post(f"/api/video-performance/videos/{own['id']}/resume").status_code == 202
    time.sleep(0.2)
    assert wait_idle(api)[OWN]["status"] == "Tracking"

    # The token never reaches logs or API responses, and no Graph API call was made for Facebook.
    config = api.get("/api/config/status").json()
    usage = api.get("/api/config/apify-facebook-usage").json()
    everything = caplog.text + str(videos_by_id(api)) + str(config) + str(usage)
    assert TOKEN not in everything
    assert all(r.url.host == "api.apify.com" for r in fake_apify.requests)
    assert usage["actor_id"] == "chronometrica/facebook-metrics-scraper"


def test_config_status_reports_the_new_provider(api):
    status = api.get("/api/config/status").json()
    assert "facebook_apify_enabled" in status and "facebook_configured" in status


def test_startup_repair_frees_videos_the_graph_api_rejected_without_starting_a_run():
    with session_scope() as db:
        old = VtVideo(platform="facebook", video_identifier="2910000000000001", video_url="https://www.facebook.com/reel/2910000000000001/",
                      source="upload", status=VideoStatus.UNSUPPORTED, status_reason="This video is not on your Facebook Page. Meta only allows reading videos")
        waiting = VtVideo(platform="facebook", video_identifier="2910000000000002", video_url="https://www.facebook.com/reel/2910000000000002/",
                          source="upload", status=VideoStatus.PENDING, status_reason="Pending - waiting for Meta's hourly limit (x)", current_views=5)
        untouched = VtVideo(platform="facebook", video_identifier="2910000000000003", video_url="https://www.facebook.com/reel/2910000000000003/",
                            source="upload", status=VideoStatus.UNSUPPORTED, status_reason="This Facebook link is a photo, not a video")
        reel = VtVideo(platform="instagram", video_identifier="IgKeepWait1", video_url="https://www.instagram.com/reel/IgKeepWait1/",
                       source="upload", status=VideoStatus.PENDING, status_reason="Pending - waiting for Meta's hourly limit (x)")
        db.add_all([old, waiting, untouched, reel])
        db.flush()
        ids = [old.id, waiting.id, untouched.id, reel.id]
    assert repo.repair_facebook_after_apify_switch() == 2
    assert repo.repair_facebook_after_apify_switch() == 0  # safe to run on every start
    with session_scope() as db:
        states = [db.get(VtVideo, i).status for i in ids]
        note = db.get(VtVideo, ids[0]).status_reason
    assert states == [VideoStatus.FAILED, VideoStatus.TRACKING, VideoStatus.UNSUPPORTED, VideoStatus.PENDING]
    assert "Retry" in note
    assert ids[1] not in repo.meta_waiting_ids() and ids[3] in repo.meta_waiting_ids()  # Instagram's queue is untouched
    repo.delete_videos(ids)


def test_a_paused_video_keeps_its_paused_status_when_metrics_arrive():
    with session_scope() as db:
        video = VtVideo(platform="facebook", video_identifier="2920000000000001", video_url="https://www.facebook.com/reel/2920000000000001/",
                        source="upload", status=VideoStatus.PAUSED)
        db.add(video)
        db.flush()
        video_id = video.id
    metrics = row_outcome(ok_row("https://www.facebook.com/reel/2920000000000001/"), {"identifier": "2920000000000001"}, "pausedrun")
    assert repo.save_metrics(video_id, metrics) is True
    assert repo.save_metrics(video_id, metrics) is True  # replaying a run neither duplicates the snapshot nor breaks
    with session_scope() as db:
        assert db.get(VtVideo, video_id).status == VideoStatus.PAUSED
        assert len(list(db.scalars(select(VtViewSnapshot).where(VtViewSnapshot.video_id == video_id)))) == 1
    repo.delete_videos([video_id])


def test_a_mixed_batch_does_not_hold_up_youtube(api, fake_apify):
    """YouTube is saved without waiting for the Facebook Actor run to finish."""
    tracker = app.state.video_tracker
    original = tracker.facebook.videos
    seen = {}
    with session_scope() as db:
        yt = VtVideo(platform="youtube", video_identifier="fbholdYT001", video_url="https://www.youtube.com/watch?v=fbholdYT001", source="manual", status="Pending")
        fb = VtVideo(platform="facebook", video_identifier="2900000000000001", video_url="https://www.facebook.com/reel/2900000000000001/", source="manual", status="Pending")
        db.add_all([yt, fb])
        db.flush()
        ids = (yt.id, fb.id)

    async def go():
        release = asyncio.Event()

        async def slow(rows):
            await release.wait()
            return await original(rows)

        tracker.facebook.videos = slow
        task = asyncio.create_task(tracker.process_videos(list(ids)))
        for _ in range(200):
            await asyncio.sleep(0.02)
            with session_scope() as db:
                seen["yt"], seen["fb"] = db.get(VtVideo, ids[0]).status, db.get(VtVideo, ids[1]).status
            if seen["yt"] == "Tracking":
                break
        release.set()
        await task
        with session_scope() as db:
            seen["fb_after"] = db.get(VtVideo, ids[1]).status

    try:
        asyncio.run(go())
    finally:
        tracker.facebook.videos = original
    repo.delete_videos(list(ids))
    assert seen == {"yt": "Tracking", "fb": "Processing", "fb_after": "Tracking"}


# ------------------------------------------------------------------ migration
def test_migration_keeps_existing_videos_and_history_both_ways(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    backend = Path(__file__).resolve().parents[1]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "migrations"))
    engine = create_engine(f"sqlite:///{(tmp_path / 'installed.db').as_posix()}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            config.attributes["connection"] = connection
            command.upgrade(config, "0008_bulk_link_uploads")
            connection.execute(text(
                "INSERT INTO video_tracking_videos (id, platform, video_identifier, video_url, source, status, current_views, tracking_started_at, created_at, updated_at) "
                "VALUES (1, 'facebook', '123456', 'https://www.facebook.com/reel/123456/', 'upload', 'Tracking', 500, '2026-01-01', '2026-01-01', '2026-01-01')"
            ))
            connection.execute(text("INSERT INTO video_tracking_view_history (video_id, captured_at, views) VALUES (1, '2026-01-01', 500)"))
            command.upgrade(config, "head")
            assert "plays" in {c["name"] for c in inspect(connection).get_columns("video_tracking_view_history")}
            assert connection.execute(text("SELECT current_views, plays, views_precision FROM video_tracking_videos")).one() == (500, None, None)
            assert connection.execute(text("SELECT views, plays FROM video_tracking_view_history")).one() == (500, None)
            command.downgrade(config, "0008_bulk_link_uploads")
            assert "plays" not in {c["name"] for c in inspect(connection).get_columns("video_tracking_videos")}
            assert "video_tracking_facebook_apify_runs" not in inspect(connection).get_table_names()
            assert connection.execute(text("SELECT views FROM video_tracking_view_history")).scalar_one() == 500
    finally:
        engine.dispose()
