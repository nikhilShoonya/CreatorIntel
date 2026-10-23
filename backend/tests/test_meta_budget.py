"""Meta call budget: large uploads stay under Meta's hourly limit - work that does not fit waits (Pending) and
continues automatically in the next hour; already-tracked videos cost nothing; YouTube is not affected."""

import asyncio
import re
from collections import Counter

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.models.entities import Creator, CreatorStatus
from app.models.usage import ApiRateStatus
from app.services import api_usage, meta_budget
from app.video_performance import repository as repo
from app.video_performance.ingest import parse_import
from app.video_performance.models import VideoStatus, VtVideo
from app.video_performance.platforms import InstagramVideoClient, YouTubeVideoClient
from app.video_performance.tracker import VideoTracker

T0 = 1_800_000_000.0
OWNERS = 100
REEL_PAGE = 3  # every reel is on its owner's 3rd page of 50 posts -> 3 calls per owner, 300 calls in total


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def clock(monkeypatch):
    now = {"t": T0}
    monkeypatch.setattr(meta_budget, "_now", lambda: now["t"])
    return now


@pytest.fixture
def budget_200(monkeypatch):
    monkeypatch.setattr(get_settings(), "meta_hourly_call_budget", 200)


def reel_code(owner: int) -> str:
    return f"Reel{owner:03d}abcd"


class FakeGraph:
    """Business Discovery: owner_NNN has 4 pages of 50 posts; its tracked reel is on page REEL_PAGE."""

    def __init__(self, clock):
        self.clock, self.calls = clock, []  # (time, owner, page)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        fields = request.url.params["fields"]
        owner = re.search(r"business_discovery\.username\(([^)]+)\)", fields).group(1)
        cursor = re.search(r"\.after\(p(\d+)\)", fields)
        page = int(cursor.group(1)) if cursor else 1
        self.calls.append((self.clock["t"], owner, page))
        index = int(owner.split("_")[-1]) if owner.startswith("owner_") else -1
        items = [{"permalink": f"https://www.instagram.com/reel/F{index:03d}p{page}n{i:02d}x/", "media_type": "VIDEO",
                  "like_count": 1, "comments_count": 0, "view_count": 10} for i in range(50)]
        if page == REEL_PAGE and index >= 0:
            items[7] = {"permalink": f"https://www.instagram.com/reel/{reel_code(index)}/", "media_type": "VIDEO",
                        "media_product_type": "REELS", "like_count": 40, "comments_count": 4, "view_count": 1_000 + index}
        paging = {"cursors": {"after": f"p{page + 1}"}, "next": "https://graph.facebook.com/next"} if page < 4 else {}
        return httpx.Response(200, json={"business_discovery": {"username": owner, "media": {"data": items, "paging": paging}},
                                         "id": "17840000000000000"})


class FakeYouTube:
    def __init__(self):
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        ids = request.url.params["id"].split(",")
        return httpx.Response(200, json={"items": [
            {"id": vid, "snippet": {"title": f"YouTube {vid}", "channelId": "UCx", "channelTitle": "YT"},
             "statistics": {"viewCount": "5000", "likeCount": "50", "commentCount": "5"}} for vid in ids
        ]})


class NoSentiment:
    model_name = "test"

    async def analyze_many(self, videos):
        list(videos)
        return {}


def _waiting(ids: list[int]) -> int:
    with session_scope() as db:
        return sum(1 for v in db.query(VtVideo).filter(VtVideo.id.in_(ids)) if v.status == VideoStatus.PENDING)


def test_100_reel_upload_stays_under_the_budget_and_finishes_in_the_next_hour(api, clock, budget_200):
    with session_scope() as db:  # an already-tracked reel (with old numbers) of an owner that must never be called
        for leftover in db.query(VtVideo).filter(VtVideo.video_identifier.like("Reel%abcd")):
            db.delete(leftover)
        db.flush()
        tracked = VtVideo(platform="instagram", video_identifier="TrackedReel01", video_url="https://www.instagram.com/reel/TrackedReel01/",
                          owner_username="tracked_owner", status=VideoStatus.TRACKING, current_views=777)
        db.add(tracked)
        db.flush()
        tracked_id = tracked.id

    lines = ["Video Link,Username"]
    lines += [f"https://www.instagram.com/reel/{reel_code(n)}/,owner_{n:03d}" for n in range(OWNERS)]
    lines += ["https://www.instagram.com/reel/TrackedReel01/,tracked_owner"]
    lines += [f"https://youtu.be/ytBudget0{n:02d}" for n in range(3)]
    parsed = parse_import("big.csv", "\n".join(lines).encode(), 10_000_000)
    upload_id, new_ids = repo.import_rows("big.csv", None, parsed.rows)
    assert tracked_id not in new_ids and len(new_ids) == OWNERS + 3  # already tracked: nothing queued

    graph, youtube = FakeGraph(clock), FakeYouTube()
    settings = get_settings()
    tracker = VideoTracker(
        settings,
        instagram=InstagramVideoClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(graph))),
        youtube=YouTubeVideoClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(youtube))),
        sentiment=NoSentiment(),
    )

    async def hour(run_upload: bool):
        if run_upload:
            await tracker.process_videos(new_ids)
        while await tracker.process_meta_queue():  # what the Meta queue does every few minutes
            pass

    try:
        # ---- hour 1: the upload is processed until the budget is used, the rest waits
        asyncio.run(hour(run_upload=True))
        first_hour = len(graph.calls)
        assert first_hour == 200  # exactly the budget - never more
        waiting = _waiting(new_ids)
        assert 0 < waiting < OWNERS
        with session_scope() as db:
            pending = db.query(VtVideo).filter(VtVideo.id.in_(new_ids), VtVideo.status == VideoStatus.PENDING).first()
            assert pending.status_reason.startswith("Pending - waiting for Meta's hourly limit")

        clock["t"] += 20 * 60  # still inside the hour: the queue makes no call
        asyncio.run(hour(run_upload=False))
        assert len(graph.calls) == first_hour

        # ---- hour 2: the queue continues the waiting reels, oldest first, and finishes
        clock["t"] = T0 + 3600 + 1
        asyncio.run(hour(run_upload=False))
        assert _waiting(new_ids) == 0
        assert len(graph.calls) - first_hour <= 200

        # never more than the budget in ANY rolling hour
        times = sorted(t for t, _, _ in graph.calls)
        assert all(sum(1 for t in times if start <= t < start + 3600) <= 200 for start in times)
        # each owner's media read page by page and only until its reel was found (one extra read for the
        # owner that was cut off by the budget in hour 1)
        assert len(graph.calls) <= OWNERS * REEL_PAGE + REEL_PAGE
        with session_scope() as db:
            reels = db.query(VtVideo).filter(VtVideo.id.in_(new_ids), VtVideo.platform == "instagram").all()
            assert all(v.status == VideoStatus.TRACKING and v.current_views == 1_000 + int(v.owner_username[-3:]) for v in reels)
            assert db.get(VtVideo, tracked_id).current_views == 777  # untouched
            youtube_videos = db.query(VtVideo).filter(VtVideo.id.in_(new_ids), VtVideo.platform == "youtube").all()
            assert all(v.current_views == 5000 for v in youtube_videos)
        assert "tracked_owner" not in {owner for _, owner, _ in graph.calls}  # zero calls for already-tracked
        assert youtube.calls == 1  # YouTube: one batched call for its 3 videos, not limited by the Meta budget
        assert Counter(owner for _, owner, _ in graph.calls).most_common(1)[0][1] <= REEL_PAGE * 2
    finally:
        asyncio.run(tracker.shutdown())
        repo.delete_videos(new_ids + [tracked_id])
        api.delete(f"/api/video-performance/uploads/{upload_id}")


# ------------------------------------------------------------------ the budget itself
def test_budget_is_a_rolling_hour(clock, monkeypatch):
    monkeypatch.setattr(get_settings(), "meta_hourly_call_budget", 3)
    for _ in range(3):
        meta_budget.take()
        clock["t"] += 600
    with pytest.raises(meta_budget.MetaBudgetExhausted) as blocked:
        meta_budget.take()
    assert blocked.value.resume_at == T0 + 3600  # when the first call leaves the window
    assert meta_budget.available() == 0
    clock["t"] = T0 + 3600 + 1
    assert meta_budget.available() == 1  # one slot freed, the other two calls are still inside the hour
    meta_budget.take()


def test_meta_reported_usage_above_the_threshold_stops_calls(clock):
    meta_budget.observe(79.0, None)
    meta_budget.take()  # below 80%: allowed
    meta_budget.observe(85.0, 10)  # Meta says: 85% used, access regained in 10 minutes
    with pytest.raises(meta_budget.MetaBudgetExhausted) as blocked:
        meta_budget.take()
    assert "85%" in blocked.value.reason and blocked.value.resume_at == T0 + 600
    clock["t"] += 601
    meta_budget.take()


def test_rate_limit_answer_pauses_meta_calls_for_an_hour(clock):
    meta_budget.note_rate_limited()
    assert meta_budget.available() == 0 and meta_budget.resume_at().timestamp() == T0 + 3600
    clock["t"] += 3601
    assert meta_budget.available() > 0


def test_rate_limited_graph_call_is_never_retried_and_pauses_the_budget(clock):
    calls = []

    def graph(request):
        calls.append(1)
        return httpx.Response(403, json={"error": {"code": 4, "message": "Application request limit reached"}})

    client = InstagramVideoClient(get_settings().model_copy(update={"http_max_retries": 3}),
                                  client=httpx.AsyncClient(transport=httpx.MockTransport(graph)))
    for _ in range(3):
        with pytest.raises(Exception):
            asyncio.run(client.profile_media("someone"))
    assert len(calls) == 1  # one refused call, then nothing until the hour has passed


def test_call_log_survives_a_restart(api, clock, monkeypatch):
    monkeypatch.setattr(get_settings(), "meta_hourly_call_budget", 5)
    for _ in range(4):
        meta_budget.take()
    api_usage.flush()  # the usage flusher saves the log (every 20 s in the app)
    with session_scope() as db:
        assert len(db.get(ApiRateStatus, meta_budget.STATE_SERVICE).details["calls"]) == 4

    meta_budget.reset()  # "restart": empty memory, loaded again from the database
    monkeypatch.setattr(meta_budget, "_loaded", False)
    assert meta_budget.available() == 1  # the 4 calls of the last hour still count
    with session_scope() as db:
        db.delete(db.get(ApiRateStatus, meta_budget.STATE_SERVICE))


# ------------------------------------------------------------------ Creator Analytics
def test_rate_limited_creator_waits_with_its_data_and_continues(api, clock, monkeypatch):
    from app.agents.content_analysis_agent import ContentAnalyzer
    from app.agents.instagram_agent import InstagramCollector
    from app.agents.youtube_agent import YouTubeCollector
    from app.services.llm_client import LLMClient
    from app.services.orchestrator import EnrichmentOrchestrator
    from tests.fakes import instagram_handler, llm_handler, mock_client, youtube_handler

    with session_scope() as db:
        for old in db.query(Creator).filter(Creator.normalized_identifier == "tradingtech31"):
            db.delete(old)
        db.flush()
        creator = Creator(channel_name="Trading Tech", platform="instagram", channel_url="https://www.instagram.com/tradingtech31/",
                          normalized_identifier="tradingtech31", identifier_type="username", status=CreatorStatus.COMPLETED,
                          followers_count=999, genre="Finance & Investment")
        db.add(creator)
        db.flush()
        creator_id = creator.id

    settings = get_settings()
    orchestrator = EnrichmentOrchestrator(
        settings,
        youtube=YouTubeCollector(settings, client=mock_client(youtube_handler)),
        instagram=InstagramCollector(settings, client=mock_client(instagram_handler)),
        analyzer=ContentAnalyzer(LLMClient(settings, client=mock_client(llm_handler)), settings=settings),
    )
    try:
        meta_budget.note_rate_limited()  # Meta refused earlier this hour
        asyncio.run(orchestrator.enrich_creator(creator_id))
        with session_scope() as db:
            waiting = db.get(Creator, creator_id)
            assert waiting.status == CreatorStatus.PENDING
            assert waiting.error_message.startswith("Pending - waiting for Meta's hourly limit")
            assert (waiting.followers_count, waiting.genre) == (999, "Finance & Investment")  # stored data kept
        assert asyncio.run(orchestrator.process_meta_queue()) == 0  # still inside the hour: nothing called

        clock["t"] += 3601
        assert asyncio.run(orchestrator.process_meta_queue()) == 1
        with session_scope() as db:
            done = db.get(Creator, creator_id)
            assert done.status in (CreatorStatus.COMPLETED, CreatorStatus.PARTIAL) and done.followers_count == 1_200_000
    finally:
        with session_scope() as db:
            db.delete(db.get(Creator, creator_id))


def test_meta_queue_endpoint(api, clock):
    meta_budget.note_rate_limited()
    data = api.get("/api/config/meta-queue").json()
    assert data["paused"] is True and data["resume_at"] is not None
    assert {"waiting_videos", "waiting_creators", "calls_last_hour", "hourly_budget"} <= data.keys()
