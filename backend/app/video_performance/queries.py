"""Read queries for the Video Performance API (each opens its own read-only session)."""

import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Literal

from sqlalchemy import and_, case, func, or_, select, union_all

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
from app.video_performance.platforms import META_WAIT
from app.video_performance.repository import STALE_COUNT_WINDOW, is_outdated_count, read_scope
from app.video_performance.schemas import (
    CreatorOut,
    GroupSentiment,
    EXACT_VIEW_PRECISIONS,
    PlatformViewsOut,
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
from app.video_performance.share_links import resolved_link_from_note
from app.video_performance.urls import parse_video_link

SortKey = Literal["current_views", "views_gained", "growth_pct", "engagement_rate", "last_checked_at", "created_at", "published_at"]
_ACTIVE = (VideoStatus.PENDING, VideoStatus.PROCESSING, VideoStatus.TRACKING, VideoStatus.PARTIAL, VideoStatus.FAILED)
UPLOAD_TOTAL_PLATFORMS = ("youtube", "instagram", "facebook")


@dataclass
class VideoFilters:
    q: str | None = None
    platform: str | None = None
    creator: str | None = None
    creator_id: int | None = None
    status: str | None = None
    sort_by: SortKey | None = None
    sort_dir: Literal["asc", "desc"] = "desc"
    video_ids: list[int] | None = None  # only these videos, shown in this order (e.g. one uploaded file)


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
    if filters.video_ids is not None:
        stmt = stmt.where(VtVideo.id.in_(filters.video_ids or [-1]))
    status_stmt = stmt  # counts per status respect the other filters
    if filters.status:
        stmt = stmt.where(VtVideo.status == filters.status)

    if filters.sort_by:
        column = getattr(VtVideo, filters.sort_by)
        ordered = column.asc() if filters.sort_dir == "asc" else column.desc()
        stmt = stmt.order_by(ordered.nulls_last(), VtVideo.id.desc())
    elif filters.video_ids:
        stmt = stmt.order_by(case({vid: n for n, vid in enumerate(filters.video_ids)}, value=VtVideo.id, else_=len(filters.video_ids)))
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
        total_pages = max(1, math.ceil(total / page_size))
        if page > total_pages and total:  # past the end (e.g. after deletes): show the last page instead
            items, total = _page(stmt, total_pages, page_size)
            total_pages = max(1, math.ceil(total / page_size))
        status_counts = counts_future.result()
    return items, total, total_pages, status_counts


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
    kept: list[tuple[datetime, int | None]] = []
    basis = None
    for snap in snaps:
        current_basis = (snap.metrics_source, snap.view_metric)
        if current_basis != basis:
            kept, previous = [], None
        basis = current_basis
        # An older, lower count returned by the API shortly after a higher one (saved before this was filtered).
        outdated = is_outdated_count(snap.views, kept, as_utc(snap.captured_at))
        change = snap.views - previous if not outdated and snap.views is not None and previous is not None else None
        out.append(
            SnapshotOut(
                captured_at=snap.captured_at, views=snap.views, likes=snap.likes, comments=snap.comments,
                engagement_rate=snap.engagement_rate, views_change=change, outdated=outdated,
                metrics_source=snap.metrics_source, view_metric=snap.view_metric, source_metrics=snap.source_metrics,
                plays=snap.plays, reactions=snap.reactions, shares=snap.shares, content_type=snap.content_type,
                profile_followers=snap.profile_followers, profile_page_likes=snap.profile_page_likes,
                views_precision=snap.views_precision, metric_status=snap.metric_status,
            )
        )
        if not outdated:
            kept.append((as_utc(snap.captured_at), snap.views))
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
    settings = get_settings()
    retention = settings.upload_file_retention_days
    if upload.file_deleted_at is None and upload.stored_filename and retention > 0:
        out.file_delete_after = as_utc(upload.created_at) + timedelta(days=retention)
    out.file_available = bool(
        upload.file_deleted_at is None and upload.stored_filename
        and (settings.upload_dir / Path(upload.stored_filename).name).is_file()
    )
    return out


def _meta_progress(db, outs: list[UploadOut]) -> list[UploadOut]:
    """Per upload: Instagram videos it added and how many still wait for Meta's hourly limit
    (one grouped query for all uploads)."""
    if not outs:
        return outs
    waiting = case((and_(VtVideo.status == VideoStatus.PENDING, VtVideo.status_reason.like(f"{META_WAIT}%")), 1), else_=0)
    rows = db.execute(
        select(VtVideo.upload_id, func.count(), func.sum(waiting))
        .where(VtVideo.upload_id.in_([o.id for o in outs]), VtVideo.platform == "instagram")
        .group_by(VtVideo.upload_id)
    ).all()
    by_id = {o.id: o for o in outs}
    for upload_id, total, waiting_count in rows:
        by_id[upload_id].instagram_total = int(total)
        by_id[upload_id].instagram_waiting = int(waiting_count or 0)
    return outs


def _batch_status(out: UploadOut) -> str:
    if out.videos_in_progress:
        return "processing"
    if out.videos_processed == 0 and (out.videos_failed or out.invalid):
        return "failed"
    return "partial" if out.videos_failed or out.invalid else "completed"


def _batch_progress(db, outs: list[UploadOut]) -> list[UploadOut]:
    """Per batch of pasted links: where its unique videos stand now (one grouped query for all batches)."""
    batches = {o.id: o for o in outs if o.upload_type == "links"}
    if not batches:
        return outs
    rows = db.execute(
        select(VtUploadRow.upload_id, VtVideo.status, func.count(VtVideo.id.distinct()))
        .join(VtVideo, VtUploadRow.video_id == VtVideo.id)
        .where(VtUploadRow.upload_id.in_(batches))
        .group_by(VtUploadRow.upload_id, VtVideo.status)
    ).all()
    for upload_id, status, count in rows:
        batch = batches[upload_id]
        batch.videos_total += count
        if status in VideoStatus.ACTIVE_WORK:
            batch.videos_in_progress += count
        elif status in VideoStatus.NO_DATA:
            batch.videos_failed += count
        else:
            batch.videos_processed += count
    for batch in batches.values():
        batch.processing_status = _batch_status(batch)
    return outs


def _with_progress(db, outs: list[UploadOut]) -> list[UploadOut]:
    return _batch_progress(db, _meta_progress(db, outs))


def uploads(limit: int | None = None) -> list[UploadOut]:
    """Uploads, newest first (all of them by default - uploads are never hidden from the website)."""
    with read_scope() as db:
        outs = [_upload_out(u) for u in db.scalars(select(VtUpload).order_by(VtUpload.created_at.desc()).limit(limit))]
        return _with_progress(db, outs)


def upload(upload_id: str) -> UploadOut | None:
    with read_scope() as db:
        found = db.get(VtUpload, upload_id)
        return _with_progress(db, [_upload_out(found)])[0] if found else None


def upload_video_ids(upload_id: str) -> list[int] | None:
    """Tracked videos that came from one upload, in its row order (None = unknown upload).

    A row points to its video through video_id. Rows without one (saved before video_id existed, or whose
    video was removed) are matched by their link, the same way the upload matched it (added, already tracked
    or a duplicate row all point to the same tracked video).
    """
    with read_scope() as db:
        if db.get(VtUpload, upload_id) is None:
            return None
        rows = db.execute(
            select(VtUploadRow.video_id, VtUploadRow.video_link, VtUploadRow.message, VtUploadRow.platform)
            .where(VtUploadRow.upload_id == upload_id, VtUploadRow.status != "invalid")
            .order_by(VtUploadRow.row_number)
        ).all()
        ordered: list[int | tuple[str, str]] = []
        for video_id, link, message, platform in rows:
            if video_id is not None:
                ordered.append(video_id)
                continue
            # A Facebook share link is stored as uploaded; its note records the real video link it resolved to.
            parsed = parse_video_link(resolved_link_from_note(message) or link, instagram_reel_hint=platform == "instagram")
            if parsed.ok:
                ordered.append((parsed.platform, parsed.identifier))
        keys = list(dict.fromkeys(entry for entry in ordered if isinstance(entry, tuple)))
        found: dict[tuple[str, str], int] = {}
        for start in range(0, len(keys), 200):
            chunk = keys[start : start + 200]
            condition = or_(*[(VtVideo.platform == p) & (VtVideo.video_identifier == i) for p, i in chunk])
            for vid, platform, identifier in db.execute(select(VtVideo.id, VtVideo.platform, VtVideo.video_identifier).where(condition)):
                found[(platform, identifier)] = vid
    ids = (found.get(entry) if isinstance(entry, tuple) else entry for entry in ordered)
    return list(dict.fromkeys(video_id for video_id in ids if video_id is not None))


def platform_view_totals(video_ids: list[int]) -> tuple[list[PlatformViewsOut], int]:
    """Summed views per platform for exactly these videos, plus how many are still being checked.

    A missing count (not checked yet, failed, video down) or an impossible negative one adds nothing
    to the total; `videos_with_views` shows how much of the platform the total covers.
    """
    usable_views = case((VtVideo.current_views >= 0, VtVideo.current_views))
    busy = case((VtVideo.status.in_(VideoStatus.ACTIVE_WORK), 1), else_=0)
    rounded = case((and_(VtVideo.current_views >= 0, VtVideo.views_precision.not_in(EXACT_VIEW_PRECISIONS)), 1), else_=0)
    with read_scope() as db:
        rows = db.execute(
            select(VtVideo.platform, func.count(), func.count(usable_views), func.sum(usable_views), func.sum(busy), func.sum(rounded))
            .where(VtVideo.id.in_(video_ids or [-1]))
            .group_by(VtVideo.platform)
        ).all()
    by_platform = {row[0]: row for row in rows}
    totals = []
    for platform in UPLOAD_TOTAL_PLATFORMS:
        _, videos, with_views, views, _, rounded_count = by_platform.get(platform, (platform, 0, 0, 0, 0, 0))
        totals.append(PlatformViewsOut(platform=platform, total_views=int(views or 0), videos=videos, videos_with_views=with_views,
                                       videos_rounded=int(rounded_count or 0)))
    return totals, sum(int(row[4] or 0) for row in rows)


def _row_outcome(row_status: str, video_status: str | None) -> str:
    if row_status in ("invalid", "duplicate"):
        return row_status
    if video_status is None:
        return "removed"
    if video_status in VideoStatus.ACTIVE_WORK:
        return "processing"
    return "failed" if video_status in VideoStatus.NO_DATA else "processed"


def upload_rows(upload_id: str) -> tuple[UploadOut, list[UploadRowOut]] | None:
    with read_scope() as db:
        upload = db.get(VtUpload, upload_id)
        if upload is None:
            return None
        rows = db.execute(
            select(VtUploadRow, VtVideo.status, VtVideo.status_reason)
            .outerjoin(VtVideo, VtUploadRow.video_id == VtVideo.id)
            .where(VtUploadRow.upload_id == upload_id)
            .order_by(VtUploadRow.row_number)
        ).all()
        out = _with_progress(db, [_upload_out(upload)])[0]
        results = []
        for row, video_status, video_reason in rows:
            item = UploadRowOut.model_validate(row)
            item.video_status, item.video_status_reason = video_status, video_reason
            if out.upload_type == "links":
                item.outcome = _row_outcome(row.status, video_status)
            results.append(item)
        return out, results


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
                func.coalesce(func.sum(case((VtVideo.platform == "facebook", 1), else_=0)), 0),
                func.coalesce(func.sum(VtVideo.current_views), 0),
                func.coalesce(func.sum(case((VtVideo.last_checked_at >= day_start, VtVideo.views_gained), else_=0)), 0),
                func.coalesce(func.sum(case((VtVideo.last_checked_at >= day_start, 1), else_=0)), 0),
                func.coalesce(func.sum(case((and_discovered(week_ago), 1), else_=0)), 0),
                func.coalesce(func.sum(case((and_discovered(day_start), 1), else_=0)), 0),
                func.coalesce(func.sum(case((VtVideo.status.in_(_ACTIVE), 1), else_=0)), 0),
                func.coalesce(func.sum(case((VtVideo.status == VideoStatus.VIDEO_DOWN, 1), else_=0)), 0),
            )
        ).one()
        active_creators = db.scalar(
            select(func.count(VtCreator.id)).where(VtCreator.enabled.is_(True), VtCreator.status == CreatorTrackingStatus.ACTIVE)
        ) or 0
    keys = (
        "total_videos", "youtube_videos", "instagram_videos", "facebook_videos", "total_current_views", "views_gained_today",
        "videos_checked_today", "new_videos_7d", "new_videos_today", "active_trackings", "videos_down",
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
    for platform in ("youtube", "instagram", "facebook"):
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
    columns = (S.video_id, S.captured_at, S.views, S.likes, S.comments, S.metrics_source, S.view_metric)
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


def _without_outdated_counts(snapshots: list) -> list:
    """Drop readings lower than one taken shortly before (outdated API counts), oldest-first input."""
    recent: dict[int, list[tuple[datetime, int | None]]] = {}
    kept = []
    for row in snapshots:
        at = as_utc(row[1])
        identity = (row[0], *row[5:])
        seen = [(t, n) for t, n in recent.get(identity, []) if at - STALE_COUNT_WINDOW <= t]
        if is_outdated_count(row[2], seen, at):
            recent[identity] = seen
            continue
        seen.append((at, row[2]))
        recent[identity] = seen
        kept.append(row)
    return kept


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
    for video_id, captured_at, views, likes, comments, source, metric in _without_outdated_counts(snapshots):
        if as_utc(captured_at) < start:
            state[video_id] = (views, likes, comments, (source, metric))
        else:
            per_day.setdefault(local(captured_at), {})[video_id] = (views, likes, comments, (source, metric))
    created = [(local(v.created_at), v.platform) for v in videos]
    discovered: dict[date, int] = {}
    for v in videos:
        if v.source == "discovered" and v.discovered_at:
            day = local(v.discovered_at)
            discovered[day] = discovered.get(day, 0) + 1

    baseline = {video_id: values[0] for video_id, values in state.items()}
    baseline_basis = {video_id: values[3] for video_id, values in state.items()}
    changed_basis = set()
    gains: dict[int, list[int | None]] = {}
    points: list[TrendPoint] = []
    for index, day in enumerate(day_list):
        checks = per_day.get(day, {})
        known = set(state)
        gained = 0
        for video_id, values in checks.items():
            if video_id in known and values[3] != state[video_id][3]:
                changed_basis.add(video_id)
            if video_id in known and values[3] == state[video_id][3]:
                delta = values[0] - state[video_id][0]
                gains.setdefault(video_id, [None] * days)[index] = delta
                gained += delta
            state[video_id] = values
        for video_id in known - checks.keys():  # tracked but not checked that day
            gains.setdefault(video_id, [None] * days)[index] = 0
        engaged = [(likes + comments, views) for views, likes, comments, _basis in state.values()
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
                facebook_videos=existing.count("facebook"),
                new_videos=discovered.get(day, 0),
                videos_checked=len(checks),
            )
        )

    comparable_ids = [vid for vid in baseline if vid not in changed_basis and state[vid][3] == baseline_basis[vid]]
    base_total = sum(baseline[vid] for vid in comparable_ids)
    now_total = sum(state[video_id][0] for video_id in comparable_ids)
    return Trend(
        points=points,
        videos_added=sum(1 for created_day, _ in created if created_day >= day_list[0]),
        views_gained=sum(p.views_gained for p in points),
        views_growth_pct=round((now_total - base_total) / base_total * 100, 2) if base_total else None,
        video_gains=gains,
    )
