"""Database access for Video Performance (sync functions; async callers run them in threads)."""

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta

from sqlalchemy import and_, delete, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.db import ReadSessionLocal, session_scope
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
from app.video_performance.platforms import ChannelInfo, DiscoveredVideo, PlatformError, VideoMetrics
from app.video_performance.sentiment import SentimentResult
from app.video_performance.timeutil import local_day_start_utc
from app.video_performance.urls import ParsedVideo


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
def claim_videos(video_ids: list[int]) -> list[dict]:
    """Mark videos Processing (paused ones stay Paused) and return what the fetch step needs."""
    with session_scope() as db:
        videos = db.scalars(select(VtVideo).where(VtVideo.id.in_(video_ids))).all()
        analysed = set(db.scalars(select(VtSentiment.video_id).where(VtSentiment.video_id.in_(video_ids))))
        rows = []
        for video in videos:
            if video.status != VideoStatus.PAUSED:
                video.status = VideoStatus.PROCESSING
            rows.append(
                {
                    "id": video.id,
                    "platform": video.platform,
                    "identifier": video.video_identifier,
                    "owner_username": video.owner_username,
                    "needs_sentiment": video.id not in analysed,
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


def save_metrics(video_id: int, metrics: VideoMetrics) -> bool:
    """Store a new snapshot and the derived day-over-day numbers. Returns False if the video vanished."""
    now = utcnow()
    with session_scope() as db:
        video = db.get(VtVideo, video_id)
        if video is None:
            return False
        rate = engagement(metrics.views, metrics.likes, metrics.comments)
        previous = db.scalar(
            select(VtViewSnapshot.views)
            .where(
                VtViewSnapshot.video_id == video_id,
                VtViewSnapshot.captured_at < local_day_start_utc(now),
                VtViewSnapshot.views.is_not(None),
            )
            .order_by(VtViewSnapshot.captured_at.desc())
            .limit(1)
        )
        db.add(
            VtViewSnapshot(
                video_id=video_id, captured_at=now, views=metrics.views, likes=metrics.likes,
                comments=metrics.comments, engagement_rate=rate,
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

        video.current_views = metrics.views
        video.previous_views = previous
        video.views_gained = metrics.views - previous if metrics.views is not None and previous is not None else None
        video.growth_pct = (
            round(video.views_gained / previous * 100, 2) if video.views_gained is not None and previous else None
        )
        video.likes, video.comments = metrics.likes, metrics.comments
        video.engagement_rate = rate
        video.engagement_basis = "views" if rate is not None else None
        video.last_checked_at = now

        notes = list(metrics.notes)
        if metrics.views is None:
            notes.insert(0, "View count is not available from the API for this video")
        if video.status != VideoStatus.PAUSED:
            video.status = VideoStatus.PARTIAL if metrics.views is None else VideoStatus.TRACKING
        video.status_reason = "; ".join(notes) or None
        return True


def save_error(video_id: int, error: PlatformError) -> None:
    with session_scope() as db:
        video = db.get(VtVideo, video_id)
        if video is None:
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


def video_content(video_id: int) -> tuple[str | None, str | None]:
    with read_scope() as db:
        row = db.execute(select(VtVideo.title, VtVideo.caption).where(VtVideo.id == video_id)).first()
        return (row.title, row.caption) if row else (None, None)


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
        ids = list(db.scalars(select(VtVideo.id).where(VtVideo.status.in_(VideoStatus.ACTIVE_WORK))))
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
        video = VtVideo(
            platform=parsed.platform,
            video_identifier=parsed.identifier,
            video_url=parsed.url,
            creator_name=creator_name or None,
            owner_username=(owner_username or parsed.owner_username) if parsed.platform == "instagram" else None,
            source=source,
            upload_id=upload_id,
            status=VideoStatus.PENDING,
        )
        db.add(video)
        db.flush()
        _link_creator(db, video)
        return video.id


def existing_identifiers(platform_ids: list[tuple[str, str]]) -> set[tuple[str, str]]:
    if not platform_ids:
        return set()
    with read_scope() as db:
        conditions = [and_(VtVideo.platform == p, VtVideo.video_identifier == i) for p, i in platform_ids]
        found = set()
        for start in range(0, len(conditions), 200):
            rows = db.execute(select(VtVideo.platform, VtVideo.video_identifier).where(or_(*conditions[start : start + 200])))
            found.update((r.platform, r.video_identifier) for r in rows)
        return found


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
            video.channel_id = video.creator_id = None
            for field_name in (
                "title", "caption", "published_at", "current_views", "previous_views", "views_gained", "growth_pct",
                "likes", "comments", "engagement_rate", "engagement_basis", "sentiment", "sentiment_confidence",
                "last_checked_at", "status_reason",
            ):
                setattr(video, field_name, None)
            video.tracking_started_at = utcnow()
            reprocess = True
        if owner_username is not None and video.platform == "instagram" and owner_username != video.owner_username:
            video.owner_username = owner_username or None
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


def has_sentiment(video_id: int) -> bool:
    with read_scope() as db:
        return bool(db.scalar(select(exists().where(VtSentiment.video_id == video_id))))


# --------------------------------------------------------------------- import
def _import_rows(filename: str, stored: str | None, rows: list) -> tuple[str, list[int]]:
    """Insert new videos from an import in bulk and record the upload. Returns (upload_id, new video IDs)."""
    from app.video_performance.models import VtUpload, VtUploadRow

    candidates = [r for r in rows if r.status == "pending"]
    tracked = existing_identifiers([(r.parsed.platform, r.parsed.identifier) for r in candidates])
    with session_scope() as db:
        upload = VtUpload(filename=filename, stored_filename=stored, total_rows=len(rows))
        db.add(upload)
        db.flush()
        new_videos = []
        for row in candidates:
            if (row.parsed.platform, row.parsed.identifier) in tracked:
                row.status, row.message = "already_tracked", "Already being tracked"
                continue
            row.status = "added"
            new_videos.append(
                VtVideo(
                    platform=row.parsed.platform,
                    video_identifier=row.parsed.identifier,
                    video_url=row.parsed.url,
                    creator_name=row.creator_name,
                    owner_username=row.owner_username,
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
        return upload.id, [v.id for v in new_videos]


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
                if row.status == "added":
                    row.status = "pending"
    raise AssertionError("unreachable")


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

