"""VideoTracker - processing pipeline of the Video Performance module.

    fetch metrics (batched per platform) -> save snapshot + day-over-day numbers
    -> sentiment (once per video) ; discovery: tracked creator -> new videos -> same pipeline

Every video/creator is handled independently: one failure never stops a batch or a job.
All DB work runs in worker threads (remote database latency never blocks the event loop).
"""

import asyncio
import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Coroutine
from typing import Any
from sqlalchemy.exc import SQLAlchemyError

from app.config.settings import Settings, get_settings
from app.utils.logging import log_event
from app.utils.time import utcnow
from app.utils.url_parser import parse_channel_link
from app.services import api_usage, meta_budget
from app.video_performance import repository as repo
from app.video_performance import apify_repository as apify_jobs
from app.video_performance.apify_reels import (
    ApifyReelClient, BUSY as APIFY_BUSY, ERROR as APIFY_ERROR, NOT_FOUND as APIFY_NOT_FOUND,
)
from app.video_performance.models import JobType, VideoStatus
from app.video_performance.platforms import (
    META_WAIT,
    ChannelInfo,
    DiscoveredVideo,
    FacebookVideoClient,
    InstagramMedia,
    InstagramVideoClient,
    PlatformError,
    VideoMetrics,
    YouTubeVideoClient,
)
from app.video_performance.sentiment import VideoSentimentAnalyzer
from app.video_performance.timeutil import job_times, slot_start_utc, tracking_tz
from app.video_performance.urls import parse_instagram_handle

logger = logging.getLogger("creatorintel.video_performance")

DISCOVERY_LOOKBACK = 15  # latest uploads/media checked per creator per run
META_QUEUE_POLL_SECONDS = 5 * 60  # how often videos waiting for Meta's hourly limit are continued (within budget)
META_QUEUE_STARTUP_DELAY_SECONDS = 20
# Reels with an unknown owner are looked for on Instagram accounts the app already knows (exact shortcode match).
OWNER_SEARCH_MAX_ACCOUNTS = 30  # extra accounts read per run (1 call each) - well inside Meta's hourly allowance
OWNER_SEARCH_MAX_CALLS = 30  # media calls, including pagination and view-field fallback
OWNER_UNKNOWN = (
    "Instagram owner unresolved. Add the posting creator's Instagram username and Retry - the official API "
    "can only find a reel through its owner's Professional account"
)
OWNER_SEARCH_PAUSED = (
    "Instagram API rate limit reached - finding this reel's owner continues automatically after the hourly limit"
)
_META_REFUSED_REASON = "Meta's rate limit was reached"  # meta_budget's reason once Meta itself refused a call
_METRIC_LABELS = {"call_count": "call count", "total_cputime": "CPU time", "total_time": "total time"}


def _owner_search_pause_message() -> str:
    """Why CreatorIntel's own Meta safety stop paused the owner search (not a refusal by Meta). No ': ' inside, so
    repository.meta_wait_note stores it unchanged and the Meta queue still picks the reel up."""
    status = meta_budget.status()
    _, metrics, _, _ = api_usage.latest_meta_reading()
    reported = ", ".join(f"{label} {value:g}%" for name, label in _METRIC_LABELS.items()
                         if (value := getattr(metrics, name)) is not None)
    if status["reason"] and status["reason"].startswith("Meta reports") and reported:
        detail = f"Meta-reported usage reached the {status['stop_percent']:g}% safety stop ({reported})"
    elif status["reason"] and status["reason"].endswith("calls made in the last hour"):
        detail = f"the app's own budget of {status['hourly_budget']} Meta calls per hour is used"
    else:
        detail = "Meta's reported usage is at the safety stop"
    when = (f"; finding this reel's owner continues after {status['resume_at'].astimezone(tracking_tz()):%H:%M}"
            if status["resume_at"] else "; finding this reel's owner continues automatically")
    return f"{META_WAIT} (CreatorIntel safety stop, not a Meta refusal - {detail}{when})"


async def _owner_search_paused(error: PlatformError) -> PlatformError:
    """The owner search stopped on Meta's limit: say whether Meta refused or the app's own safety stop paused it."""
    if error.code != PlatformError.RATE_LIMITED:
        return error
    if error.message.startswith(META_WAIT) and _META_REFUSED_REASON not in error.message:
        return PlatformError(PlatformError.RATE_LIMITED, await asyncio.to_thread(_owner_search_pause_message))
    return PlatformError(PlatformError.RATE_LIMITED, OWNER_SEARCH_PAUSED)  # Meta itself refused a call


def _reel_metrics(item: "InstagramMedia", owner: str) -> VideoMetrics | PlatformError:
    if not item.is_video:
        return PlatformError(PlatformError.UNSUPPORTED, "The exact matching Instagram post is not a video/reel")
    return VideoMetrics(
        identifier=item.shortcode,
        views=item.views,
        likes=item.likes,
        comments=item.comments,
        caption=item.caption,
        published_at=item.published_at,
        owner_username=owner,
        notes=[] if item.is_video else ["This post is not a video/reel"],
    )


class VideoTracker:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        youtube: YouTubeVideoClient | None = None,
        instagram: InstagramVideoClient | None = None,
        facebook: FacebookVideoClient | None = None,
        sentiment: VideoSentimentAnalyzer | None = None,
        apify: ApifyReelClient | None = None,
    ):
        self.settings = settings or get_settings()
        self.youtube = youtube or YouTubeVideoClient(self.settings)
        self.instagram = instagram or InstagramVideoClient(self.settings)
        self.facebook = facebook or FacebookVideoClient(self.settings)
        self.sentiment = sentiment or VideoSentimentAnalyzer(self.settings)
        self.apify = apify or ApifyReelClient(self.settings)
        self._job_locks = {JobType.METRICS_REFRESH: asyncio.Lock(), JobType.CREATOR_DISCOVERY: asyncio.Lock()}
        self._tasks: set[asyncio.Task] = set()
        self._in_flight: set[int] = set()
        # Live progress of a running job: job_type -> [done, total]
        self._progress: dict[str, list[int]] = {}
        self._owner_search_after: str | None = None  # rotate bounded candidate batches on subsequent retries

    # ------------------------------------------------------------ scheduling
    def _schedule(self, coro: Coroutine[Any, Any, Any], name: str) -> None:
        task = asyncio.create_task(coro, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def start_processing(self, video_ids: list[int]) -> None:
        if video_ids:
            self._schedule(self.process_videos(video_ids), f"vt-process-{video_ids[0]}")

    # ------------------------------------------------------------ Meta queue
    def start_meta_queue(self, interval_seconds: float = META_QUEUE_POLL_SECONDS) -> None:
        """Continue videos waiting for Meta's hourly limit in the background (state is in the database,
        so this also picks up what was waiting before a restart)."""
        self._schedule(self._meta_queue_loop(interval_seconds), "vt-meta-queue")

    async def _meta_queue_loop(self, interval_seconds: float) -> None:
        await asyncio.sleep(META_QUEUE_STARTUP_DELAY_SECONDS)
        while True:
            try:
                while await self.process_meta_queue():  # keep going while the budget allows
                    pass
            except Exception:
                logger.exception("vt_meta_queue_crashed")
            await asyncio.sleep(interval_seconds)

    async def process_meta_queue(self) -> int:
        """Continue videos waiting for Meta's hourly limit, oldest first, as far as the Meta budget allows.
        Returns how many were picked up (0 = nothing waiting, or no budget left right now)."""
        available = meta_budget.available()
        if available <= 0:
            return 0
        # every waiting video needs at least one call (reels of one owner share it), so this never over-picks
        ids = [i for i in await asyncio.to_thread(repo.meta_waiting_ids, available) if i not in self._in_flight]
        if not ids:
            return 0
        log_event(logger, logging.INFO, "vt_meta_queue_continue", videos=len(ids), budget_left=available)
        await self.process_videos(ids)
        return len(ids)

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

    def job_progress(self, job_type: str) -> tuple[int, int] | None:
        """(done, total) of the running job, or None when it is not running / not counted yet."""
        progress = self._progress.get(job_type)
        return (progress[0], progress[1]) if progress and self.job_running(job_type) else None

    def _tick(self, job_type: str) -> None:
        progress = self._progress.get(job_type)
        if progress:
            progress[0] = min(progress[0] + 1, progress[1])

    async def shutdown(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

    async def resume_after_restart(self) -> None:
        try:  # current numbers that came from an outdated (lower) API count get the real higher reading back
            await asyncio.to_thread(repo.repair_outdated_counts)
        except Exception:
            logger.exception("vt_repair_outdated_counts_failed")
        try:  # rate-limited videos left by older versions join the Meta queue (continued by start_meta_queue)
            await asyncio.to_thread(repo.repair_temporary_failures)
        except Exception:
            logger.exception("vt_repair_temporary_failures_failed")
        video_ids = await asyncio.to_thread(repo.recover_after_restart)
        if video_ids:
            log_event(logger, logging.INFO, "vt_resume", videos=len(video_ids))
            self.start_processing(video_ids)

    # ------------------------------------------------------------ processing
    async def process_videos(self, video_ids: list[int], on_done: Callable[[], None] | None = None) -> tuple[int, int]:
        """Fetch + store metrics for the given videos. Returns (succeeded, failed).

        ``on_done`` is called once per video when its result has been saved (used for job progress).
        Instagram / Facebook videos that do not fit Meta's hourly limit wait in the Meta queue (Pending).
        """
        # Skip videos another task is already checking (e.g. "Refresh now" during the daily job).
        requested_at = utcnow()
        wanted = sorted(set(video_ids) - self._in_flight)
        if on_done:
            for _ in range(len(set(video_ids)) - len(wanted)):
                on_done()
        if not wanted:
            return 0, 0
        self._in_flight.update(wanted)
        try:
            return await self._process_videos(wanted, on_done, requested_at)
        finally:
            self._in_flight.difference_update(wanted)

    async def _process_videos(self, video_ids: list[int], on_done: Callable[[], None] | None = None, requested_at=None) -> tuple[int, int]:
        started = time.perf_counter()
        rows = await asyncio.to_thread(repo.claim_videos, video_ids)
        results: dict[int, VideoMetrics | PlatformError] = {}
        # Reels go to Apify first - except ones the Meta queue continues: Apify already failed for them.
        apify_rows = [r for r in rows if r["platform"] == "instagram" and r.get("instagram_media_kind") == "reel"
                      and self.settings.apify_reels_configured and not r.get("meta_queue")]
        apify_ids = {r["id"] for r in apify_rows}
        apify_associations = {}
        if apify_rows:
            try:
                apify_results, apify_associations = await self.apify.videos(apify_rows, requested_at=requested_at)
                results.update(apify_results)
            except Exception as exc:
                # SDK exceptions may include credential-bearing request details.
                log_event(logger, logging.ERROR, "vt_apify_batch_failed", error_type=type(exc).__name__,
                          database_error=isinstance(exc, SQLAlchemyError))
                message = ("Apify database preparation failed; apply pending migrations with python -m alembic upgrade head "
                           "and retry. No automatic Meta fallback" if isinstance(exc, SQLAlchemyError)
                           else "Apify processing failed; no automatic Meta fallback. Retry later")
                for row in apify_rows:
                    results[row["id"]] = PlatformError(APIFY_ERROR, message)
        # Reels Apify could not read fall back to Meta's official API (Business Discovery) in this same run.
        apify_failed: dict[int, PlatformError] = {}
        for row in apify_rows:
            outcome = results.get(row["id"])
            if isinstance(outcome, PlatformError) and outcome.code == APIFY_NOT_FOUND and not row.get("owner_username"):
                # A Reel that does not exist is in no account's posts: searching every known account would only use
                # up Meta's hourly limit. Video Down ("Retry failed" skips it; a manual Retry checks it again).
                results[row["id"]] = PlatformError(PlatformError.NOT_FOUND, outcome.message)
            elif isinstance(outcome, PlatformError) and outcome.code in (APIFY_ERROR, APIFY_NOT_FOUND):
                apify_failed[row["id"]] = results.pop(row["id"])
                apify_ids.discard(row["id"])

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
            if row["platform"] == "instagram" and row["id"] not in apify_ids:
                if row.get("owner_conflict"):
                    results[row["id"]] = PlatformError(PlatformError.OWNER_CONFLICT, row["owner_conflict"] + "; correct the owner metadata and Retry")
                else:
                    by_owner[row["owner_username"]].append(row)
        blocked: PlatformError | None = None
        scanned: dict[str, list[InstagramMedia]] = {}  # accounts whose media was read in this run
        scan_details: dict[str, dict] = {}
        searchable: list[dict] = []  # reels whose owner is unknown or wrong - looked for on other known accounts
        for owner, owner_rows in by_owner.items():
            if blocked is not None:  # Meta refused: don't spend more calls in this run
                for row in owner_rows:
                    results[row["id"]] = blocked
                continue
            searchable += await self._instagram_owner(owner, owner_rows, results, scanned, scan_details)
            for row in owner_rows:
                outcome = results.get(row["id"])
                if isinstance(outcome, PlatformError) and outcome.code in (PlatformError.RATE_LIMITED, PlatformError.AUTH, PlatformError.CONFIG):
                    blocked = outcome
                    break
        if searchable and blocked is None:
            await self._find_reel_owners(searchable, scanned, results, scan_details)
        elif searchable:  # Meta refused before the owner search could run: search again with the automatic re-check
            paused = await _owner_search_paused(blocked)
            for row in searchable:
                results[row["id"]] = paused

        facebook_rows = [r for r in rows if r["platform"] == "facebook"]
        if facebook_rows:
            await asyncio.gather(*(self._facebook_video(row, results) for row in facebook_rows))

        for video_id, apify_error in apify_failed.items():
            outcome = results.get(video_id)
            if isinstance(outcome, VideoMetrics):
                outcome.notes.append("Apify could not read this Reel; metrics from Meta's official API")
            elif not (isinstance(outcome, PlatformError) and outcome.code == PlatformError.RATE_LIMITED):
                # Both failed: Apify's rules apply (stored numbers kept, never Meta's queue). Meta waits keep their
                # own status - the Meta queue continues those with Meta only, without another Apify run.
                meta = outcome.message if isinstance(outcome, PlatformError) else "no result"
                # "does not exist" from Apify and not found by Meta either: Video Down, which "Retry failed" skips
                # (each Apify attempt is billed even when the Reel is gone). A manual Retry still checks it again.
                code = PlatformError.NOT_FOUND if apify_error.code == APIFY_NOT_FOUND else APIFY_ERROR
                results[video_id] = PlatformError(code, f"{apify_error.message}. Meta fallback - {meta}")

        succeeded = failed = 0
        to_analyse: list[int] = []
        for row in rows:
            outcome = results.get(row["id"])
            persisted = False
            ctx = {"video_id": row["id"], "platform": row["platform"], "stage": "metrics"}
            try:
                if isinstance(outcome, VideoMetrics):
                    if row.get("owner_warning"):
                        outcome.notes.append(row["owner_warning"] + "; verified stored owner retained")
                    saved = await asyncio.to_thread(repo.save_metrics, row["id"], outcome)
                    if not saved:
                        raise LookupError("Video was removed before metrics could be persisted")
                    succeeded += 1
                    persisted = True
                    if row["needs_sentiment"]:
                        to_analyse.append(row["id"])
                    log_event(logger, logging.INFO, "vt_video_ok", **ctx, views=outcome.views)
                elif isinstance(outcome, PlatformError) and outcome.code == APIFY_BUSY:
                    pass  # another worker owns this batch; do not overwrite its status
                else:
                    error = outcome or PlatformError(PlatformError.API, "No result")
                    await asyncio.to_thread(repo.save_error, row["id"], error)
                    persisted = True
                    failed += 1
                    log_event(logger, logging.WARNING, "vt_video_failed", **ctx, code=error.code, reason=error.message)
            except repo.Conflict as exc:
                failed += 1
                await asyncio.to_thread(repo.save_error, row["id"], PlatformError(
                    APIFY_ERROR if row["id"] in apify_ids else PlatformError.OWNER_CONFLICT, str(exc)))
                persisted = True
            except Exception:
                failed += 1
                logger.exception("vt_video_save_crashed video_id=%s", row["id"])
                try:
                    await asyncio.to_thread(repo.save_error, row["id"], PlatformError(
                        APIFY_ERROR if row["id"] in apify_ids else PlatformError.API,
                        "Metrics could not be persisted; stored numbers retained. Retry this video",
                    ))
                except Exception:
                    logger.exception("vt_video_save_error_crashed video_id=%s", row["id"])
            if persisted and row["id"] in apify_associations:
                await asyncio.to_thread(apify_jobs.acknowledge, row["id"], apify_associations[row["id"]])
            if on_done:
                on_done()
        if on_done:
            for _ in range(len(video_ids) - len(rows)):  # videos that could not be claimed (deleted/paused)
                on_done()

        if to_analyse:
            await self._analyse(to_analyse)
        log_event(
            logger, logging.INFO, "vt_batch_done",
            videos=len(rows), succeeded=succeeded, failed=failed, duration_s=f"{time.perf_counter() - started:.1f}",
        )
        return succeeded, failed

    async def _facebook_video(self, row: dict, results: dict) -> None:
        try:
            results[row["id"]] = await self.facebook.video(row["identifier"])
        except PlatformError as exc:
            if exc.code == PlatformError.NOT_FOUND and not row.get("seen"):
                # Never seen before: the ID may simply not be on our Page (Meta returns the same error).
                exc = PlatformError(
                    PlatformError.UNSUPPORTED,
                    "Facebook video could not be resolved with the configured Page/API access. Only videos posted "
                    "on your Facebook Page can be tracked (or the video is deleted/private)",
                )
            results[row["id"]] = exc
        except Exception:
            logger.exception("vt_facebook_video_crashed video_id=%s", row["id"])
            results[row["id"]] = PlatformError(PlatformError.API, "Unexpected error while contacting Facebook")

    async def _instagram_owner(
        self, owner: str | None, rows: list[dict], results: dict, scanned: dict[str, list[InstagramMedia]] | None = None,
        scan_details: dict[str, dict] | None = None,
    ) -> list[dict]:
        """Read one owner's media and match its reels. Returns the reels to look for on other known accounts
        (owner unknown, or a reel never seen that is not on the given account - e.g. a collab reel)."""
        scanned = {} if scanned is None else scanned
        scan_details = {} if scan_details is None else scan_details
        owner = parse_instagram_handle(owner)
        if not owner:
            for row in rows:
                results[row["id"]] = PlatformError(PlatformError.OWNER_UNRESOLVED, OWNER_UNKNOWN)
            return list(rows)
        pages = self.settings.video_tracking_instagram_scan_pages
        try:  # one read per owner per run; older pages only while a wanted reel is still missing
            profile, media = await self.instagram.profile_media(owner, pages=pages, find={r["identifier"] for r in rows})
        except PlatformError as exc:
            if exc.code == PlatformError.UNSUPPORTED and not any(row.get("owner_verified") for row in rows):
                for row in rows:
                    results[row["id"]] = PlatformError(
                        PlatformError.OWNER_UNRESOLVED,
                        f"Instagram owner unresolved: supplied account @{owner} is unavailable through Business Discovery; "
                        "check the posting creator's Professional-account username and Retry",
                    )
                return list(rows)
            # say which account was looked up - except for token/config problems, which are not about it
            error = exc if exc.code in (PlatformError.AUTH, PlatformError.CONFIG) else PlatformError(
                exc.code, f"@{owner}: {exc.message}"
            )
            for row in rows:
                results[row["id"]] = error
            return []
        except Exception:
            logger.exception("vt_instagram_owner_crashed owner=%s", owner)
            for row in rows:
                results[row["id"]] = PlatformError(PlatformError.API, "Unexpected error while contacting Instagram")
            return []
        owner = parse_instagram_handle(profile.get("username")) or owner
        scanned[owner] = media
        scan_details[owner] = profile
        by_code = {m.shortcode: m for m in media}
        scanned_all = profile.get("_scan_complete", False)
        searchable: list[dict] = []
        for row in rows:
            item = by_code.get(row["identifier"])
            if item is None:
                if profile.get("_scan_error"):
                    error = profile["_scan_error"]
                elif not scanned_all:
                    error = PlatformError(
                        PlatformError.OWNER_UNRESOLVED,
                        f"Instagram owner unresolved: search incomplete after {len(media)} posts on @{owner} "
                        "(page/request limit). Check the posting username or retry with a larger configured scan window",
                    )
                elif row.get("owner_verified", row.get("seen")):
                    error = PlatformError(PlatformError.NOT_FOUND, f"Reel no longer found on @{owner}'s account")
                else:  # never found: wrong username in the file, or a collab reel posted from another account
                    error = PlatformError(
                        PlatformError.OWNER_UNRESOLVED,
                        f"Reel not found on @{owner}'s posts - check the Instagram username (a collab reel is "
                        f"listed under the account that posted it)",
                    )
                if not row.get("owner_verified", row.get("seen")) and not profile.get("_scan_error"):
                    searchable.append(row)
                results[row["id"]] = error
                continue
            results[row["id"]] = _reel_metrics(item, profile.get("username") or owner)
        return searchable

    async def _find_reel_owners(
        self, rows: list[dict], scanned: dict[str, list[InstagramMedia]], results: dict,
        scan_details: dict[str, dict] | None = None,
    ) -> None:
        """Look for reels with an unknown (or wrong) owner in the recent posts of Instagram accounts the app
        already knows. Matching uses exact shortcodes. Reuse this run's media, then read cursor pages within
        both account and request budgets. Auth/config errors stop the search; rate limits enter the Meta queue."""
        wanted = {row["identifier"]: row for row in rows}
        scan_details = {} if scan_details is None else scan_details

        def match(owner: str, media: list[InstagramMedia]) -> None:
            for item in media:
                row = wanted.pop(item.shortcode, None)
                if row is not None:
                    results[row["id"]] = _reel_metrics(item, owner)
                    log_event(logger, logging.INFO, "vt_reel_owner_found", video_id=row["id"], owner=owner)

        for owner, media in scanned.items():
            match(owner, media)
        searched = 0
        stopped: PlatformError | None = None
        incomplete = any(not scan_details.get(owner, {}).get("_scan_complete", False) for owner in scanned)
        failures = 0
        budget = [OWNER_SEARCH_MAX_CALLS]
        candidates = []
        attempted = 0
        known: set[str] = set(scanned)
        history: dict[str, set[str]] = {}
        if wanted:
            all_known = list(dict.fromkeys(
                handle for value in await asyncio.to_thread(repo.instagram_owner_candidates)
                if (handle := parse_instagram_handle(value))
            ))
            known |= set(all_known)
            # Only previously exhausted media lists can rule out an account.
            history = await asyncio.to_thread(repo.owner_search_history, list(wanted))
            candidates = [c for c in all_known if c not in scanned and any(c not in history[code] for code in wanted)]
            if self._owner_search_after in candidates:
                start = candidates.index(self._owner_search_after) + 1
                candidates = candidates[start:] + candidates[:start]
            for candidate in candidates[:OWNER_SEARCH_MAX_ACCOUNTS]:
                if not wanted or budget[0] <= 0:
                    break
                attempted += 1
                try:
                    profile, media = await self.instagram.profile_media(
                        candidate, pages=self.settings.video_tracking_instagram_scan_pages,
                        find=set(wanted), request_budget=budget,
                    )
                except PlatformError as exc:
                    if exc.code in (PlatformError.RATE_LIMITED, PlatformError.AUTH, PlatformError.CONFIG):
                        log_event(logger, logging.WARNING, "vt_reel_owner_search_stopped", code=exc.code)
                        stopped = exc
                        break  # keep the rest of Meta's hourly allowance / the token problem is shown elsewhere
                    failures += 1
                    self._owner_search_after = candidate
                    continue  # e.g. no longer a Professional account
                except Exception:
                    logger.exception("vt_reel_owner_search_crashed account=%s", candidate)
                    failures += 1
                    self._owner_search_after = candidate
                    continue
                self._owner_search_after = candidate
                searched += 1
                scanned[candidate] = media
                scan_details[candidate] = profile
                incomplete = incomplete or not profile.get("_scan_complete", False)
                match(profile.get("username") or candidate, media)
                if profile.get("_scan_error"):
                    error = profile["_scan_error"]
                    if error.code in (PlatformError.RATE_LIMITED, PlatformError.AUTH, PlatformError.CONFIG):
                        stopped = error
                        break
                    failures += 1
        log_event(logger, logging.INFO, "vt_reel_owner_search", reels=len(rows), found=len(rows) - len(wanted),
                  accounts_checked=len(scanned), new_calls=searched)
        found_codes = [row["identifier"] for row in rows if row["identifier"] not in wanted]
        if found_codes:
            await asyncio.to_thread(repo.forget_owner_search, found_codes)
        # accounts read completely without error this run: remembered as "not here" for the reels still missing
        read_ok = {name for name in scanned
                   if scan_details.get(name, {}).get("_scan_complete", False)
                   and not scan_details.get(name, {}).get("_scan_error")}
        if wanted and read_ok:
            await asyncio.to_thread(repo.record_owner_search, {code: read_ok for code in wanted})
        paused = await _owner_search_paused(stopped) if stopped is not None else None
        for row in wanted.values():  # still unknown: say what was tried
            searched_for_it = history.get(row["identifier"], set()) | read_ok
            # every Instagram account the app knows was checked: a Retry makes no new call until a new account is known
            exhausted = bool(known) and not (known - searched_for_it)
            if stopped:
                results[row["id"]] = paused
            else:
                limited = not exhausted and (incomplete or attempted < len(candidates) or failures > 0)
                detail = (f"search incomplete: account/page/request limit or API errors ({failures} accounts unavailable)"
                          if limited else "no exact match in the available media of all known candidate accounts - "
                          "each was checked once and is not read again for this reel")
                results[row["id"]] = PlatformError(
                    PlatformError.OWNER_UNRESOLVED,
                    f"{OWNER_UNKNOWN} ({detail}; {len(searched_for_it)} of {len(known)} known accounts checked). "
                    "This is not a global Instagram search",
                )

    async def _analyse(self, video_ids: list[int]) -> None:
        """Sentiment for new videos, several per AI request. Pending ones are not saved, so the next run retries them."""
        try:
            contents = await asyncio.to_thread(repo.video_contents, video_ids)
            results = await self.sentiment.analyze_many((vid, title, caption) for vid, (title, caption) in contents.items())
        except Exception:
            logger.exception("vt_sentiment_crashed videos=%s", len(video_ids))
            return
        for video_id, result in results.items():
            ctx = {"video_id": video_id, "stage": "sentiment", "sentiment": result.sentiment or "none", "reason": result.error}
            if result.pending:
                log_event(logger, logging.INFO, "vt_sentiment_pending", **ctx)
                continue
            try:
                await asyncio.to_thread(repo.save_sentiment, video_id, result, result.model or self.sentiment.model_name)
                log_event(logger, logging.INFO, "vt_sentiment", **ctx)
            except Exception:
                logger.exception("vt_sentiment_save_crashed video_id=%s", video_id)

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
        except Exception:
            # A background task must always leave a visible terminal state.
            # Without this, an unexpected DB/client error leaves the creator
            # stuck at Pending indefinitely with no way to understand why.
            error = PlatformError(PlatformError.API, "Unexpected error while validating this creator")
            await asyncio.to_thread(repo.save_creator_result, creator_id, error=error)
            logger.exception("vt_creator_setup_crashed creator_id=%s", creator_id)

    async def discover(
        self, creator_ids: list[int], on_done: Callable[[], None] | None = None
    ) -> tuple[int, int, int]:
        """Check creators for videos published since tracking started. Returns (ok, failed, new videos).

        ``on_done`` is called once per creator when it has been checked (used for job progress).
        """
        ok = failed = 0
        new_video_ids: list[int] = []
        for creator_id in creator_ids:
            try:
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
                    error = PlatformError(PlatformError.API, "Unexpected error while discovering videos")
                    await asyncio.to_thread(repo.save_creator_result, creator_id, error=error)
                    logger.exception("vt_discovery_crashed creator_id=%s", creator_id)
            finally:
                if on_done:
                    on_done()
        if new_video_ids:
            await self.process_videos(new_video_ids)
        return ok, failed, len(new_video_ids)

    # ----------------------------------------------------------------- jobs
    async def run_metrics_refresh(self, trigger: str) -> None:
        """JOB 1 - refresh every actively tracked video (daily)."""
        await self._exclusive(JobType.METRICS_REFRESH, trigger, self._metrics_refresh)

    async def run_discovery(self, trigger: str) -> None:
        """JOB 2 - look for newly published videos of every enabled creator (daily)."""
        await self._exclusive(JobType.CREATOR_DISCOVERY, trigger, self._discovery)

    async def _exclusive(self, job_type: str, trigger: str, body) -> None:
        """Run a job at most once at a time - in this process and across processes sharing the database."""
        async with self._job_locks[job_type]:
            if trigger != "manual":
                # Automatic runs: once per daily slot (since the most recent scheduled time), never based on
                # how many hours ago the last run was - a manual run yesterday afternoon must not cancel today's.
                slot = slot_start_utc(job_times(self.settings)[job_type])
                last = await asyncio.to_thread(repo.last_completed_run, job_type)
                if last is not None and last >= slot:
                    log_event(logger, logging.INFO, "vt_job_skipped_already_ran", job=job_type, trigger=trigger)
                    return
            owner = uuid.uuid4().hex
            if not await asyncio.to_thread(repo.acquire_job_lock, job_type, owner):
                log_event(logger, logging.INFO, "vt_job_skipped_running_elsewhere", job=job_type, trigger=trigger)
                return
            try:
                await body(trigger)
            finally:
                await asyncio.to_thread(repo.release_job_lock, job_type, owner)

    async def _metrics_refresh(self, trigger: str) -> None:
        run_id = await asyncio.to_thread(repo.start_job, JobType.METRICS_REFRESH, trigger)
        started = time.perf_counter()
        succeeded = failed = total = 0
        try:
            ids = await asyncio.to_thread(
                repo.videos_due_for_refresh, self.settings.video_tracking_max_days,
                self.settings.video_tracking_down_recheck_days,
            )
            total = len(ids)
            self._progress[JobType.METRICS_REFRESH] = [0, total]
            tick = lambda: self._tick(JobType.METRICS_REFRESH)  # noqa: E731
            for start in range(0, len(ids), 200):  # bounded batches
                ok, bad = await self.process_videos(ids[start : start + 200], tick)
                succeeded, failed = succeeded + ok, failed + bad
            counts = await asyncio.to_thread(repo.status_counts, ids) if failed else {}
            details = [
                f"{n} {label}" for label, n in (
                    ("down", counts.get(VideoStatus.VIDEO_DOWN, 0)),
                    ("unsupported", counts.get(VideoStatus.UNSUPPORTED, 0)),
                    ("failed", counts.get(VideoStatus.FAILED, 0)),
                ) if n
            ]
            message = (f"Refreshed {succeeded} of {total} videos" + (f" ({', '.join(details)})" if details else "")
                       + f" in {time.perf_counter() - started:.0f}s")
            await asyncio.to_thread(repo.finish_job, run_id, total, succeeded, failed, message)
        except Exception as exc:
            logger.exception("vt_refresh_job_crashed")
            await asyncio.to_thread(repo.finish_job, run_id, total, succeeded, failed, f"Job stopped: {type(exc).__name__}", False)
        finally:
            self._progress.pop(JobType.METRICS_REFRESH, None)
        log_event(logger, logging.INFO, "vt_job_metrics_refresh", trigger=trigger, total=total, succeeded=succeeded, failed=failed)

    async def _discovery(self, trigger: str) -> None:
        run_id = await asyncio.to_thread(repo.start_job, JobType.CREATOR_DISCOVERY, trigger)
        started = time.perf_counter()
        ok = failed = new = total = 0
        try:
            creator_ids = await asyncio.to_thread(repo.enabled_creator_ids)
            total = len(creator_ids)
            self._progress[JobType.CREATOR_DISCOVERY] = [0, total]
            ok, failed, new = await self.discover(creator_ids, lambda: self._tick(JobType.CREATOR_DISCOVERY))
            message = f"Checked {total} creators, {new} new videos, in {time.perf_counter() - started:.0f}s"
            await asyncio.to_thread(repo.finish_job, run_id, total, ok, failed, message)
        except Exception as exc:
            logger.exception("vt_discovery_job_crashed")
            await asyncio.to_thread(repo.finish_job, run_id, total, ok, failed, f"Job stopped: {type(exc).__name__}", False)
        finally:
            self._progress.pop(JobType.CREATOR_DISCOVERY, None)
        log_event(logger, logging.INFO, "vt_job_creator_discovery", trigger=trigger, creators=total, new_videos=new, failed=failed)
