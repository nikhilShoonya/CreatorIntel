"""Read queries for the Video Performance API (each opens its own read-only session)."""

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal

from sqlalchemy import case, func, or_, select, union_all

from app.config.settings import get_settings
from app.utils.time import as_utc, utcnow
from app.video_performance.models import (
    CreatorTrackingStatus,
    VideoStatus,
    VtCreator,
    VtSentiment,
    VtUpload,
    VtUploadRow,
    VtVideo,
    VtViewSnapshot,
)
from app.video_performance.repository import read_scope
from app.video_performance.schemas import (
    CreatorOut,
    GroupSentiment,
    RecentSentiment,
    SentimentCounts,
    SnapshotOut,
    TrendPoint,
    UploadOut,
    UploadRowOut,
    VideoOut,
    youtube_thumbnail,
)
from app.video_performance.timeutil import local_day_start_utc, tracking_tz

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


def _video_query(filters: VideoFilters):
    """(filtered + sorted statement, statement for per-status counts)."""
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
    return stmt, status_stmt


def export_videos(filters: VideoFilters, limit: int = 20000) -> list[VtVideo]:
    stmt, _ = _video_query(filters)
    with read_scope() as db:
        return list(db.scalars(stmt.limit(limit)))


def list_videos(filters: VideoFilters, page: int, page_size: int) -> tuple[list[VideoOut], int, int, dict[str, int]]:
    stmt, status_stmt = _video_query(filters)
    counts_sub = status_stmt.with_only_columns(VtVideo.status).subquery()
    with ThreadPoolExecutor(max_workers=1) as pool:
        counts_future = pool.submit(_status_counts, select(counts_sub.c.status, func.count()).group_by(counts_sub.c.status))
        items, total = _page(stmt, page, page_size)
        status_counts = counts_future.result()
    return items, total, max(1, math.ceil(total / page_size)), status_counts


def _status_counts(stmt) -> dict[str, int]:
    with read_scope() as db:
        return {status: count for status, count in db.execute(stmt)}


def _page(stmt, page: int, page_size: int) -> tuple[list[VideoOut], int]:
    with read_scope() as db:
        rows = db.execute(
            stmt.add_columns(func.count().over().label("total")).offset((page - 1) * page_size).limit(page_size)
        ).all()
        total = rows[0].total if rows else db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
        return [VideoOut.model_validate(row[0]) for row in rows], total


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


def _upload_out(upload: VtUpload) -> UploadOut:
    out = UploadOut.model_validate(upload)
    retention = get_settings().upload_file_retention_days
    if upload.file_deleted_at is None and upload.stored_filename and retention > 0:
        out.file_delete_after = as_utc(upload.created_at) + timedelta(days=retention)
    return out


def uploads(limit: int = 50) -> list[UploadOut]:
    with read_scope() as db:
        return [_upload_out(u) for u in db.scalars(select(VtUpload).order_by(VtUpload.created_at.desc()).limit(limit))]


def upload_rows(upload_id: str) -> tuple[UploadOut, list[UploadRowOut]] | None:
    with read_scope() as db:
        upload = db.get(VtUpload, upload_id)
        if upload is None:
            return None
        rows = db.scalars(
            select(VtUploadRow).where(VtUploadRow.upload_id == upload_id).order_by(VtUploadRow.row_number)
        ).all()
        return _upload_out(upload), [UploadRowOut.model_validate(r) for r in rows]


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


def sentiment_breakdown() -> tuple[SentimentCounts, list[GroupSentiment]]:
    with read_scope() as db:
        by_platform = db.execute(
            select(VtVideo.platform, VtVideo.sentiment, func.count()).group_by(VtVideo.platform, VtVideo.sentiment)
        ).all()
    overall = _counts([(s, n) for _, s, n in by_platform])
    platforms = []
    for platform in ("youtube", "instagram"):
        pairs = [(s, n) for p, s, n in by_platform if p == platform]
        if pairs:
            platforms.append(GroupSentiment(name=platform, platform=platform, counts=_counts(pairs), total=sum(n for _, n in pairs)))
    return overall, platforms


def recent_sentiment(limit: int = 20) -> list[RecentSentiment]:
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
                analyzed_at=s.analyzed_at, thumbnail_url=youtube_thumbnail(v.platform, v.video_identifier),
            )
            for s, v in rows
        ]


def ranked(kind: str, limit: int = 5) -> list[VideoOut]:
    stmt = select(VtVideo)
    if kind == "top":
        stmt = stmt.where(VtVideo.current_views.is_not(None)).order_by(VtVideo.current_views.desc())
    elif kind == "engagement":
        stmt = stmt.where(VtVideo.engagement_rate.is_not(None), VtVideo.current_views >= 100).order_by(
            VtVideo.engagement_rate.desc()
        )
    else:  # latest detected
        stmt = stmt.where(VtVideo.source == "discovered").order_by(VtVideo.discovered_at.desc())
    with read_scope() as db:
        return [VideoOut.model_validate(v) for v in db.scalars(stmt.limit(limit))]

# ------------------------------------------------------------------ trend
@dataclass
class Trend:
    points: list[TrendPoint]
    videos_added: int = 0
    views_gained: int = 0
    views_growth_pct: float | None = None
    video_gains: dict[int, list[int | None]] = field(default_factory=dict)


def _trend_snapshots(start: datetime) -> list:
    """Each video's latest check before `start` (where the trend starts from) plus every check since, oldest first."""
    S = VtViewSnapshot
    columns = (S.video_id, S.captured_at, S.views, S.likes, S.comments)
    latest = (
        select(S.video_id, func.max(S.captured_at).label("at"))
        .where(S.captured_at < start, S.views.is_not(None))
        .group_by(S.video_id)
        .subquery()
    )
    before = select(*columns).join(latest, (S.video_id == latest.c.video_id) & (S.captured_at == latest.c.at))
    since = select(*columns).where(S.captured_at >= start, S.views.is_not(None))
    combined = union_all(before, since).subquery()
    with read_scope() as db:
        return db.execute(select(combined).order_by(combined.c.captured_at)).all()


def _video_dates() -> list:
    with read_scope() as db:
        return db.execute(select(VtVideo.platform, VtVideo.created_at, VtVideo.source, VtVideo.discovered_at)).all()


def trend(days: int) -> Trend:
    """Daily totals for the last `days` local days, built only from recorded view checks (no estimates)."""
    tz = tracking_tz()
    today = datetime.now(timezone.utc).astimezone(tz).date()
    day_list = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    start = datetime.combine(day_list[0], time.min, tzinfo=tz).astimezone(timezone.utc)
    with ThreadPoolExecutor(max_workers=1) as pool:  # both reads in parallel (remote DB latency)
        videos_future = pool.submit(_video_dates)
        snapshots = _trend_snapshots(start)
        videos = videos_future.result()

    def local(value: datetime) -> date:
        return as_utc(value).astimezone(tz).date()

    state: dict[int, tuple] = {}  # each video's latest (views, likes, comments) so far
    per_day: dict[date, dict[int, tuple]] = {}  # latest check of each video on each day of the range
    for video_id, captured_at, views, likes, comments in snapshots:
        if as_utc(captured_at) < start:
            state[video_id] = (views, likes, comments)
        else:
            per_day.setdefault(local(captured_at), {})[video_id] = (views, likes, comments)
    created = [(local(v.created_at), v.platform) for v in videos]
    discovered: dict[date, int] = {}
    for v in videos:
        if v.source == "discovered" and v.discovered_at:
            day = local(v.discovered_at)
            discovered[day] = discovered.get(day, 0) + 1

    baseline = {video_id: values[0] for video_id, values in state.items()}
    gains: dict[int, list[int | None]] = {}
    points: list[TrendPoint] = []
    for index, day in enumerate(day_list):
        checks = per_day.get(day, {})
        known = set(state)
        gained = 0
        for video_id, values in checks.items():
            if video_id in known:  # the first ever check of a video is not a gain
                delta = values[0] - state[video_id][0]
                gains.setdefault(video_id, [None] * days)[index] = delta
                gained += delta
            state[video_id] = values
        for video_id in known - checks.keys():  # tracked but not checked that day
            gains.setdefault(video_id, [None] * days)[index] = 0
        engaged = [(likes + comments, views) for views, likes, comments in state.values()
                   if views and likes is not None and comments is not None]
        engaged_views = sum(views for _, views in engaged)
        existing = [platform for created_day, platform in created if created_day <= day]
        points.append(
            TrendPoint(
                day=day,
                total_views=sum(values[0] for values in state.values()),
                views_gained=gained,
                engagement_rate=round(sum(e for e, _ in engaged) / engaged_views * 100, 2) if engaged_views else None,
                videos=len(existing),
                youtube_videos=existing.count("youtube"),
                instagram_videos=existing.count("instagram"),
                new_videos=discovered.get(day, 0),
                videos_checked=len(checks),
            )
        )

    base_total = sum(baseline.values())
    now_total = sum(state[video_id][0] for video_id in baseline)
    return Trend(
        points=points,
        videos_added=sum(1 for created_day, _ in created if created_day >= day_list[0]),
        views_gained=sum(p.views_gained for p in points),
        views_growth_pct=round((now_total - base_total) / base_total * 100, 2) if base_total else None,
        video_gains=gains,
    )
