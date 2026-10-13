"""Creator Analytics dashboard: totals and distributions over ALL stored creators.

Only reads the database - opening the dashboard never calls YouTube / Instagram or the AI. Every count uses the
same stored values the Creator List filters on, so a dashboard number equals the list total for that filter.
"""

from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import defer

from app.agents.content_analysis_agent import PENDING_PREFIX
from app.config.settings import get_settings
from app.models.db import ReadSessionLocal
from app.models.entities import Creator, CreatorStatus
from app.schemas.api import CountItem, CreatorDashboardOut, DashboardCreatorOut, GrowthPoint
from app.utils.time import as_utc

LIST_SIZE = 8
RECENT_SIZE = 10
MAX_GROWTH_DAYS = 366  # "all time" trend: at most the last year, one point per day
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


def _creators(order, *conditions, limit: int = LIST_SIZE) -> list[DashboardCreatorOut]:
    with ReadSessionLocal() as db:
        rows = db.scalars(
            select(Creator).where(*conditions).options(*(defer(c) for c in _DEFERRED)).order_by(order).limit(limit)
        ).all()
        return [DashboardCreatorOut.model_validate(row) for row in rows]


def _added() -> list:
    """(created_at, platform, status) of every creator - for growth over time (when each was added)."""
    with ReadSessionLocal() as db:
        return db.execute(select(Creator.created_at, Creator.platform, Creator.status)).all()


def _growth(rows: list, days: int, tz: ZoneInfo) -> tuple[list[GrowthPoint], list]:
    """Daily cumulative series over the period (all time when days == 0) and the creators added in it."""
    today = datetime.now(timezone.utc).astimezone(tz).date()
    added = sorted((as_utc(created).astimezone(tz).date(), status) for created, _platform, status in rows if created)
    if days > 0:
        start = today - timedelta(days=days - 1)
    else:
        start = added[0][0] if added else today
        start = max(start, today - timedelta(days=MAX_GROWTH_DAYS - 1))
    per_day: dict[date, list[int]] = defaultdict(lambda: [0, 0])
    total = analysed = 0
    for day, status in added:
        if day < start:
            total += 1
            analysed += status == CreatorStatus.COMPLETED
        else:
            per_day[day][0] += 1
            per_day[day][1] += status == CreatorStatus.COMPLETED
    points = []
    for offset in range((today - start).days + 1):
        day = start + timedelta(days=offset)
        new, new_ok = per_day.get(day, (0, 0))
        total += new
        analysed += new_ok
        points.append(GrowthPoint(day=day.isoformat(), total=total, analysed=analysed, added=new))
    in_range = [row for row in rows if row[0] and as_utc(row[0]).astimezone(tz).date() >= start] if days > 0 else rows
    return points, in_range


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


def creator_dashboard(days: int = 0) -> CreatorDashboardOut:
    """`days` = period for "added" numbers and the growth trend (0 = all time); totals are always all-time."""
    settings = get_settings()
    tz = ZoneInfo(settings.video_tracking_timezone)
    with ThreadPoolExecutor(max_workers=4) as pool:  # independent reads in parallel (remote DB latency)
        recent_future = pool.submit(
            _creators, Creator.analyzed_at.desc(), Creator.analyzed_at.is_not(None), limit=RECENT_SIZE
        )
        attention_future = pool.submit(
            _creators, Creator.updated_at.desc(), Creator.status.in_((CreatorStatus.FAILED, CreatorStatus.PARTIAL))
        )
        pending_future = pool.submit(_ai_pending)
        added_future = pool.submit(_added)
        groups = _grouped()
        recent, attention, ai_pending = recent_future.result(), attention_future.result(), pending_future.result()
        growth, in_range = _growth(added_future.result(), days, tz)

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
        range_days=days,
        added_in_range=len(in_range),
        added_by_platform=_items(Counter(row[1] for row in in_range), ("youtube", "instagram"), PLATFORM_LABELS),
        growth=growth,
        timezone=settings.video_tracking_timezone,
    )
