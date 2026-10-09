"""In-process daily scheduler for the two Video Performance jobs.

Runs inside the backend (no browser needed). Times are configured in backend/.env:
VIDEO_TRACKING_DISCOVERY_TIME / VIDEO_TRACKING_REFRESH_TIME (HH:MM) in VIDEO_TRACKING_TIMEZONE.

Every minute each job is checked on its own: it is due when it has not completed since its most recent
scheduled time ("slot", e.g. today 06:30). So:
  * a server that was off at the scheduled time catches up as soon as it starts (same day, no gaps);
  * a long-running job never pushes the other job to the next day;
  * a manual "Run now" in the afternoon does not cancel the next morning's run;
  * a failed run is retried after RETRY_AFTER_MINUTES, never in a tight loop.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.config.settings import Settings
from app.utils.logging import log_event
from app.utils.time import utcnow
from app.video_performance import repository as repo
from app.video_performance.timeutil import job_times, next_run_utc, slot_start_utc
from app.video_performance.tracker import VideoTracker

logger = logging.getLogger("creatorintel.video_performance.scheduler")

STARTUP_DELAY_SECONDS = 15
POLL_SECONDS = 60
RETRY_AFTER_MINUTES = 30  # after a failed/interrupted run, wait this long before trying again
ON_TIME_MINUTES = 15  # a start within this many minutes of the scheduled time is "scheduled", later = "catch_up"
CLEANUP_EVERY = timedelta(hours=6)


class VideoTrackingScheduler:
    def __init__(self, tracker: VideoTracker, settings: Settings):
        self.tracker = tracker
        self.settings = settings
        self._task: asyncio.Task | None = None
        self.times = job_times(settings)
        self._last_cleanup: datetime | None = None

    @property
    def enabled(self) -> bool:
        return self.settings.video_tracking_scheduler_enabled

    def next_runs(self) -> dict[str, datetime | None]:
        if not self.enabled:
            return {job: None for job in self.times}
        return {job: next_run_utc(at) for job, at in self.times.items()}

    def start(self) -> None:
        if self.enabled and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="vt-scheduler")
            log_event(
                logger, logging.INFO, "vt_scheduler_started", timezone=self.settings.video_tracking_timezone,
                discovery=self.settings.video_tracking_discovery_time, refresh=self.settings.video_tracking_refresh_time,
            )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def due_trigger(self, job_type: str, now: datetime | None = None) -> str | None:
        """'scheduled' / 'catch_up' when the job should start now, else None."""
        now = now or utcnow()
        if self.tracker.job_running(job_type):
            return None
        slot = slot_start_utc(self.times[job_type], now)
        last_done = await asyncio.to_thread(repo.last_completed_run, job_type)
        if last_done is not None and last_done >= slot:
            return None  # already ran in this slot (e.g. today since 06:30)
        last_started = await asyncio.to_thread(repo.last_started_run, job_type)
        if last_started is not None and now - last_started < timedelta(minutes=RETRY_AFTER_MINUTES):
            return None  # started recently (running elsewhere, or failed) - do not hammer
        return "scheduled" if now - slot < timedelta(minutes=ON_TIME_MINUTES) else "catch_up"

    async def tick(self, now: datetime | None = None) -> list[str]:
        """Start every due job (each independently). Returns the jobs started."""
        started = []
        for job_type in self.times:
            try:
                trigger = await self.due_trigger(job_type, now)
                if trigger and self.tracker.start_job(job_type, trigger):
                    log_event(logger, logging.INFO, "vt_job_due", job=job_type, trigger=trigger)
                    started.append(job_type)
            except Exception:  # a failing check must never kill the scheduler
                logger.exception("vt_scheduler_check_failed job=%s", job_type)
        return started

    async def _cleanup_files(self) -> None:
        """Delete old uploaded video lists (their rows are kept in the database)."""
        try:
            removed = await asyncio.to_thread(
                repo.cleanup_upload_files, self.settings.upload_dir, self.settings.upload_file_retention_days
            )
            if removed:
                log_event(logger, logging.INFO, "housekeeping_files_removed", module="video_performance", files=removed)
        except Exception:
            logger.exception("vt_file_cleanup_crashed")

    async def _loop(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
        while True:
            await self.tick()
            now = datetime.now(timezone.utc)
            if self._last_cleanup is None or now - self._last_cleanup >= CLEANUP_EVERY:
                self._last_cleanup = now
                await self._cleanup_files()
            await asyncio.sleep(POLL_SECONDS)
