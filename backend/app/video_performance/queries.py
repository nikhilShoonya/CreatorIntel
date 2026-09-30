"""Read queries for the Video Performance API (each opens its own read-only session)."""

import math
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from sqlalchemy import case, func, or_, select

from app.utils.time import utcnow
from app.video_performance.models import CreatorTrackingStatus, VideoStatus, VtCreator, VtSentiment, VtUpload, VtVideo, VtViewSnapshot
from app.video_performance.repository import read_scope
from app.video_performance.schemas import (
    CreatorOut,
    GroupSentiment,
    RecentSentiment,
    SentimentCounts,
    SnapshotOut,
    UploadOut,
    VideoOut,
)
from app.video_performance.timeutil import local_day_start_utc

SortKey = Literal["current_views", "views_gained", "growth_pct", "engagement_rate", "last_checked_at", "created_at", "published_at"]
_ACTIVE = (VideoStatus.PENDING, VideoStatus.PROCESSING, VideoStatus.TRACKING, VideoStatus.PARTIAL, VideoStatus.FAILED)


@dataclass
class VideoFilters:
    q: str | None = None
    platform: str | None = None
    creator: str | None = None
    creator_id: int | None = None
    status: str | None = None
    sort_by: SortKey | None = None
    sort_dir: Literal["asc", "desc"] = "desc"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def list_videos(filters: VideoFilters, page: int, page_size: int) -> tuple[list[VideoOut], int, int, dict[str, int]]:
    stmt = select(VtVideo)
    if filters.q:
        term = f"%{_escape(filters.q.strip().lower())}%"
        stmt = stmt.where(
            or_(
                func.lower(VtVideo.title).like(term, escape="\\"),
                func.lower(VtVideo.caption).like(term, escape="\\"),
                func.lower(VtVideo.creator_name).like(term, escape="\\"),
                func.lower(VtVideo.video_url).like(term, escape="\\"),
                func.lower(VtVideo.owner_username).like(term, escape="\\"),
            )
        )
    if filters.platform:
        stmt = stmt.where(VtVideo.platform == filters.platform)
    if filters.creator:
        stmt = stmt.where(VtVideo.creator_name == filters.creator)
    if filters.creator_id:
        stmt = stmt.where(VtVideo.creator_id == filters.creator_id)
    status_stmt = stmt  # counts per status respect the other filters
    if filters.status:
        stmt = stmt.where(VtVideo.status == filters.status)

    if filters.sort_by:
        column = getattr(VtVideo, filters.sort_by)
        ordered = column.asc() if filters.sort_dir == "asc" else column.desc()
        stmt = stmt.order_by(ordered.nulls_last(), VtVideo.id.desc())
    else:
        stmt = stmt.order_by(VtVideo.created_at.desc(), VtVideo.id.desc())

    with read_scope() as db:
        rows = db.execute(
            stmt.add_columns(func.count().over().label("total")).offset((page - 1) * page_size).limit(page_size)
        ).all()
        total = rows[0].total if rows else db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        counts_sub = status_stmt.with_only_columns(VtVideo.status).subquery()
        status_counts = {
            status: count
            for status, count in db.execute(select(counts_sub.c.status, func.count()).group_by(counts_sub.c.status))
        }
        items = [VideoOut.model_validate(row[0]) for row in rows]
    return items, total, max(1, math.ceil(total / page_size)), status_counts


def creator_names() -> list[str]:
    with read_scope() as db:
        return sorted(
            n for n in db.scalars(select(VtVideo.creator_name).where(VtVideo.creator_name.is_not(None)).distinct()) if n
        )


def video(video_id: int) -> VtVideo | None:
    with read_scope() as db:
        return db.get(VtVideo, video_id)


def history(video_id: int) -> list[SnapshotOut]:
    with read_scope() as db:
        snaps = db.scalars(
            select(VtViewSnapshot).where(VtViewSnapshot.video_id == video_id).order_by(VtViewSnapshot.captured_at)
        ).all()
    out, previous = [], None
    for snap in snaps:
        change = snap.views - previous if snap.views is not None and previous is not None else None
        out.append(
            SnapshotOut(
                captured_at=snap.captured_at, views=snap.views, likes=snap.likes, comments=snap.comments,
                engagement_rate=snap.engagement_rate, views_change=change,
            )
        )
        previous = snap.views if snap.views is not None else previous
    return list(reversed(out))  # newest first


def creators() -> list[CreatorOut]:
    with read_scope() as db:
        counts = dict(
            db.execute(
                select(VtVideo.creator_id, func.count()).where(VtVideo.creator_id.is_not(None)).group_by(VtVideo.creator_id)
            ).all()
        )
        result = []
        for creator in db.scalars(select(VtCreator).order_by(VtCreator.created_at.desc())):
            item = CreatorOut.model_validate(creator)
            item.video_count = counts.get(creator.id, 0)
            result.append(item)
        return result


def uploads(limit: int = 50) -> list[UploadOut]:
    with read_scope() as db:
        return [UploadOut.model_validate(u) for u in db.scalars(select(VtUpload).order_by(VtUpload.created_at.desc()).limit(limit))]


# ------------------------------------------------------------------ dashboard
def dashboard_totals() -> dict:
    day_start = local_day_start_utc()
    week_ago = utcnow() - timedelta(days=7)
    with read_scope() as db:
        row = db.execute(
            select(
                func.count(VtVideo.id),
                func.coalesce(func.sum(case((VtVideo.platform == "youtube", 1), else_=0)), 0),
                func.coalesce(func.sum(case((VtVideo.platform == "instagram", 1), else_=0)), 0),
                func.coalesce(func.sum(VtVideo.current_views), 0),
                func.coalesce(func.sum(case((VtVideo.last_checked_at >= day_start, VtVideo.views_gained), else_=0)), 0),
                func.coalesce(func.sum(case((VtVideo.last_checked_at >= day_start, 1), else_=0)), 0),
                func.coalesce(func.sum(case((and_discovered(week_ago), 1), else_=0)), 0),
                func.coalesce(func.sum(case((and_discovered(day_start), 1), else_=0)), 0),
                func.coalesce(func.sum(case((VtVideo.status.in_(_ACTIVE), 1), else_=0)), 0),
            )
        ).one()
        active_creators = db.scalar(
            select(func.count(VtCreator.id)).where(VtCreator.enabled.is_(True), VtCreator.status == CreatorTrackingStatus.ACTIVE)
        ) or 0
    keys = (
        "total_videos", "youtube_videos", "instagram_videos", "total_current_views", "views_gained_today",
        "videos_checked_today", "new_videos_7d", "new_videos_today", "active_trackings",
    )
    return {**{k: int(v or 0) for k, v in zip(keys, row)}, "active_creators": int(active_creators)}


def and_discovered(since):
    return (VtVideo.source == "discovered") & (VtVideo.discovered_at >= since)


def _counts(pairs: list[tuple[str | None, int]]) -> SentimentCounts:
    counts = SentimentCounts()
    for sentiment, n in pairs:
        attr = {"Positive": "positive", "Neutral": "neutral", "Negative": "negative"}.get(sentiment or "", "not_analyzed")
        setattr(counts, attr, getattr(counts, attr) + n)
    return counts


def sentiment_breakdown() -> tuple[SentimentCounts, list[GroupSentiment], list[GroupSentiment]]:
    with read_scope() as db:
        by_platform = db.execute(
            select(VtVideo.platform, VtVideo.sentiment, func.count()).group_by(VtVideo.platform, VtVideo.sentiment)
        ).all()
        by_creator = db.execute(
            select(VtVideo.creator_name, VtVideo.platform, VtVideo.sentiment, func.count())
            .where(VtVideo.creator_name.is_not(None))
            .group_by(VtVideo.creator_name, VtVideo.platform, VtVideo.sentiment)
        ).all()
    overall = _counts([(s, n) for _, s, n in by_platform])
    platforms = []
    for platform in ("youtube", "instagram"):
        pairs = [(s, n) for p, s, n in by_platform if p == platform]
        if pairs:
            platforms.append(GroupSentiment(name=platform, platform=platform, counts=_counts(pairs), total=sum(n for _, n in pairs)))
    grouped: dict[tuple[str, str], list[tuple[str | None, int]]] = {}
    for name, platform, sentiment, n in by_creator:
        grouped.setdefault((name, platform), []).append((sentiment, n))
    creators_ = [
        GroupSentiment(name=name, platform=platform, counts=_counts(pairs), total=sum(n for _, n in pairs))
        for (name, platform), pairs in grouped.items()
    ]
    creators_.sort(key=lambda g: (-g.total, g.name.lower()))
    return overall, platforms, creators_[:10]


def recent_sentiment(limit: int = 8) -> list[RecentSentiment]:
    with read_scope() as db:
        rows = db.execute(
            select(VtSentiment, VtVideo)
            .join(VtVideo, VtVideo.id == VtSentiment.video_id)
            .where(VtSentiment.sentiment.is_not(None))
            .order_by(VtSentiment.analyzed_at.desc())
            .limit(limit)
        ).all()
        return [
            RecentSentiment(
                video_id=v.id, display_title=VideoOut.model_validate(v).display_title, video_url=v.video_url,
                platform=v.platform, creator_name=v.creator_name, sentiment=s.sentiment, confidence=s.confidence,
                analyzed_at=s.analyzed_at,
            )
            for s, v in rows
        ]


def ranked(kind: str, limit: int = 5) -> list[VideoOut]:
    stmt = select(VtVideo)
    if kind == "top":
        stmt = stmt.where(VtVideo.current_views.is_not(None)).order_by(VtVideo.current_views.desc())
    elif kind == "growing":
        stmt = stmt.where(VtVideo.growth_pct.is_not(None), VtVideo.previous_views >= 100).order_by(
            VtVideo.growth_pct.desc(), VtVideo.views_gained.desc()
        )
    elif kind == "engagement":
        stmt = stmt.where(VtVideo.engagement_rate.is_not(None), VtVideo.current_views >= 100).order_by(
            VtVideo.engagement_rate.desc()
        )
    else:  # latest detected
        stmt = stmt.where(VtVideo.source == "discovered").order_by(VtVideo.discovered_at.desc())
    with read_scope() as db:
        return [VideoOut.model_validate(v) for v in db.scalars(stmt.limit(limit))]
