"""Daily Video Performance jobs: one run per daily slot, no skipped days (times in Asia/Kolkata)."""

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.config.settings import get_settings
from app.video_performance import scheduler as sched
from app.video_performance.timeutil import slot_start_utc

IST = ZoneInfo("Asia/Kolkata")


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=IST).astimezone(timezone.utc)


class FakeTracker:
    def __init__(self):
        self.running: set[str] = set()
        self.started: list[tuple[str, str]] = []

    def job_running(self, job_type):
        return job_type in self.running

    def start_job(self, job_type, trigger):
        self.started.append((job_type, trigger))
        return True


@pytest.fixture
def history(monkeypatch):
    """{job_type: {"done": [...], "started": [...]}} replacing the job-run table."""
    runs = {"metrics_refresh": {"done": [], "started": []}, "creator_discovery": {"done": [], "started": []}}
    monkeypatch.setattr(sched.repo, "last_completed_run", lambda job: max(runs[job]["done"], default=None))
    monkeypatch.setattr(sched.repo, "last_started_run",
                        lambda job: max(runs[job]["started"] + runs[job]["done"], default=None))
    return runs


def scheduler(tracker):
    cfg = get_settings().model_copy(update={"video_tracking_timezone": "Asia/Kolkata",
                                            "video_tracking_discovery_time": "06:00",
                                            "video_tracking_refresh_time": "06:30"})
    return sched.VideoTrackingScheduler(tracker, cfg)


def test_slot_start():
    from datetime import time
    assert slot_start_utc(time(6, 30), ist(8, 12, 40)) == ist(8, 6, 30)
    assert slot_start_utc(time(6, 30), ist(8, 5, 0)) == ist(7, 6, 30)  # before 06:30 -> yesterday's slot


def test_manual_run_in_the_afternoon_does_not_cancel_next_day(history):
    history["metrics_refresh"]["done"] = [ist(7, 17, 19)]  # manual "Run now" on 7 Oct, 17:19
    history["creator_discovery"]["done"] = [ist(8, 6, 1)]
    s = scheduler(FakeTracker())
    # 8 Oct 12:40: the old rule (24 h since last run) skipped this - the refresh is due for today's slot
    assert asyncio.run(s.due_trigger("metrics_refresh", ist(8, 12, 40))) == "catch_up"
    assert asyncio.run(s.due_trigger("creator_discovery", ist(8, 12, 40))) is None  # already ran today


def test_on_time_run_is_scheduled_and_runs_once_per_day(history):
    tracker = FakeTracker()
    s = scheduler(tracker)
    history["metrics_refresh"]["done"] = [ist(7, 6, 31)]
    assert asyncio.run(s.due_trigger("metrics_refresh", ist(8, 6, 25))) is None  # before today's slot
    assert asyncio.run(s.due_trigger("metrics_refresh", ist(8, 6, 30))) == "scheduled"
    history["metrics_refresh"]["done"].append(ist(8, 6, 31))
    assert asyncio.run(s.due_trigger("metrics_refresh", ist(8, 23, 0))) is None  # done for today


def test_jobs_are_independent_long_discovery_does_not_skip_refresh(history):
    tracker = FakeTracker()
    s = scheduler(tracker)
    history["metrics_refresh"]["done"] = [ist(7, 6, 31)]
    history["creator_discovery"]["started"] = [ist(8, 6, 0)]
    tracker.running.add("creator_discovery")  # discovery still running past 06:30
    started = asyncio.run(s.tick(ist(8, 6, 40)))
    assert started == ["metrics_refresh"] and tracker.started == [("metrics_refresh", "scheduled")]


def test_failed_run_is_retried_later_not_in_a_loop(history):
    s = scheduler(FakeTracker())
    history["metrics_refresh"]["done"] = [ist(7, 6, 31)]
    history["metrics_refresh"]["started"] = [ist(8, 6, 30)]  # today's run failed / was interrupted
    assert asyncio.run(s.due_trigger("metrics_refresh", ist(8, 6, 50))) is None  # within 30 min
    assert asyncio.run(s.due_trigger("metrics_refresh", ist(8, 7, 5))) == "catch_up"


def test_running_job_is_not_started_twice(history):
    tracker = FakeTracker()
    tracker.running.add("metrics_refresh")
    s = scheduler(tracker)
    assert asyncio.run(s.due_trigger("metrics_refresh", ist(8, 7, 0))) is None
