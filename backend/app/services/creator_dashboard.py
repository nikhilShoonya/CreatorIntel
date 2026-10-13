"""Creator Analytics dashboard: totals and distributions over ALL stored creators.

Only reads the database - opening the dashboard never calls YouTube / Instagram or the AI. Every count uses the
same stored values the Creator List filters on, so a dashboard number equals the list total for that filter.
"""

from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import case, func, select
from sqlalchemy.orm import defer

from app.agents.content_analysis_agent import PENDING_PREFIX
from app.models.db import ReadSessionLocal
from app.models.entities import Creator, CreatorStatus
from app.schemas.api import CountItem, CreatorDashboardOut, DashboardCreatorOut

LIST_SIZE = 8
STATUS_ORDER = (CreatorStatus.COMPLETED, CreatorStatus.PARTIAL, CreatorStatus.FAILED,
                CreatorStatus.PROCESSING, CreatorStatus.PENDING)
SENTIMENT_ORDER = ("Positive", "Neutral", "Negative", "Unknown")
PLATFORM_LABELS = {"youtube": "YouTube", "instagram": "Instagram", "invalid": "Invalid link", "unsupported": "Unsupported link"}
NOT_ANALYSED = "Not analysed"
_DEFERRED = (Creator.content_sample, Creator.provenance, Creator.issues, Creator.evidence_topics)


def _grouped() -> list:
    """Counts per (platform, status, genre, language, sentiment) - the distinct combinations are few."""
    views_rate = case((Creator.engagement_rate_basis == "views", Creator.engagement_rate), else_=None)
    with ReadSessionLocal() as db:
        return db.execute(
            select(
                Creator.platform, Creator.status, Creator.genre, Creator.language, Creator.sentiment,
                func.count(), func.sum(case((Creator.genre_needs_review.is_(True), 1), else_=0)),
                func.sum(views_rate), func.count(views_rate), func.max(Creator.updated_at),
            ).group_by(Creator.platform, Creator.status, Creator.genre, Creator.language, Creator.sentiment)
        ).all()


def _creators(order, *conditions) -> list[DashboardCreatorOut]:
    with ReadSessionLocal() as db:
        rows = db.scalars(
            select(Creator).where(*conditions).options(*(defer(c) for c in _DEFERRED)).order_by(order).limit(LIST_SIZE)
        ).all()
        return [DashboardCreatorOut.model_validate(row) for row in rows]


def _ai_pending() -> int:
    """Creators whose AI analysis is waiting for Groq quota (same rule as the automatic retry loop)."""
    with ReadSessionLocal() as db:
        rows = db.execute(
            select(Creator.issues).where(Creator.analyzed_at.is_(None), Creator.issues.is_not(None))
        ).scalars().all()
    return sum(1 for issues in rows if any(PENDING_PREFIX in str(i) for i in (issues or [])))


def _items(counter: Counter, order: tuple[str, ...] = (), labels: dict[str, str] | None = None) -> list[CountItem]:
    """Known values first (in `order`, then by count), the "not analysed" bucket (None) last."""
    known = [k for k in counter if k is not None]
    known.sort(key=lambda k: (order.index(k) if k in order else len(order), -counter[k], k))
    items = [CountItem(value=k, label=(labels or {}).get(k, k), count=counter[k]) for k in known if counter[k]]
    if counter.get(None):
        items.append(CountItem(value=None, label=NOT_ANALYSED, count=counter[None]))
    return items


def creator_dashboard() -> CreatorDashboardOut:
    with ThreadPoolExecutor(max_workers=3) as pool:  # independent reads in parallel (remote DB latency)
        recent_future = pool.submit(
            _creators, Creator.analyzed_at.desc(), Creator.analyzed_at.is_not(None)
        )
        attention_future = pool.submit(
            _creators, Creator.updated_at.desc(), Creator.status.in_((CreatorStatus.FAILED, CreatorStatus.PARTIAL))
        )
        pending_future = pool.submit(_ai_pending)
        groups = _grouped()
        recent, attention, ai_pending = recent_future.result(), attention_future.result(), pending_future.result()

    platform, status, genre, language, sentiment = Counter(), Counter(), Counter(), Counter(), Counter()
    total = review = rate_count = 0
    rate_sum = 0.0
    last_updated = None
    for plat, stat, gen, lang, sent, n, needs_review, r_sum, r_count, updated in groups:
        total += n
        platform[plat] += n
        status[stat] += n
        # AI fields only exist for creators with platform data; invalid links are not "not analysed" content
        if plat in ("youtube", "instagram"):
            genre[gen or None] += n
            language[lang or None] += n
            sentiment[sent or None] += n
        review += int(needs_review or 0)
        rate_sum += float(r_sum or 0)
        rate_count += int(r_count or 0)
        if updated is not None and (last_updated is None or updated > last_updated):
            last_updated = updated

    return CreatorDashboardOut(
        total=total,
        analysed=status[CreatorStatus.COMPLETED] + status[CreatorStatus.PARTIAL],
        needs_attention=status[CreatorStatus.FAILED] + status[CreatorStatus.PARTIAL],
        in_progress=status[CreatorStatus.PENDING] + status[CreatorStatus.PROCESSING],
        genre_needs_review=review,
        ai_pending=ai_pending,
        average_engagement_rate=round(rate_sum / rate_count, 2) if rate_count else None,
        engagement_sample=rate_count,
        by_platform=_items(platform, ("youtube", "instagram"), PLATFORM_LABELS),
        by_status=_items(status, STATUS_ORDER),
        by_genre=_items(genre),
        by_language=_items(language),
        by_sentiment=_items(sentiment, SENTIMENT_ORDER),
        recently_analysed=recent,
        attention=attention,
        last_updated_at=last_updated,
    )
