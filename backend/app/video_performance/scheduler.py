"""In-process daily scheduler for the two Video Performance jobs.

Runs inside the backend (no browser needed). Times are configured in backend/.env:
VIDEO_TRACKING_DISCOVERY_TIME / VIDEO_TRACKING_REFRESH_TIME (HH:MM) in VIDEO_TRACKING_TIMEZONE.
If the server was down at a scheduled time, the missed job runs once on startup (catch-up).
"""

import asyncio
import logging
from datetime import datetime, time, timedelta, timezone

from app.config.settings import Settings
from app.utils.logging import log_event
from app.video_performance import repository as repo
from app.video_performance.models import JobType
from app.video_performance.timeutil import next_run_utc, parse_hhmm
from app.video_performance.tracker import VideoTracker

logger = logging.getLogger("creatorintel.video_performance.scheduler")

STARTUP_DELAY_SECONDS = 15


class VideoTrackingScheduler:
    def __init__(self, tracker: VideoTracker, settings: Settings):
        self.tracker = tracker
        self.settings = settings
        self._task: asyncio.Task | None = None
        self.times = {
            JobType.CREATOR_DISCOVERY: parse_hhmm(settings.video_tracking_discovery_time, time(6, 0)),
            JobType.METRICS_REFRESH: parse_hhmm(settings.video_tracking_refresh_time, time(6, 30)),
        }

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

    async def _run(self, job_type: str, trigger: str) -> None:
        runner = self.tracker.run_discovery if job_type == JobType.CREATOR_DISCOVERY else self.tracker.run_metrics_refresh
        try:
            await runner(trigger)
        except Exception:  # a failed job must never kill the scheduler
            logger.exception("vt_scheduled_job_crashed job=%s", job_type)

    async def _catch_up(self) -> None:
        now = datetime.now(timezone.utc)
        for job_type in (JobType.CREATOR_DISCOVERY, JobType.METRICS_REFRESH):
            last = await asyncio.to_thread(repo.last_completed_run, job_type)
            if last is None or now - last > timedelta(hours=24):
                log_event(logger, logging.INFO, "vt_catch_up", job=job_type)
                await self._run(job_type, "catch_up")

    async def _loop(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
        await self._catch_up()
        while True:
            upcoming = sorted((next_run_utc(at), job) for job, at in self.times.items())
            due_at, job_type = upcoming[0]
            # Sleep in short steps so clock changes / long sleeps stay accurate.
            while (remaining := (due_at - datetime.now(timezone.utc)).total_seconds()) > 0:
                await asyncio.sleep(min(remaining, 300))
            await self._run(job_type, "scheduled")
            await asyncio.sleep(1)
