"""Creator Analytics dashboard: every number comes from the stored creators and matches the Creator List."""

from collections import Counter
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.db import session_scope
from app.models.entities import Creator, CreatorStatus
from app.services import creator_dashboard as dashboard_service
from app.utils.time import utcnow

DASHBOARD = "/api/creators/dashboard"


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


@pytest.fixture
def seeded(api):
    """Creators in every state: analysed, partial, failed, pending, AI pending, invalid link, missing values."""
    now = utcnow()

    def creator(name, platform="youtube", status=CreatorStatus.COMPLETED, **fields):
        return Creator(channel_name=f"Dash {name}", platform=platform, channel_url=f"https://example.com/{name}",
                       normalized_identifier=f"dash-{name}", status=status, **fields)

    rows = [
        creator("yt-complete", genre="Finance & Investment", language="Hindi", sentiment="Positive", engagement_rate=4.0,
                engagement_rate_basis="views", subscriber_count=1000, analyzed_at=now - timedelta(minutes=1)),
        creator("ig-complete", "instagram", genre="Finance & Investment", language="English", sentiment="Neutral",
                engagement_rate=2.0, engagement_rate_basis="views", analyzed_at=now - timedelta(minutes=5)),
        creator("ig-followers", "instagram", genre="News", language="English", sentiment="Neutral", engagement_rate=9.0,
                engagement_rate_basis="followers", genre_needs_review=True, analyzed_at=now - timedelta(minutes=9)),
        creator("ig-partial", "instagram", CreatorStatus.PARTIAL, genre=None, language=None, sentiment=None,
                error_message="AI analysis unavailable"),
        creator("ig-failed", "instagram", CreatorStatus.FAILED, error_message="Instagram data unavailable"),
        creator("ai-pending", "youtube", CreatorStatus.PARTIAL,
                issues=["AI analysis pending: Groq daily limit reached"], content_sample={"items": []}),
        creator("pending", "youtube", CreatorStatus.PENDING),
        creator("broken", "invalid", CreatorStatus.FAILED, error_message="Channel link is not a valid URL"),
    ]
    with session_scope() as db:
        db.add_all(rows)
        db.flush()
        ids = [r.id for r in rows]
    yield ids
    with session_scope() as db:
        for cid in ids:
            db.delete(db.get(Creator, cid))


def _list_total(api, **filters) -> int:
    response = api.get("/api/creators", params={"page_size": 10, **filters})
    assert response.status_code == 200
    return response.json()["total"]


def test_dashboard_numbers_match_the_creator_list(api, seeded):
    data = api.get(DASHBOARD).json()
    assert data["total"] == _list_total(api)
    for key, filter_name in (("by_platform", "platform"), ("by_status", "status"), ("by_genre", "genre"),
                             ("by_language", "language"), ("by_sentiment", "sentiment")):
        for item in data[key]:
            if item["value"] is not None:  # "Not analysed" has no list filter
                assert item["count"] == _list_total(api, **{filter_name: item["value"]}), (key, item)

    statuses = {i["value"]: i["count"] for i in data["by_status"]}
    assert data["analysed"] == statuses.get("Completed", 0) + statuses.get("Partial", 0)
    assert data["needs_attention"] == statuses.get("Failed", 0) + statuses.get("Partial", 0)
    assert data["in_progress"] >= 1 and data["genre_needs_review"] >= 1 and data["ai_pending"] >= 1


def test_distributions_handle_missing_values_and_invalid_links(api, seeded):
    data = api.get(DASHBOARD).json()
    platforms = {i["value"]: i["label"] for i in data["by_platform"]}
    assert platforms["youtube"] == "YouTube" and platforms["invalid"] == "Invalid link"
    for key in ("by_genre", "by_language", "by_sentiment"):
        labels = [i["label"] for i in data[key]]
        assert labels[-1] == "Not analysed" and data[key][-1]["value"] is None  # missing values listed last
        # AI fields are counted for YouTube / Instagram creators only, never for invalid links
        assert sum(i["count"] for i in data[key]) == sum(c for v, c in Counter(
            {i["value"]: i["count"] for i in data["by_platform"]}).items() if v in ("youtube", "instagram"))
    assert [i["value"] for i in data["by_sentiment"] if i["value"]][:2] == ["Positive", "Neutral"]


def test_lists_and_engagement(api, seeded):
    data = api.get(DASHBOARD).json()
    recent = [c["channel_name"] for c in data["recently_analysed"]]
    assert recent[:3] == ["Dash yt-complete", "Dash ig-complete", "Dash ig-followers"]  # newest analysis first
    assert all(c["analyzed_at"] for c in data["recently_analysed"])
    attention = {c["channel_name"]: c for c in data["attention"]}
    assert {"Dash ig-failed", "Dash ig-partial", "Dash broken"} <= attention.keys()
    assert attention["Dash ig-failed"]["error_message"] == "Instagram data unavailable"
    assert all(c["status"] in ("Failed", "Partial") for c in data["attention"])
    # follower-based engagement (9.0) is not mixed into the views-based average
    assert data["engagement_sample"] >= 2 and data["average_engagement_rate"] is not None


def test_opening_the_dashboard_makes_no_external_calls(api, seeded, monkeypatch):
    async def blocked(*_args, **_kwargs):
        raise AssertionError("the dashboard must not call YouTube / Instagram / Groq")

    monkeypatch.setattr(httpx.AsyncClient, "request", blocked)
    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)
    assert api.get(DASHBOARD).status_code == 200


def test_empty_database(monkeypatch):
    monkeypatch.setattr(dashboard_service, "_grouped", lambda: [])
    monkeypatch.setattr(dashboard_service, "_creators", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(dashboard_service, "_ai_pending", lambda: 0)
    monkeypatch.setattr(dashboard_service, "_added", lambda: [])
    data = dashboard_service.creator_dashboard(30)
    assert data.total == 0 and data.analysed == 0 and data.average_engagement_rate is None
    assert data.by_platform == [] and data.by_genre == [] and data.recently_analysed == [] and data.last_updated_at is None
    assert data.added_in_range == 0 and len(data.growth) == 30 and all(p.total == 0 for p in data.growth)
    assert dashboard_service.creator_dashboard(0).growth[-1].total == 0  # all time, nothing added: one empty day


def test_growth_trend_and_added_in_period(api, seeded):
    from app.models.entities import Creator as CreatorModel

    with session_scope() as db:  # one seeded creator was added 40 days ago (outside a 30-day period)
        db.get(CreatorModel, seeded[0]).created_at = utcnow() - timedelta(days=40)
    month = api.get(DASHBOARD, params={"days": 30}).json()
    week = api.get(DASHBOARD, params={"days": 7}).json()
    fortnight = api.get(DASHBOARD, params={"days": 14}).json()
    assert len(fortnight["growth"]) == 14 and fortnight["growth"][-1]["total"] == fortnight["total"]
    everything = api.get(DASHBOARD, params={"days": 0}).json()

    assert len(month["growth"]) == 30 and len(week["growth"]) == 7
    assert month["growth"][-1]["total"] == month["total"] == everything["growth"][-1]["total"]  # cumulative
    assert month["growth"][0]["total"] >= 1  # creators added before the period are the starting point
    assert sum(p["added"] for p in month["growth"]) == month["added_in_range"] == month["total"] - month["growth"][0]["total"] + month["growth"][0]["added"]
    assert everything["added_in_range"] == everything["total"] and month["added_in_range"] < month["total"]
    completed = next(i["count"] for i in month["by_status"] if i["value"] == "Completed")
    assert month["growth"][-1]["analysed"] == completed  # "successfully analysed" = Completed
    assert sum(i["count"] for i in month["added_by_platform"]) == month["added_in_range"]
    assert api.get(DASHBOARD, params={"days": 400}).status_code == 422
    assert len(month["recently_analysed"]) <= 10
