"""Database access for Video Performance (sync functions; async callers run them in threads)."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.db import ReadSessionLocal, session_scope
from app.utils.logging import log_event
from app.utils.time import as_utc, utcnow
from app.utils.url_parser import ParsedLink
from app.video_performance.models import (
    CreatorTrackingStatus,
    VideoStatus,
    VtCreator,
    VtJobRun,
    VtSentiment,
    VtVideo,
    VtViewSnapshot,
)
from app.services import meta_budget
from app.video_performance.platforms import META_WAIT, ChannelInfo, DiscoveredVideo, PlatformError, VideoMetrics
from app.video_performance.sentiment import SentimentResult
from app.video_performance.timeutil import local_day_start_utc, tracking_tz
from app.video_performance.urls import ParsedVideo, parse_instagram_handle, parse_video_link


logger = logging.getLogger("creatorintel.video_performance.repository")


class Conflict(Exception):
    def __init__(self, message: str, existing_id: int | None = None):
        super().__init__(message)
        self.existing_id = existing_id


@contextmanager
def read_scope() -> Iterator[Session]:
    db = ReadSessionLocal()
    try:
        yield db
    finally:
        db.close()


def engagement(views: int | None, likes: int | None, comments: int | None) -> float | None:
    if not views or views <= 0 or likes is None or comments is None:
        return None
    rate = (likes + comments) / views * 100
    return round(rate, 2) if 0 <= rate <= 100 else None


# ------------------------------------------------------------------ processing
def instagram_owner_candidates() -> list[str]:
    """Instagram Professional accounts already known to the app, most useful first - searched for reels whose
    owner is unknown: owners of reels that were read successfully (by number of reels), creators tracked in
    Video Performance, then Instagram creators analysed in Creator Analytics."""
    from app.models.entities import Creator, CreatorStatus

    with read_scope() as db:
        owners = db.execute(
            select(VtVideo.owner_username, func.count())
            .where(VtVideo.platform == "instagram", VtVideo.owner_username.is_not(None), VtVideo.current_views.is_not(None))
            .group_by(VtVideo.owner_username)
            .order_by(func.count().desc())
        ).all()
        tracked = db.scalars(select(VtCreator.username).where(
            VtCreator.platform == "instagram", VtCreator.username.is_not(None),
            VtCreator.status == CreatorTrackingStatus.ACTIVE,
        ).order_by(VtCreator.id))
        analysed = db.scalars(
            select(Creator.normalized_identifier).where(
                Creator.platform == "instagram", Creator.status.in_((CreatorStatus.COMPLETED, CreatorStatus.PARTIAL))
            ).order_by(Creator.id)
        )
        names = [o for o, _ in owners] + list(tracked) + list(analysed)
    return list(dict.fromkeys(handle for n in names if (handle := parse_instagram_handle(n))))


def _has_metrics(video: VtVideo) -> bool:
    return any(value is not None for value in (video.current_views, video.likes, video.comments))


def _owner_verified(db: Session, video: VtVideo) -> bool:
    # An exact media match can have no available counts. Its saved snapshot still proves retrieval.
    return _has_metrics(video) or bool(db.scalar(select(exists().where(VtViewSnapshot.video_id == video.id))))


def _owner_metadata(db: Session, videos: list[VtVideo]) -> dict[int, dict[str, str]]:
    """Only exact Reel associations are trusted; display names and other upload rows are not owners."""
    from app.video_performance.models import VtUploadRow

    instagram = [v for v in videos if v.platform == "instagram"]
    by_code = {v.video_identifier: v for v in instagram}
    sources: dict[int, dict[str, str]] = {v.id: {} for v in instagram}
    creator_ids = {v.creator_id for v in instagram if v.creator_id is not None}
    creators = {c.id: c for c in db.scalars(select(VtCreator).where(VtCreator.id.in_(creator_ids)))}
    for video in instagram:
        parsed = parse_video_link(video.video_url)
        source_owner = parsed.owner_username if parsed.platform == "instagram" and parsed.identifier == video.video_identifier else None
        values = {"stored owner": video.owner_username, "source URL": source_owner}
        creator = creators.get(video.creator_id)
        if creator is not None and creator.platform == "instagram":
            values["linked creator"] = (parse_instagram_handle(creator.username) or parse_instagram_handle(creator.channel_url)
                                        or parse_instagram_handle(creator.normalized_identifier))
        if (video.creator_name or "").startswith("@") or "/" in (video.creator_name or ""):
            values["creator handle"] = video.creator_name
        sources[video.id] = {name: handle for name, value in values.items() if (handle := parse_instagram_handle(value))}
    codes = list(by_code)
    for start in range(0, len(codes), 100):
        rows = db.scalars(select(VtUploadRow).where(
            VtUploadRow.platform == "instagram", VtUploadRow.username.is_not(None),
            VtUploadRow.status.in_(("added", "already_tracked", "duplicate")),
            or_(*(VtUploadRow.video_link.contains(code, autoescape=True) for code in codes[start:start + 100])),
        ).order_by(VtUploadRow.id))
        for row in rows:
            parsed = parse_video_link(row.video_link, instagram_reel_hint=row.platform == "instagram")
            video = by_code.get(parsed.identifier) if parsed.platform == "instagram" else None
            handle = parse_instagram_handle(row.username)
            if video is not None and handle and not (row.message or "").startswith("Owner metadata superseded by explicit edit;"):
                sources[video.id][f"upload {row.upload_id} row {row.row_number}"] = handle
    return sources


def claim_videos(video_ids: list[int]) -> list[dict]:
    """Mark videos Processing (paused ones stay Paused) and return what the fetch step needs."""
    with session_scope() as db:
        videos = db.scalars(select(VtVideo).where(VtVideo.id.in_(video_ids))).all()
        analysed = set(db.scalars(select(VtSentiment.video_id).where(VtSentiment.video_id.in_(video_ids))))
        retrieved = set(db.scalars(select(VtViewSnapshot.video_id).where(VtViewSnapshot.video_id.in_(video_ids)).distinct()))
        metadata = _owner_metadata(db, videos)
        rows = []
        for video in videos:
            sources = metadata.get(video.id, {})
            stored = parse_instagram_handle(video.owner_username)
            verified = bool(stored and (_has_metrics(video) or video.id in retrieved))
            conflict = len(set(sources.values())) > 1
            diagnostic = None
            if conflict:
                diagnostic = "Conflicting Instagram owner metadata: " + ", ".join(f"{source}=@{name}" for source, name in sources.items())
                log_event(logger, logging.WARNING, "vt_owner_metadata_conflict", video_id=video.id, sources=diagnostic)
            owner = stored if verified else (next(iter(sources.values()), None) if not conflict else None)
            # continued by the Meta queue: it is waiting for Meta, so Meta (not a new Apify run) checks it
            meta_queue = video.status == VideoStatus.PENDING and (video.status_reason or "").startswith(META_WAIT)
            # waiting for Meta after Apify already failed for it: never sent to Apify again while Meta is paused
            apify_tried = meta_queue and APIFY_TRIED_NOTE in (video.status_reason or "")
            if video.status != VideoStatus.PAUSED:
                video.status = VideoStatus.PROCESSING
            rows.append(
                {
                    "id": video.id,
                    "platform": video.platform,
                    "identifier": video.video_identifier,
                    "instagram_media_kind": video.instagram_media_kind,
                    "video_url": video.video_url,
                    "owner_username": owner if video.platform == "instagram" else video.owner_username,
                    "owner_verified": verified,
                    "owner_conflict": diagnostic if conflict and not verified else None,
                    "owner_warning": diagnostic if verified else None,
                    "needs_sentiment": video.id not in analysed,
                    "meta_queue": meta_queue,
                    "apify_tried": apify_tried,
                    # metrics were fetched successfully before (a later "not found" means the video went down)
                    "seen": video.current_views is not None or video.likes is not None,
                }
            )
        return rows


def _link_creator(db: Session, video: VtVideo) -> None:
    if video.creator_id is not None:
        return
    condition = None
    if video.platform == "youtube" and video.channel_id:
        condition = VtCreator.platform_channel_id == video.channel_id
    elif video.platform == "instagram" and video.owner_username:
        condition = VtCreator.username == video.owner_username
    if condition is not None:
        creator = db.scalar(select(VtCreator).where(VtCreator.platform == video.platform, condition))
        if creator is not None:
            video.creator_id = creator.id
            video.creator_name = video.creator_name or creator.creator_name


# Platform APIs are served from caches: a check made shortly after another one sometimes returns an older,
# slightly lower view count (seen as e.g. -83 views a few minutes later, back up on the next check). A reading
# lower than one taken within this window is treated as such an outdated count and not stored; a real decrease
# (e.g. YouTube removing spam views) still shows once it persists beyond the window.
STALE_COUNT_WINDOW = timedelta(hours=12)


def is_outdated_count(views: int | None, earlier: list[tuple[datetime, int | None]], at: datetime) -> bool:
    """True when `views` is lower than a reading taken within STALE_COUNT_WINDOW before `at`."""
    if views is None:
        return False
    recent = [n for t, n in earlier if n is not None and at - STALE_COUNT_WINDOW <= as_utc(t) <= at]
    return bool(recent) and views < max(recent)


def repair_outdated_counts() -> int:
    """Videos whose current numbers came from an outdated (lower) API count saved before such counts were
    filtered get the higher real reading back. Saved readings are not changed. Safe to run any time."""
    from collections import defaultdict

    with session_scope() as db:
        rows = db.execute(
            select(VtViewSnapshot.video_id, VtViewSnapshot.captured_at, VtViewSnapshot.views, VtViewSnapshot.likes,
                   VtViewSnapshot.comments, VtViewSnapshot.engagement_rate)
            .where(VtViewSnapshot.views.is_not(None))
            .order_by(VtViewSnapshot.video_id, VtViewSnapshot.captured_at)
        ).all()
        by_video: dict[int, list] = defaultdict(list)
        for row in rows:
            by_video[row.video_id].append(row)
        repaired = 0
        for video_id, snaps in by_video.items():
            latest = snaps[-1]
            at = as_utc(latest.captured_at)
            earlier = [(as_utc(s.captured_at), s.views) for s in snaps[:-1]]
            if not is_outdated_count(latest.views, earlier, at):
                continue
            video = db.get(VtVideo, video_id)
            if video is None or video.current_views != latest.views:
                continue  # already corrected (or changed since)
            best = max((s for s in snaps if at - STALE_COUNT_WINDOW <= as_utc(s.captured_at)), key=lambda s: s.views)
            _apply_counts(video, best.views, best.likes, best.comments, best.engagement_rate,
                          _previous_day_views(db, video_id, at))
            repaired += 1
    if repaired:
        log_event(logger, logging.INFO, "vt_outdated_counts_repaired", videos=repaired)
    return repaired


def repair_temporary_failures() -> list[int]:
    """Startup repair of videos left by older versions after a rate limit:
    * Instagram / Facebook ones (Failed, or Tracking with a "Latest check failed" note) join the Meta queue:
      Pending - waiting for Meta's hourly limit, stored numbers kept;
    * YouTube ones that were Failed by the daily quota while they had good numbers go back to Tracking.
    Returns the videos now waiting in the Meta queue."""
    rate_limited = and_(
        or_(VtVideo.status_reason.like("%rate limit reached%"), VtVideo.status_reason.like("%quota exceeded%")),
        # Apify failures are never moved into Meta's queue (that would start new billable Apify runs on its own).
        ~VtVideo.status_reason.like("%Apify%"),
    )
    with session_scope() as db:
        videos = db.scalars(
            select(VtVideo).where(
                VtVideo.status != VideoStatus.PAUSED,
                or_(
                    (VtVideo.status == VideoStatus.FAILED) & rate_limited,
                    VtVideo.status_reason.like(f"Latest check failed%{RETRY_NOTE}"),
                ),
            )
        ).all()
        repaired = 0
        for video in videos:
            last_ok = db.scalar(select(func.max(VtViewSnapshot.captured_at)).where(VtViewSnapshot.video_id == video.id))
            message = (video.status_reason or "").split(": ", 1)[-1]  # drop the "@owner: " prefix
            message = message.removeprefix("Latest check failed (").split(") - showing", 1)[0]
            error = PlatformError(PlatformError.RATE_LIMITED, message)
            if video.platform in META_PLATFORMS:
                video.status = VideoStatus.PENDING
                video.status_reason = meta_wait_note(error, last_ok if video.current_views is not None else None)
            elif video.current_views is not None and video.status == VideoStatus.FAILED:
                video.status = VideoStatus.TRACKING
                video.status_reason = _temporary_note(error, last_ok)
            else:
                continue
            if video.current_views is not None:
                video.last_checked_at = last_ok or video.last_checked_at
            repaired += 1
    if repaired:
        log_event(logger, logging.INFO, "vt_temporary_failures_repaired", videos=repaired)
    return meta_waiting_ids()


APIFY_TRIED_NOTE = "Apify could not read it either"  # in a Meta-wait note: Apify already failed for this reel


def meta_waiting_reels_for_apify(limit: int) -> list[int]:
    """Reels waiting for Meta's hourly limit that Apify has not tried yet, oldest first (used while Meta is paused)."""
    with read_scope() as db:
        return list(db.scalars(
            select(VtVideo.id)
            .where(
                VtVideo.status == VideoStatus.PENDING, VtVideo.status_reason.like(f"{META_WAIT}%"),
                VtVideo.platform == "instagram", VtVideo.instagram_media_kind == "reel",
                ~VtVideo.status_reason.like(f"%{APIFY_TRIED_NOTE}%"),
            )
            .order_by(VtVideo.created_at, VtVideo.id)
            .limit(limit)
        ))


def meta_waiting_ids(limit: int | None = None) -> list[int]:
    """Videos waiting for Meta's hourly limit, oldest first."""
    with read_scope() as db:
        return list(db.scalars(
            select(VtVideo.id)
            .where(VtVideo.status == VideoStatus.PENDING, VtVideo.status_reason.like(f"{META_WAIT}%"))
            .order_by(VtVideo.created_at, VtVideo.id)
            .limit(limit)
        ))


def _previous_day_views(db: Session, video_id: int, now: datetime, comparable: tuple = ()) -> int | None:
    """Views of the latest check before today (ignoring an outdated lower count taken shortly before it)."""
    day_start = local_day_start_utc(now)
    last = db.execute(
        select(VtViewSnapshot.captured_at, VtViewSnapshot.views)
        .where(VtViewSnapshot.video_id == video_id, VtViewSnapshot.captured_at < day_start, VtViewSnapshot.views.is_not(None), *comparable)
        .order_by(VtViewSnapshot.captured_at.desc())
        .limit(1)
    ).first()
    if last is None:
        return None
    return db.scalar(
        select(func.max(VtViewSnapshot.views)).where(
            VtViewSnapshot.video_id == video_id,
            VtViewSnapshot.captured_at >= as_utc(last.captured_at) - STALE_COUNT_WINDOW,
            VtViewSnapshot.captured_at < day_start,
            *comparable,
        )
    )


def save_metrics(video_id: int, metrics: VideoMetrics) -> bool:
    """Store a new snapshot and the derived day-over-day numbers. Returns False if the video vanished."""
    now = utcnow()
    with session_scope() as db:
        video = db.get(VtVideo, video_id)
        if video is None:
            return False
        if metrics.source_run_id:
            saved = db.scalar(select(VtViewSnapshot).where(
                VtViewSnapshot.video_id == video_id, VtViewSnapshot.source_run_id == metrics.source_run_id,
            ))
            if saved:
                if video.status == VideoStatus.PROCESSING:
                    partial = saved.views is None or (saved.metrics_source == "apify_instagram_reel"
                                                      and (saved.likes is None or saved.comments is None))
                    video.status = VideoStatus.PARTIAL if partial else VideoStatus.TRACKING
                return True  # replay must not duplicate a snapshot or leave the claimed row Processing
        if video.platform == "instagram" and metrics.owner_username:
            owner = parse_instagram_handle(metrics.owner_username)
            if owner is None:
                raise Conflict("Instagram metrics did not contain a valid owner username")
            previous_owner = parse_instagram_handle(video.owner_username)
            if previous_owner and previous_owner != owner:
                verified = _owner_verified(db, video)
                log_event(logger, logging.WARNING, "vt_api_owner_conflict", video_id=video.id,
                          stored_owner=previous_owner, matched_owner=owner, verified=verified)
                if verified:
                    raise Conflict("Instagram API owner conflicts with the verified stored owner; stored metrics retained")
                video.creator_id = None
            metrics.owner_username = owner
        comparable = ()
        if metrics.metrics_source == "apify_instagram_reel":
            comparable = (VtViewSnapshot.metrics_source == metrics.metrics_source,
                          VtViewSnapshot.view_metric == metrics.view_metric)
        elif video.metrics_source == "apify_instagram_reel":
            comparable = (VtViewSnapshot.metrics_source.is_(None),)
        previous = _previous_day_views(db, video_id, now, comparable)
        recent = db.execute(
            select(VtViewSnapshot.captured_at, VtViewSnapshot.views, VtViewSnapshot.likes, VtViewSnapshot.comments,
                   VtViewSnapshot.engagement_rate)
            .where(VtViewSnapshot.video_id == video_id, VtViewSnapshot.captured_at >= now - STALE_COUNT_WINDOW,
                   VtViewSnapshot.views.is_not(None), *comparable)
            .order_by(VtViewSnapshot.views.desc(), VtViewSnapshot.captured_at.desc())
        ).all()
        if is_outdated_count(metrics.views, [(r.captured_at, r.views) for r in recent], now):
            # Keep the higher reading from earlier (a real API value), not the outdated one.
            best = recent[0]
            _apply_counts(video, best.views, best.likes, best.comments, best.engagement_rate, previous)
            video.last_checked_at = now
            if video.status not in (VideoStatus.PAUSED, VideoStatus.TRACKING, VideoStatus.PARTIAL):
                video.status, video.status_reason = VideoStatus.TRACKING, None
            log_event(logger, logging.INFO, "vt_outdated_count_ignored", video_id=video_id,
                      returned=metrics.views, kept=best.views)
            return True

        rate = engagement(metrics.views, metrics.likes, metrics.comments)
        db.add(
            VtViewSnapshot(
                video_id=video_id, captured_at=now, views=metrics.views, likes=metrics.likes,
                comments=metrics.comments, engagement_rate=rate,
                metrics_source=metrics.metrics_source, view_metric=metrics.view_metric,
                source_metrics=metrics.source_metrics, source_run_id=metrics.source_run_id,
            )
        )
        video.title = metrics.title or video.title
        video.caption = metrics.caption if metrics.caption is not None else video.caption
        video.published_at = metrics.published_at or video.published_at
        video.channel_id = metrics.channel_id or video.channel_id
        video.owner_username = metrics.owner_username or video.owner_username
        if not video.creator_name:
            video.creator_name = metrics.channel_title or (f"@{metrics.owner_username}" if metrics.owner_username else None)
        _link_creator(db, video)

        # A value the API did not return this time never replaces a stored one (the snapshot above keeps
        # exactly what was returned).
        def kept(new, old):
            return new if new is not None else old

        _apply_counts(video, kept(metrics.views, video.current_views), kept(metrics.likes, video.likes),
                      kept(metrics.comments, video.comments),
                      rate if metrics.metrics_source == "apify_instagram_reel" else kept(rate, video.engagement_rate), previous)
        video.last_checked_at = now

        # A retained older count keeps its provider/metric label when the new count is missing.
        if metrics.views is not None:
            video.metrics_source = metrics.metrics_source
            video.view_metric = metrics.view_metric
        if metrics.metrics_source:
            video.source_metrics = metrics.source_metrics
        elif metrics.views is not None:
            video.source_metrics = None

        notes = list(metrics.notes)
        if metrics.views is None:
            notes.insert(0, "View count is not available from the API for this video")
        if video.status != VideoStatus.PAUSED:
            video.status = VideoStatus.PARTIAL if (metrics.views is None or (
                metrics.metrics_source == "apify_instagram_reel" and (metrics.likes is None or metrics.comments is None)
            )) else VideoStatus.TRACKING
        video.status_reason = "; ".join(notes) or None
        return True


def _apply_counts(video: VtVideo, views: int | None, likes: int | None, comments: int | None,
                  rate: float | None, previous: int | None) -> None:
    video.current_views = views
    video.previous_views = previous
    video.views_gained = views - previous if views is not None and previous is not None else None
    video.growth_pct = round(video.views_gained / previous * 100, 2) if video.views_gained is not None and previous else None
    video.likes, video.comments = likes, comments
    video.engagement_rate = rate
    video.engagement_basis = "views" if rate is not None else None


TEMPORARY_ERRORS = (PlatformError.RATE_LIMITED, PlatformError.API)
RETRY_NOTE = "retried automatically"


def _temporary_note(error: PlatformError, last_ok: datetime | None) -> str:
    when = as_utc(last_ok).astimezone(tracking_tz()).strftime("%d %b, %I:%M %p") if last_ok else "the previous check"
    return f"Latest check failed ({error.message}) - showing the numbers from {when}; {RETRY_NOTE}"


META_PLATFORMS = ("instagram", "facebook")


def meta_wait_note(error: PlatformError, last_ok: datetime | None = None) -> str:
    """'Pending - waiting for Meta's hourly limit (...; continues after 13:05)' [+ which stored numbers are shown]."""
    note = error.message.split(": ", 1)[-1] if META_WAIT in error.message else ""
    if not note.startswith(META_WAIT):
        resume = meta_budget.resume_at()
        when = f"; continues after {as_utc(resume).astimezone(tracking_tz()):%H:%M}" if resume else ""
        note = f"{META_WAIT} ({error.message.split(': ', 1)[-1]}{when})"
    if last_ok:
        note += f" - showing the numbers from {as_utc(last_ok).astimezone(tracking_tz()):%d %b, %I:%M %p}"
    return note


def save_error(video_id: int, error: PlatformError) -> None:
    with session_scope() as db:
        video = db.get(VtVideo, video_id)
        if video is None:
            return
        if error.code == "apify_error":
            # No Meta queue, metric erasure, or advancement of Last Checked after an Apify failure.
            if video.status not in (VideoStatus.PAUSED, VideoStatus.COMPLETED):
                video.status = VideoStatus.PARTIAL if _has_metrics(video) else VideoStatus.FAILED
            video.status_reason = error.message
            return
        if error.code == PlatformError.RATE_LIMITED and video.platform in META_PLATFORMS:
            # Meta's hourly limit (or our budget for it): queued, not failed. Stored numbers and "last checked"
            # stay; the Meta queue continues these videos automatically, oldest first.
            if video.status != VideoStatus.PAUSED:
                video.status = VideoStatus.PENDING
            video.status_reason = meta_wait_note(error, video.last_checked_at if video.current_views is not None else None)
            return
        if error.code in TEMPORARY_ERRORS and video.current_views is not None:
            # A temporary problem (e.g. Meta's hourly rate limit) does not make good numbers wrong: the video
            # stays tracked, "last checked" keeps the time of the last successful check, and a note says why.
            if video.status not in (VideoStatus.PAUSED, VideoStatus.PARTIAL):
                video.status = VideoStatus.TRACKING
            video.status_reason = _temporary_note(error, video.last_checked_at)
            return
        if video.status != VideoStatus.PAUSED:
            if error.code == PlatformError.UNSUPPORTED:
                video.status = VideoStatus.UNSUPPORTED
            elif error.code == PlatformError.NOT_FOUND:
                video.status = VideoStatus.VIDEO_DOWN
            else:
                video.status = VideoStatus.FAILED
        video.status_reason = error.message
        video.last_checked_at = utcnow()


def save_sentiment(video_id: int, result: SentimentResult, model: str) -> None:
    with session_scope() as db:
        video = db.get(VtVideo, video_id)
        if video is None:
            return
        db.add(
            VtSentiment(
                video_id=video_id, sentiment=result.sentiment, confidence=result.confidence,
                model=model if result.error is None or result.confidence is not None else None,
                input_chars=result.input_chars,
            )
        )
        video.sentiment = result.sentiment
        video.sentiment_confidence = result.confidence


def video_contents(video_ids: list[int]) -> dict[int, tuple[str | None, str | None]]:
    """Title and caption of several videos in one query (keeps the given order)."""
    with read_scope() as db:
        rows = {r.id: (r.title, r.caption) for r in db.execute(
            select(VtVideo.id, VtVideo.title, VtVideo.caption).where(VtVideo.id.in_(video_ids))
        )}
    return {vid: rows[vid] for vid in video_ids if vid in rows}


DOWN_STOPPED_NOTE = "no longer checked daily - use Retry to check again"


def videos_due_for_refresh(max_days: int, down_recheck_days: int = 0) -> list[int]:
    with session_scope() as db:
        if max_days > 0:
            cutoff = utcnow() - timedelta(days=max_days)
            db.execute(
                update(VtVideo)
                .where(VtVideo.status.in_(VideoStatus.DAILY), VtVideo.tracking_started_at < cutoff)
                .values(status=VideoStatus.COMPLETED, status_reason=f"Tracking window of {max_days} days finished")
            )
        ids = list(db.scalars(select(VtVideo.id).where(VtVideo.status.in_(VideoStatus.DAILY)).order_by(VtVideo.id)))
        if down_recheck_days > 0:
            # Videos that have been unavailable for a long time are no longer re-checked every day.
            cutoff = utcnow() - timedelta(days=down_recheck_days)
            last_seen = (
                select(VtViewSnapshot.video_id, func.max(VtViewSnapshot.captured_at).label("seen"))
                .group_by(VtViewSnapshot.video_id)
                .subquery()
            )
            stale = set(db.scalars(
                select(VtVideo.id)
                .outerjoin(last_seen, last_seen.c.video_id == VtVideo.id)
                .where(VtVideo.status == VideoStatus.VIDEO_DOWN,
                       func.coalesce(last_seen.c.seen, VtVideo.tracking_started_at) < cutoff)
            ))
            if stale:
                for video in db.scalars(select(VtVideo).where(VtVideo.id.in_(stale))):
                    if DOWN_STOPPED_NOTE not in (video.status_reason or ""):
                        reason = (video.status_reason or "Video unavailable").rstrip(".")
                        video.status_reason = f"{reason} - unavailable for {down_recheck_days}+ days, {DOWN_STOPPED_NOTE}"
                ids = [i for i in ids if i not in stale]
        return ids


def status_counts(video_ids: list[int]) -> dict[str, int]:
    """Current status of the given videos, counted (for job summaries)."""
    counts: dict[str, int] = {}
    with read_scope() as db:
        for start in range(0, len(video_ids), 500):
            chunk = video_ids[start : start + 500]
            for status, n in db.execute(
                select(VtVideo.status, func.count()).where(VtVideo.id.in_(chunk)).group_by(VtVideo.status)
            ):
                counts[status] = counts.get(status, 0) + n
    return counts


def recover_after_restart() -> list[int]:
    """Interrupted work: jobs marked failed, in-flight videos re-queued. Returns video IDs to process."""
    with session_scope() as db:
        db.execute(
            update(VtJobRun)
            .where(VtJobRun.status == "running")
            .values(status="failed", finished_at=utcnow(), message="Interrupted by a server restart")
        )
        # (videos waiting for Meta's hourly limit are continued by the Meta queue, within the budget)
        ids = list(db.scalars(select(VtVideo.id).where(
            VtVideo.status.in_(VideoStatus.ACTIVE_WORK),
            or_(VtVideo.status_reason.is_(None), ~VtVideo.status_reason.like(f"{META_WAIT}%")),
        )))
        return ids


# ---------------------------------------------------------------------- jobs
def start_job(job_type: str, trigger: str) -> int:
    with session_scope() as db:
        run = VtJobRun(job_type=job_type, trigger=trigger)
        db.add(run)
        db.flush()
        return run.id


def finish_job(run_id: int, processed: int, succeeded: int, failed: int, message: str | None, ok: bool = True) -> None:
    with session_scope() as db:
        run = db.get(VtJobRun, run_id)
        if run is not None:
            run.status = "completed" if ok else "failed"
            run.finished_at = utcnow()
            run.processed, run.succeeded, run.failed, run.message = processed, succeeded, failed, message


def acquire_job_lock(job_type: str, owner: str, hours: int = 6) -> bool:
    """Atomically claim a job (works across processes). Expired locks (crashed runs) can be taken over."""
    from app.video_performance.models import VtJobLock

    now = utcnow()
    with session_scope() as db:
        if db.get(VtJobLock, job_type) is None:
            try:
                with db.begin_nested():
                    db.add(VtJobLock(job_type=job_type))
            except IntegrityError:
                pass  # another process inserted it first
        claimed = db.execute(
            update(VtJobLock)
            .where(VtJobLock.job_type == job_type, or_(VtJobLock.locked_until.is_(None), VtJobLock.locked_until < now))
            .values(owner=owner, locked_until=now + timedelta(hours=hours))
        ).rowcount
        return claimed == 1


def release_job_lock(job_type: str, owner: str) -> None:
    from app.video_performance.models import VtJobLock

    with session_scope() as db:
        db.execute(
            update(VtJobLock)
            .where(VtJobLock.job_type == job_type, VtJobLock.owner == owner)
            .values(owner=None, locked_until=None)
        )


def last_completed_run(job_type: str) -> datetime | None:
    with read_scope() as db:
        return as_utc(
            db.scalar(
                select(func.max(VtJobRun.started_at)).where(VtJobRun.job_type == job_type, VtJobRun.status == "completed")
            )
        )


def last_started_run(job_type: str) -> datetime | None:
    """Start of the most recent run of this job, whatever its outcome."""
    with read_scope() as db:
        return as_utc(db.scalar(select(func.max(VtJobRun.started_at)).where(VtJobRun.job_type == job_type)))


def latest_runs(limit_per_type: int = 1) -> list[VtJobRun]:
    with read_scope() as db:
        runs = []
        for job_type in ("creator_discovery", "metrics_refresh"):
            runs.extend(
                db.scalars(
                    select(VtJobRun).where(VtJobRun.job_type == job_type).order_by(VtJobRun.started_at.desc()).limit(limit_per_type)
                ).all()
            )
        return runs


# ------------------------------------------------------------------- creators
def _create_creator(name: str, parsed: ParsedLink) -> int:
    with session_scope() as db:
        existing = db.scalar(
            select(VtCreator).where(
                VtCreator.platform == parsed.platform, VtCreator.normalized_identifier == parsed.normalized_identifier
            )
        )
        if existing is not None:
            raise Conflict(f"This creator is already tracked as '{existing.creator_name}'", existing.id)
        creator = VtCreator(
            creator_name=name,
            platform=parsed.platform,
            channel_url=parsed.canonical_url or "",
            normalized_identifier=parsed.normalized_identifier or "",
            identifier_type=parsed.identifier_type,
            username=parsed.identifier if parsed.platform == "instagram" else None,
            status=CreatorTrackingStatus.PENDING,
        )
        db.add(creator)
        db.flush()
        return creator.id


def creator_row(creator_id: int) -> dict | None:
    with read_scope() as db:
        c = db.get(VtCreator, creator_id)
        if c is None:
            return None
        return {
            "id": c.id, "name": c.creator_name, "platform": c.platform, "channel_url": c.channel_url,
            "username": c.username, "platform_channel_id": c.platform_channel_id,
            "uploads_playlist_id": c.uploads_playlist_id, "enabled": c.enabled,
            "tracking_since": as_utc(c.tracking_since), "status": c.status,
        }


def save_creator_channel(creator_id: int, info: ChannelInfo) -> None:
    with session_scope() as db:
        creator = db.get(VtCreator, creator_id)
        if creator is None:
            return
        if info.platform_channel_id:
            duplicate = db.scalar(
                select(VtCreator.id).where(
                    VtCreator.platform == creator.platform,
                    VtCreator.platform_channel_id == info.platform_channel_id,
                    VtCreator.id != creator_id,
                )
            )
            if duplicate:
                creator.status = CreatorTrackingStatus.FAILED
                creator.status_reason = "The same channel is already tracked (added with a different link)"
                creator.enabled = False
                return
        creator.platform_channel_id = info.platform_channel_id or creator.platform_channel_id
        creator.platform_name = info.platform_name or creator.platform_name
        creator.uploads_playlist_id = info.uploads_playlist_id or creator.uploads_playlist_id
        creator.username = info.username or creator.username
        # Link already-tracked videos of this channel
        condition = (
            VtVideo.channel_id == creator.platform_channel_id if creator.platform == "youtube"
            else VtVideo.owner_username == creator.username
        )
        db.execute(
            update(VtVideo)
            .where(VtVideo.platform == creator.platform, VtVideo.creator_id.is_(None), condition)
            .values(creator_id=creator.id)
        )


def save_creator_result(creator_id: int, *, error: PlatformError | None = None, discovered: int = 0) -> None:
    with session_scope() as db:
        creator = db.get(VtCreator, creator_id)
        if creator is None:
            return
        # ``save_creator_channel`` can reject this record after resolving its
        # real platform identity (for example, the same YouTube channel added
        # once by handle and once by channel ID).  Preserve that terminal
        # failure instead of turning it into a misleading paused success.
        if error is None and not creator.enabled and creator.status == CreatorTrackingStatus.FAILED:
            return
        creator.last_discovery_at = utcnow()
        if error is None:
            creator.status = CreatorTrackingStatus.ACTIVE if creator.enabled else CreatorTrackingStatus.PAUSED
            creator.status_reason = None
            creator.videos_discovered += discovered
        else:
            creator.status = (
                CreatorTrackingStatus.UNSUPPORTED if error.code == PlatformError.UNSUPPORTED else CreatorTrackingStatus.FAILED
            )
            creator.status_reason = error.message


def add_discovered_videos(creator_id: int, videos: list[DiscoveredVideo], mark_discovered: bool) -> list[int]:
    """Insert videos not tracked yet. Returns the new video IDs."""
    if not videos:
        return []
    with session_scope() as db:
        creator = db.get(VtCreator, creator_id)
        if creator is None:
            return []
        identifiers = [v.identifier for v in videos]
        known = set(
            db.scalars(
                select(VtVideo.video_identifier).where(
                    VtVideo.platform == creator.platform, VtVideo.video_identifier.in_(identifiers)
                )
            )
        )
        now = utcnow()
        new_rows = []
        for video in videos:
            if video.identifier in known:
                continue
            known.add(video.identifier)
            new_rows.append(
                VtVideo(
                    platform=creator.platform,
                    video_identifier=video.identifier,
                    video_url=video.url,
                    creator_name=creator.creator_name,
                    owner_username=creator.username if creator.platform == "instagram" else None,
                    channel_id=creator.platform_channel_id if creator.platform == "youtube" else None,
                    creator_id=creator.id,
                    source="discovered" if mark_discovered else "manual",
                    published_at=video.published_at,
                    discovered_at=now if mark_discovered else None,
                    status=VideoStatus.PENDING,
                )
            )
        db.add_all(new_rows)
        db.flush()
        return [row.id for row in new_rows]


def enabled_creator_ids() -> list[int]:
    with read_scope() as db:
        return list(
            db.scalars(
                select(VtCreator.id).where(
                    VtCreator.enabled.is_(True), VtCreator.status != CreatorTrackingStatus.UNSUPPORTED
                )
            )
        )


# --------------------------------------------------------------------- videos
def _add_video(
    parsed: ParsedVideo, creator_name: str | None, owner_username: str | None, source: str, upload_id: str | None = None
) -> int:
    with session_scope() as db:
        existing = db.scalar(
            select(VtVideo).where(VtVideo.platform == parsed.platform, VtVideo.video_identifier == parsed.identifier)
        )
        if existing is not None:
            raise Conflict("This video is already being tracked", existing.id)
        owner = _validated_owner(owner_username, parsed)
        video = VtVideo(
            platform=parsed.platform,
            video_identifier=parsed.identifier,
            video_url=parsed.url,
            instagram_media_kind=parsed.instagram_media_kind,
            creator_name=creator_name or None,
            owner_username=owner,
            source=source,
            upload_id=upload_id,
            status=VideoStatus.PENDING,
        )
        db.add(video)
        db.flush()
        _link_creator(db, video)
        return video.id


def update_video(
    video_id: int,
    *,
    creator_name: str | None,
    parsed: ParsedVideo | None,
    owner_username: str | None,
    tracking: str | None,
) -> bool:
    """Apply edits. Returns True if the video must be (re)processed."""
    with session_scope() as db:
        video = db.get(VtVideo, video_id)
        if video is None:
            raise LookupError("Video not found")
        reprocess = False
        if parsed is not None and parsed.platform == "instagram" and video.instagram_media_kind != parsed.instagram_media_kind:
            video.instagram_media_kind = parsed.instagram_media_kind
            if video.status not in (VideoStatus.PAUSED, VideoStatus.COMPLETED):
                reprocess = True
        if creator_name is not None:
            video.creator_name = creator_name or None
        if parsed is not None and (parsed.platform, parsed.identifier) != (video.platform, video.video_identifier):
            clash = db.scalar(
                select(VtVideo.id).where(
                    VtVideo.platform == parsed.platform, VtVideo.video_identifier == parsed.identifier, VtVideo.id != video_id
                )
            )
            if clash:
                raise Conflict("That video is already being tracked", clash)
            # A different video: its history does not belong to the new one.
            db.execute(delete(VtViewSnapshot).where(VtViewSnapshot.video_id == video_id))
            db.execute(delete(VtSentiment).where(VtSentiment.video_id == video_id))
            video.platform, video.video_identifier, video.video_url = parsed.platform, parsed.identifier, parsed.url
            video.owner_username = parsed.owner_username if parsed.platform == "instagram" else None
            video.instagram_media_kind = parsed.instagram_media_kind
            video.metrics_source = video.view_metric = video.source_metrics = None
            video.channel_id = video.creator_id = None
            for field_name in (
                "title", "caption", "published_at", "current_views", "previous_views", "views_gained", "growth_pct",
                "likes", "comments", "engagement_rate", "engagement_basis", "sentiment", "sentiment_confidence",
                "last_checked_at", "status_reason",
            ):
                setattr(video, field_name, None)
            video.tracking_started_at = utcnow()
            reprocess = True
        incoming = _validated_owner(owner_username, parsed) if parsed else parse_instagram_handle(owner_username)
        if owner_username and not incoming:
            raise Conflict("Instagram username is not valid")
        if incoming and video.platform == "instagram" and incoming != video.owner_username:
            if parse_instagram_handle(video.owner_username) and _owner_verified(db, video):
                log_event(logger, logging.WARNING, "vt_verified_owner_edit_conflict", video_id=video.id,
                          stored_owner=video.owner_username, incoming_owner=incoming)
                raise Conflict("This Reel already has a verified owner; conflicting metadata was not applied")
            old_owner = parse_instagram_handle(video.owner_username)
            video.owner_username = incoming
            video.creator_id = None
            # Keep original upload values for audit, but mark metadata explicitly corrected by the user.
            # Otherwise an immutable old upload could prevent every subsequent retry of the corrected Reel.
            from app.video_performance.models import VtUploadRow
            upload_rows = db.scalars(select(VtUploadRow).where(
                VtUploadRow.platform == "instagram", VtUploadRow.username.is_not(None),
                VtUploadRow.video_link.contains(video.video_identifier, autoescape=True),
            ))
            for source_row in upload_rows:
                source = parse_video_link(source_row.video_link, instagram_reel_hint=source_row.platform == "instagram")
                if (source.platform == "instagram" and source.identifier == video.video_identifier
                        and parse_instagram_handle(source_row.username) != incoming):
                    source_row.message = "Owner metadata superseded by explicit edit; " + (source_row.message or "original value retained")
            if old_owner and parse_instagram_handle(video.creator_name) == old_owner:
                video.creator_name = f"@{incoming}"
            if parsed is not None:
                video.video_url = parsed.url
            _link_creator(db, video)
            reprocess = True
        if tracking == "paused":
            video.status = VideoStatus.PAUSED
            video.status_reason = "Tracking stopped by user"
            reprocess = False
        elif tracking == "tracking" and video.status in (VideoStatus.PAUSED, VideoStatus.COMPLETED):
            reprocess = True
        if reprocess:
            video.status = VideoStatus.PENDING
        return reprocess


def set_paused(video_ids: list[int], paused: bool) -> list[int]:
    with session_scope() as db:
        if paused:
            rows = list(db.scalars(select(VtVideo.id).where(VtVideo.id.in_(video_ids))))
            if rows:
                db.execute(
                    update(VtVideo).where(VtVideo.id.in_(rows))
                    .values(status=VideoStatus.PAUSED, status_reason="Tracking stopped by user")
                )
            return rows
        rows = list(
            db.scalars(
                select(VtVideo.id).where(
                    VtVideo.id.in_(video_ids), VtVideo.status.in_((VideoStatus.PAUSED, VideoStatus.COMPLETED))
                )
            )
        )
        if rows:
            db.execute(
                update(VtVideo).where(VtVideo.id.in_(rows))
                .values(status=VideoStatus.PENDING, status_reason=None, tracking_started_at=utcnow())
            )
        return rows


def queue_for_processing(video_ids: list[int], statuses: tuple[str, ...] | None = None) -> list[int]:
    with session_scope() as db:
        stmt = select(VtVideo.id).where(VtVideo.id.in_(video_ids), VtVideo.status.not_in(VideoStatus.ACTIVE_WORK))
        if statuses:
            stmt = stmt.where(VtVideo.status.in_(statuses))
        ids = list(db.scalars(stmt))
        if ids:
            db.execute(
                update(VtVideo)
                .where(VtVideo.id.in_(ids), VtVideo.status != VideoStatus.PAUSED)
                .values(status=VideoStatus.PENDING)
            )
        return ids


def failed_video_ids() -> list[int]:
    with read_scope() as db:
        return list(db.scalars(select(VtVideo.id).where(VtVideo.status == VideoStatus.FAILED)))


def delete_videos(video_ids: list[int]) -> int:
    with session_scope() as db:
        db.execute(delete(VtViewSnapshot).where(VtViewSnapshot.video_id.in_(video_ids)))
        db.execute(delete(VtSentiment).where(VtSentiment.video_id.in_(video_ids)))
        return db.execute(delete(VtVideo).where(VtVideo.id.in_(video_ids))).rowcount or 0


def update_creator(creator_id: int, name: str | None, enabled: bool | None) -> bool:
    """Returns True when tracking was (re-)enabled and discovery should run."""
    with session_scope() as db:
        creator = db.get(VtCreator, creator_id)
        if creator is None:
            raise LookupError("Creator not found")
        if name is not None:
            creator.creator_name = name
            db.execute(update(VtVideo).where(VtVideo.creator_id == creator_id).values(creator_name=name))
        enable_now = False
        if enabled is not None and enabled != creator.enabled:
            creator.enabled = enabled
            if enabled:
                creator.tracking_since = utcnow()
                creator.status = CreatorTrackingStatus.PENDING
                enable_now = True
            else:
                creator.status = CreatorTrackingStatus.PAUSED
        return enable_now


def delete_creator(creator_id: int, delete_videos_too: bool) -> int:
    with session_scope() as db:
        if db.get(VtCreator, creator_id) is None:
            raise LookupError("Creator not found")
        removed = 0
        if delete_videos_too:
            ids = list(db.scalars(select(VtVideo.id).where(VtVideo.creator_id == creator_id)))
            if ids:
                db.execute(delete(VtViewSnapshot).where(VtViewSnapshot.video_id.in_(ids)))
                db.execute(delete(VtSentiment).where(VtSentiment.video_id.in_(ids)))
                removed = db.execute(delete(VtVideo).where(VtVideo.id.in_(ids))).rowcount or 0
        else:
            db.execute(update(VtVideo).where(VtVideo.creator_id == creator_id).values(creator_id=None))
        db.execute(delete(VtCreator).where(VtCreator.id == creator_id))
        return removed


# --------------------------------------------------------------------- import
def _validated_owner(value: str | None, parsed: ParsedVideo | None) -> str | None:
    if parsed is not None and parsed.platform != "instagram":
        return None
    explicit = parse_instagram_handle(value)
    source = parse_instagram_handle(parsed.owner_username) if parsed else None
    if value and not explicit:
        raise Conflict("Instagram username is not valid")
    if explicit and source and explicit != source:
        raise Conflict("Conflicting Instagram owner metadata in the username and video URL")
    return source or explicit


def _enrich_import_owner(db: Session, video: VtVideo, row) -> bool:
    """Fill missing metadata without replacing an existing owner or changing upload ownership."""
    if video.platform != "instagram" or not row.owner_username:
        return False
    incoming = _validated_owner(row.owner_username, row.parsed)
    stored = parse_instagram_handle(video.owner_username)
    if stored:
        if stored != incoming:
            note = f"Owner conflict: stored @{stored}, supplied @{incoming}; stored owner retained"
            row.message = f"{row.message}; {note}" if row.message else note
            log_event(logger, logging.WARNING, "vt_import_owner_conflict", video_id=video.id,
                      stored_owner=stored, incoming_owner=incoming)
        return False
    video.owner_username = incoming
    _link_creator(db, video)
    row.message = "Already being tracked; missing Instagram owner supplied"
    if video.status not in (*VideoStatus.ACTIVE_WORK, VideoStatus.PAUSED, VideoStatus.COMPLETED):
        video.status, video.status_reason = VideoStatus.PENDING, None
        row.message += "; metrics retrieval queued"
        return True
    return False


# ------------------------------------------------- reel owner search memory
# Accounts whose available media lists were exhausted without finding a reel. Truncated scans cannot rule
# out an owner. v2 ignores legacy entries, which could incorrectly cache partial scans as exhaustive.
# Kept in api_rate_status (one JSON row) - no schema change, survives restarts.
OWNER_SEARCH_SERVICE = "vt_owner_search_v2"


def owner_search_history(shortcodes: list[str]) -> dict[str, set[str]]:
    """{shortcode: accounts already searched without finding it}."""
    from app.models.usage import ApiRateStatus

    with read_scope() as db:
        row = db.get(ApiRateStatus, OWNER_SEARCH_SERVICE)
        saved = (row.details or {}) if row else {}
    return {code: set(saved.get(code) or []) for code in shortcodes}


def record_owner_search(searched: dict[str, set[str]]) -> None:
    """Remember accounts whose media lists were exhausted without finding these reels."""
    from app.models.usage import ApiRateStatus

    if not searched:
        return
    with session_scope() as db:
        row = db.get(ApiRateStatus, OWNER_SEARCH_SERVICE) or ApiRateStatus(service=OWNER_SEARCH_SERVICE)
        details = dict(row.details or {})
        for code, accounts in searched.items():
            details[code] = sorted(set(details.get(code) or []) | accounts)
        row.details, row.observed_at = details, utcnow()  # reassigned so the JSON change is saved
        db.add(row)


def forget_owner_search(shortcodes: list[str]) -> None:
    """Found (or owner supplied): the reel no longer needs its search memory."""
    from app.models.usage import ApiRateStatus

    with session_scope() as db:
        row = db.get(ApiRateStatus, OWNER_SEARCH_SERVICE)
        if row is not None and row.details and any(code in row.details for code in shortcodes):
            row.details = {k: v for k, v in row.details.items() if k not in shortcodes}


def _import_rows(filename: str, stored: str | None, rows: list) -> tuple[str, list[int]]:
    """Record an upload, add videos, and recover ownerless records. Return IDs needing processing."""
    from app.video_performance.models import VtUpload, VtUploadRow

    candidates = [r for r in rows if r.status == "pending"]
    with session_scope() as db:
        tracked = {}
        for start in range(0, len(candidates), 100):
            conditions = [and_(VtVideo.platform == r.parsed.platform, VtVideo.video_identifier == r.parsed.identifier)
                          for r in candidates[start:start + 100]]
            tracked.update({(v.platform, v.video_identifier): v for v in db.scalars(select(VtVideo).where(or_(*conditions)))})
        upload = VtUpload(filename=filename, stored_filename=stored, total_rows=len(rows))
        db.add(upload)
        db.flush()
        new_videos = []
        recovered = []
        for row in candidates:
            existing = tracked.get((row.parsed.platform, row.parsed.identifier))
            if existing is not None:
                row.status = "already_tracked"
                row.message = row.message or "Already being tracked"
                if _enrich_import_owner(db, existing, row):
                    recovered.append(existing.id)
                if existing.instagram_media_kind is None and row.parsed.instagram_media_kind:
                    existing.instagram_media_kind = row.parsed.instagram_media_kind
                    from app.config.settings import get_settings
                    if (get_settings().apify_reels_configured and row.parsed.instagram_media_kind == "reel"
                            and existing.status not in (*VideoStatus.ACTIVE_WORK, VideoStatus.PAUSED, VideoStatus.COMPLETED)):
                        existing.status, existing.status_reason = VideoStatus.PENDING, None
                        recovered.append(existing.id)
                continue
            row.status = "added"
            new_videos.append(
                VtVideo(
                    platform=row.parsed.platform,
                    video_identifier=row.parsed.identifier,
                    video_url=row.parsed.url,
                    instagram_media_kind=row.parsed.instagram_media_kind,
                    creator_name=row.creator_name,
                    owner_username=_validated_owner(row.owner_username, row.parsed),
                    source="upload",
                    upload_id=upload.id,
                    status=VideoStatus.PENDING,
                )
            )
        # Link Instagram rows to tracked creators with one lookup (YouTube rows link after the first fetch).
        owners = {v.owner_username for v in new_videos if v.owner_username}
        if owners:
            creators = db.execute(
                select(VtCreator.id, VtCreator.username, VtCreator.creator_name).where(
                    VtCreator.platform == "instagram", VtCreator.username.in_(owners)
                )
            ).all()
            by_username = {c.username: c for c in creators}
            for video in new_videos:
                creator = by_username.get(video.owner_username)
                if creator is not None:
                    video.creator_id = creator.id
                    video.creator_name = video.creator_name or creator.creator_name
        db.add_all(new_videos)
        db.flush()
        for row in rows:
            if row.status == "duplicate":
                existing = tracked.get((row.parsed.platform, row.parsed.identifier))
                if existing is not None and _enrich_import_owner(db, existing, row):
                    recovered.append(existing.id)
        db.add_all(
            VtUploadRow(
                upload_id=upload.id,
                row_number=r.row,
                creator_name=r.creator_name,
                platform=r.parsed.platform or r.raw_platform,
                video_link=r.raw_link,
                username=r.owner_username,
                status=r.status,
                message=r.message,
            )
            for r in rows
        )
        upload.added = len(new_videos)
        upload.already_tracked = sum(1 for r in rows if r.status == "already_tracked")
        upload.duplicates = sum(1 for r in rows if r.status == "duplicate")
        upload.invalid = sum(1 for r in rows if r.status == "invalid")
        return upload.id, list(dict.fromkeys([v.id for v in new_videos] + recovered))


# ------------------------------------------------- concurrency-safe wrappers
# Two people adding the same video/creator at the same moment hit the unique constraints;
# report that as a normal conflict instead of a server error.
def create_creator(name: str, parsed: ParsedLink) -> int:
    try:
        return _create_creator(name, parsed)
    except IntegrityError as exc:
        raise Conflict("This creator is already tracked") from exc


def add_video(
    parsed: ParsedVideo, creator_name: str | None, owner_username: str | None, source: str, upload_id: str | None = None
) -> int:
    try:
        return _add_video(parsed, creator_name, owner_username, source, upload_id)
    except IntegrityError as exc:
        raise Conflict("This video is already being tracked") from exc


def import_rows(filename: str, stored: str | None, rows: list) -> tuple[str, list[int]]:
    for attempt in (1, 2):
        try:
            return _import_rows(filename, stored, rows)
        except IntegrityError:
            if attempt == 2:
                raise
            for row in rows:  # retry: rows the concurrent import added now count as already tracked
                if row.status in ("added", "already_tracked"):
                    row.status = "pending"
    raise AssertionError("unreachable")


# ---------------------------------------------------------------- uploads (edit / delete)
def upload_file(upload_id: str) -> tuple[str, str | None, bool] | None:
    """(display filename, stored filename, file already removed) of an upload, or None if it does not exist."""
    from app.video_performance.models import VtUpload

    with read_scope() as db:
        upload = db.get(VtUpload, upload_id)
        if upload is None:
            return None
        return upload.filename, upload.stored_filename, upload.file_deleted_at is not None


def rename_upload(upload_id: str, filename: str) -> bool:
    from app.video_performance.models import VtUpload

    with session_scope() as db:
        upload = db.get(VtUpload, upload_id)
        if upload is None:
            return False
        upload.filename = filename
        return True


def delete_upload(upload_id: str, delete_videos: bool) -> tuple[str | None, int] | None:
    """Delete an upload record and its saved rows. Returns (stored filename, videos deleted), None if unknown.

    Videos this file added keep being tracked unless delete_videos is set; then they are deleted together
    with their history. Videos that were already tracked before this file are never touched.
    """
    from app.video_performance.models import VtUpload, VtUploadRow

    with session_scope() as db:
        upload = db.get(VtUpload, upload_id)
        if upload is None:
            return None
        stored = upload.stored_filename
        added = list(db.scalars(select(VtVideo.id).where(VtVideo.upload_id == upload_id)))
        removed = 0
        if added and delete_videos:
            db.execute(delete(VtViewSnapshot).where(VtViewSnapshot.video_id.in_(added)))
            db.execute(delete(VtSentiment).where(VtSentiment.video_id.in_(added)))
            removed = db.execute(delete(VtVideo).where(VtVideo.id.in_(added))).rowcount or 0
        elif added:
            db.execute(update(VtVideo).where(VtVideo.id.in_(added)).values(upload_id=None))
        db.execute(delete(VtUploadRow).where(VtUploadRow.upload_id == upload_id))
        db.execute(delete(VtUpload).where(VtUpload.id == upload_id))
    return stored, removed


# ---------------------------------------------------------------- housekeeping
def cleanup_upload_files(upload_dir, retention_days: int) -> int:
    """Delete uploaded video lists older than the retention period; their rows stay in
    video_tracking_upload_rows (older uploads are back-filled from the file first). Returns files removed."""
    from pathlib import Path

    from app.video_performance.ingest import ImportError_, parse_import
    from app.video_performance.models import VtUpload, VtUploadRow

    if retention_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=retention_days)
    removed = 0
    with session_scope() as db:
        uploads = db.scalars(
            select(VtUpload).where(
                VtUpload.created_at < cutoff, VtUpload.file_deleted_at.is_(None), VtUpload.stored_filename.is_not(None)
            )
        ).all()
        for upload in uploads:
            path = Path(upload_dir) / Path(upload.stored_filename).name
            has_rows = db.scalar(select(exists().where(VtUploadRow.upload_id == upload.id)))
            if not has_rows and path.exists():
                try:
                    parsed = parse_import(upload.filename, path.read_bytes(), 50 * 1024 * 1024)
                except (OSError, ImportError_):
                    continue  # keep the file: its rows could not be saved
                db.add_all(
                    VtUploadRow(
                        upload_id=upload.id, row_number=r.row, creator_name=r.creator_name,
                        platform=r.parsed.platform or r.raw_platform, video_link=r.raw_link, username=r.owner_username,
                        status="invalid" if r.status == "invalid" else ("duplicate" if r.status == "duplicate" else "added"),
                        message=r.message,
                    )
                    for r in parsed.rows
                )
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            upload.file_deleted_at = utcnow()
            removed += 1
    return removed

