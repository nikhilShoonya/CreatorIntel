"""VideoTracker - processing pipeline of the Video Performance module.

    fetch metrics (batched per platform) -> save snapshot + day-over-day numbers
    -> sentiment (once per video) ; discovery: tracked creator -> new videos -> same pipeline

Every video/creator is handled independently: one failure never stops a batch or a job.
All DB work runs in worker threads (remote database latency never blocks the event loop).
"""

import asyncio
import logging
import time
from collections import defaultdict
from collections.abc import Coroutine
from typing import Any

from app.config.settings import Settings, get_settings
from app.utils.logging import log_event
from app.utils.url_parser import parse_channel_link
from app.video_performance import repository as repo
from app.video_performance.models import JobType
from app.video_performance.platforms import (
    ChannelInfo,
    DiscoveredVideo,
    InstagramVideoClient,
    PlatformError,
    VideoMetrics,
    YouTubeVideoClient,
)
from app.video_performance.sentiment import VideoSentimentAnalyzer

logger = logging.getLogger("creatorintel.video_performance")

DISCOVERY_LOOKBACK = 15  # latest uploads/media checked per creator per run


class VideoTracker:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        youtube: YouTubeVideoClient | None = None,
        instagram: InstagramVideoClient | None = None,
        sentiment: VideoSentimentAnalyzer | None = None,
    ):
        self.settings = settings or get_settings()
        self.youtube = youtube or YouTubeVideoClient(self.settings)
        self.instagram = instagram or InstagramVideoClient(self.settings)
        self.sentiment = sentiment or VideoSentimentAnalyzer(self.settings)
        self._ai_semaphore = asyncio.Semaphore(3)
        self._job_locks = {JobType.METRICS_REFRESH: asyncio.Lock(), JobType.CREATOR_DISCOVERY: asyncio.Lock()}
        self._tasks: set[asyncio.Task] = set()

    # ------------------------------------------------------------ scheduling
    def _schedule(self, coro: Coroutine[Any, Any, Any], name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start_processing(self, video_ids: list[int]) -> None:
        if video_ids:
            self._schedule(self.process_videos(video_ids), f"vt-process-{video_ids[0]}")

    def start_creator_setup(self, creator_id: int, backfill: int) -> None:
        self._schedule(self.setup_creator(creator_id, backfill), f"vt-creator-{creator_id}")

    def start_discovery_for(self, creator_ids: list[int]) -> None:
        self._schedule(self.discover(creator_ids), "vt-discover")

    def start_job(self, job_type: str, trigger: str) -> bool:
        if self._job_locks[job_type].locked():
            return False
        runner = self.run_metrics_refresh if job_type == JobType.METRICS_REFRESH else self.run_discovery
        self._schedule(runner(trigger), f"vt-job-{job_type}")
        return True

    def job_running(self, job_type: str) -> bool:
        return self._job_locks[job_type].locked()

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def resume_after_restart(self) -> None:
        video_ids = await asyncio.to_thread(repo.recover_after_restart)
        if video_ids:
            log_event(logger, logging.INFO, "vt_resume", videos=len(video_ids))
            self.start_processing(video_ids)

    # ------------------------------------------------------------ processing
    async def process_videos(self, video_ids: list[int]) -> tuple[int, int]:
        """Fetch + store metrics for the given videos. Returns (succeeded, failed)."""
        started = time.perf_counter()
        rows = await asyncio.to_thread(repo.claim_videos, sorted(set(video_ids)))
        results: dict[int, VideoMetrics | PlatformError] = {}

        youtube_rows = [r for r in rows if r["platform"] == "youtube"]
        if youtube_rows:
            try:
                found = await self.youtube.videos([r["identifier"] for r in youtube_rows])
                for row in youtube_rows:
                    results[row["id"]] = found.get(row["identifier"]) or PlatformError(
                        PlatformError.NOT_FOUND, "YouTube video not found (deleted, private or wrong link)"
                    )
            except PlatformError as exc:
                for row in youtube_rows:
                    results[row["id"]] = exc
            except Exception:
                logger.exception("vt_youtube_batch_crashed")
                for row in youtube_rows:
                    results[row["id"]] = PlatformError(PlatformError.API, "Unexpected error while contacting YouTube")

        by_owner: dict[str | None, list[dict]] = defaultdict(list)
        for row in rows:
            if row["platform"] == "instagram":
                by_owner[row["owner_username"]].append(row)
        for owner, owner_rows in by_owner.items():
            await self._instagram_owner(owner, owner_rows, results)

        succeeded = failed = 0
        to_analyse: list[int] = []
        for row in rows:
            outcome = results.get(row["id"])
            ctx = {"video_id": row["id"], "platform": row["platform"], "stage": "metrics"}
            try:
                if isinstance(outcome, VideoMetrics):
                    await asyncio.to_thread(repo.save_metrics, row["id"], outcome)
                    succeeded += 1
                    if row["needs_sentiment"]:
                        to_analyse.append(row["id"])
                    log_event(logger, logging.INFO, "vt_video_ok", **ctx, views=outcome.views)
                else:
                    error = outcome or PlatformError(PlatformError.API, "No result")
                    await asyncio.to_thread(repo.save_error, row["id"], error)
                    failed += 1
                    log_event(logger, logging.WARNING, "vt_video_failed", **ctx, code=error.code, reason=error.message)
            except Exception:
                failed += 1
                logger.exception("vt_video_save_crashed video_id=%s", row["id"])

        await asyncio.gather(*(self._analyse(video_id) for video_id in to_analyse))
        log_event(
            logger, logging.INFO, "vt_batch_done",
            videos=len(rows), succeeded=succeeded, failed=failed, duration_s=f"{time.perf_counter() - started:.1f}",
        )
        return succeeded, failed

    async def _instagram_owner(self, owner: str | None, rows: list[dict], results: dict) -> None:
        if not owner:
            for row in rows:
                results[row["id"]] = PlatformError(
                    PlatformError.UNSUPPORTED,
                    "Instagram reel owner is unknown. Add the creator's Instagram username - the official API "
                    "can only find a reel through its owner's Professional account",
                )
            return
        pages = self.settings.video_tracking_instagram_scan_pages
        try:
            profile, media = await self.instagram.profile_media(owner, pages=pages)
        except PlatformError as exc:
            error = PlatformError(exc.code, f"@{owner}: {exc.message}")  # say which account was looked up
            for row in rows:
                results[row["id"]] = error
            return
        except Exception:
            logger.exception("vt_instagram_owner_crashed owner=%s", owner)
            for row in rows:
                results[row["id"]] = PlatformError(PlatformError.API, "Unexpected error while contacting Instagram")
            return
        by_code = {m.shortcode: m for m in media}
        scanned_all = len(media) < pages * 50
        for row in rows:
            item = by_code.get(row["identifier"])
            if item is None:
                results[row["id"]] = PlatformError(
                    PlatformError.NOT_FOUND if scanned_all else PlatformError.UNSUPPORTED,
                    f"Reel not found on @{owner}'s account" if scanned_all
                    else f"Reel is older than @{owner}'s latest {len(media)} posts that the official API returns",
                )
                continue
            results[row["id"]] = VideoMetrics(
                identifier=item.shortcode,
                views=item.views,
                likes=item.likes,
                comments=item.comments,
                caption=item.caption,
                published_at=item.published_at,
                owner_username=profile.get("username") or owner,
                notes=[] if item.is_video else ["This post is not a video/reel"],
            )

    async def _analyse(self, video_id: int) -> None:
        async with self._ai_semaphore:
            try:
                title, caption = await asyncio.to_thread(repo.video_content, video_id)
                result = await self.sentiment.analyze(title, caption, video_id=video_id)
                await asyncio.to_thread(repo.save_sentiment, video_id, result, self.sentiment.model_name)
                log_event(
                    logger, logging.INFO, "vt_sentiment", video_id=video_id, stage="sentiment",
                    sentiment=result.sentiment or "none", reason=result.error,
                )
            except Exception:
                logger.exception("vt_sentiment_crashed video_id=%s", video_id)

    # ------------------------------------------------------------- creators
    async def _resolve_creator(self, row: dict) -> None:
        parsed = parse_channel_link(row["channel_url"])
        if row["platform"] == "youtube":
            info = await self.youtube.resolve_channel(parsed)
        else:
            profile, _ = await self.instagram.profile_media(row["username"], pages=1, per_page=1)
            info = ChannelInfo(profile.get("id"), profile.get("name") or profile.get("username"), username=row["username"])
        await asyncio.to_thread(repo.save_creator_channel, row["id"], info)

    async def _latest_videos(self, row: dict, limit: int) -> list[DiscoveredVideo]:
        if row["platform"] == "youtube":
            if not row["uploads_playlist_id"]:
                return []
            return await self.youtube.recent_uploads(row["uploads_playlist_id"], limit)
        _, media = await self.instagram.profile_media(row["username"], pages=1, per_page=limit)
        return [DiscoveredVideo(m.shortcode, m.url, m.published_at) for m in media if m.is_video]

    async def setup_creator(self, creator_id: int, backfill: int) -> None:
        """Validate a newly added creator with the platform API and optionally track its latest videos."""
        row = await asyncio.to_thread(repo.creator_row, creator_id)
        if row is None:
            return
        try:
            await self._resolve_creator(row)
            row = await asyncio.to_thread(repo.creator_row, creator_id) or row
            new_ids: list[int] = []
            if backfill > 0:
                latest = await self._latest_videos(row, backfill)
                new_ids = await asyncio.to_thread(repo.add_discovered_videos, creator_id, latest[:backfill], False)
            await asyncio.to_thread(repo.save_creator_result, creator_id)
            self.start_processing(new_ids)
            log_event(logger, logging.INFO, "vt_creator_ready", creator_id=creator_id, platform=row["platform"], backfilled=len(new_ids))
        except PlatformError as exc:
            await asyncio.to_thread(repo.save_creator_result, creator_id, error=exc)
            log_event(logger, logging.WARNING, "vt_creator_failed", creator_id=creator_id, platform=row["platform"], reason=exc.message)

    async def discover(self, creator_ids: list[int]) -> tuple[int, int, int]:
        """Check creators for videos published since tracking started. Returns (ok, failed, new videos)."""
        ok = failed = 0
        new_video_ids: list[int] = []
        for creator_id in creator_ids:
            started = time.perf_counter()
            row = await asyncio.to_thread(repo.creator_row, creator_id)
            if row is None or not row["enabled"]:
                continue
            ctx = {"creator_id": creator_id, "platform": row["platform"], "stage": "discovery"}
            try:
                if row["platform"] == "youtube" and not row["uploads_playlist_id"]:
                    await self._resolve_creator(row)
                    row = await asyncio.to_thread(repo.creator_row, creator_id) or row
                latest = await self._latest_videos(row, DISCOVERY_LOOKBACK)
                since = row["tracking_since"]
                fresh = [v for v in latest if v.published_at is not None and since is not None and v.published_at >= since]
                ids = await asyncio.to_thread(repo.add_discovered_videos, creator_id, fresh, True)
                await asyncio.to_thread(repo.save_creator_result, creator_id, discovered=len(ids))
                new_video_ids.extend(ids)
                ok += 1
                log_event(logger, logging.INFO, "vt_discovery_ok", **ctx, new_videos=len(ids),
                          duration_s=f"{time.perf_counter() - started:.1f}")
            except PlatformError as exc:
                failed += 1
                await asyncio.to_thread(repo.save_creator_result, creator_id, error=exc)
                log_event(logger, logging.WARNING, "vt_discovery_failed", **ctx, code=exc.code, reason=exc.message)
            except Exception:
                failed += 1
                logger.exception("vt_discovery_crashed creator_id=%s", creator_id)
        if new_video_ids:
            await self.process_videos(new_video_ids)
        return ok, failed, len(new_video_ids)

    # ----------------------------------------------------------------- jobs
    async def run_metrics_refresh(self, trigger: str) -> None:
        """JOB 1 - refresh every actively tracked video (daily)."""
        async with self._job_locks[JobType.METRICS_REFRESH]:
            run_id = await asyncio.to_thread(repo.start_job, JobType.METRICS_REFRESH, trigger)
            started = time.perf_counter()
            succeeded = failed = total = 0
            try:
                ids = await asyncio.to_thread(repo.videos_due_for_refresh, self.settings.video_tracking_max_days)
                total = len(ids)
                for start in range(0, len(ids), 200):  # bounded batches
                    ok, bad = await self.process_videos(ids[start : start + 200])
                    succeeded, failed = succeeded + ok, failed + bad
                message = f"Refreshed {succeeded} of {total} videos in {time.perf_counter() - started:.0f}s"
                await asyncio.to_thread(repo.finish_job, run_id, total, succeeded, failed, message)
            except Exception as exc:
                logger.exception("vt_refresh_job_crashed")
                await asyncio.to_thread(repo.finish_job, run_id, total, succeeded, failed, f"Job stopped: {type(exc).__name__}", False)
            log_event(logger, logging.INFO, "vt_job_metrics_refresh", trigger=trigger, total=total, succeeded=succeeded, failed=failed)

    async def run_discovery(self, trigger: str) -> None:
        """JOB 2 - look for newly published videos of every enabled creator (daily)."""
        async with self._job_locks[JobType.CREATOR_DISCOVERY]:
            run_id = await asyncio.to_thread(repo.start_job, JobType.CREATOR_DISCOVERY, trigger)
            started = time.perf_counter()
            ok = failed = new = total = 0
            try:
                creator_ids = await asyncio.to_thread(repo.enabled_creator_ids)
                total = len(creator_ids)
                ok, failed, new = await self.discover(creator_ids)
                message = f"Checked {total} creators, {new} new videos, in {time.perf_counter() - started:.0f}s"
                await asyncio.to_thread(repo.finish_job, run_id, total, ok, failed, message)
            except Exception as exc:
                logger.exception("vt_discovery_job_crashed")
                await asyncio.to_thread(repo.finish_job, run_id, total, ok, failed, f"Job stopped: {type(exc).__name__}", False)
            log_event(logger, logging.INFO, "vt_job_creator_discovery", trigger=trigger, creators=total, new_videos=new, failed=failed)
