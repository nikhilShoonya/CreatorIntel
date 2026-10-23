"""Durable Apify job reservations and conservative application usage accounting."""

import uuid
from datetime import timezone

from sqlalchemy import delete, func, or_, select

from app.config.settings import Settings
from app.models.db import session_scope
from app.utils.time import as_utc, utcnow
from app.video_performance.models import VtApifyRun, VtApifyWork
from app.video_performance.repository import acquire_job_lock, read_scope, release_job_lock

ACTIVE = ("reserved", "starting", "running", "unknown")


def _data(run):
    return {name: getattr(run, name) for name in (
        "id", "remote_run_id", "actor_id", "inputs", "status", "started_at", "finished_at", "budget_units",
    )}


def reserve(rows: list[dict], settings: Settings, requested_at=None) -> tuple[list[dict], list[dict], str | None]:
    """Serialize reservation creation across processes. Count even uncertain/failed submissions conservatively."""
    owner = uuid.uuid4().hex
    requested_at = requested_at or utcnow()
    if not acquire_job_lock("apify_reservations", owner, hours=1 / 60):
        return [], rows, "Another Apify submission is being reserved; retry shortly"
    try:
        with session_scope() as db:
            existing = []
            for work in db.scalars(select(VtApifyWork).where(VtApifyWork.video_id.in_([r["id"] for r in rows]))):
                if work.finished_at is None or as_utc(work.finished_at) >= as_utc(requested_at):
                    existing.append(work)
                else:
                    db.delete(work)  # explicit refresh requested after completion may start a new run
            db.flush()
            active_ids = {work.video_id for work in existing}
            runs = [_data(run) for run in db.scalars(select(VtApifyRun).where(
                VtApifyRun.id.in_({work.run_id for work in existing}),
            ))]
            remaining = [row for row in rows if row["id"] not in active_ids]
            month = utcnow().astimezone(timezone.utc).strftime("%Y-%m")
            used = db.scalar(select(func.coalesce(func.sum(VtApifyRun.budget_units), 0)).where(
                or_(VtApifyRun.month == month, VtApifyRun.status.in_(ACTIVE)),
            ))
            capacity = max(0, settings.apify_instagram_reel_monthly_result_limit - used)
            allowed, refused = remaining[:capacity], remaining[capacity:]
            for offset in range(0, len(allowed), settings.apify_instagram_reel_batch_size):
                chunk = allowed[offset:offset + settings.apify_instagram_reel_batch_size]
                run = VtApifyRun(actor_id=settings.apify_instagram_reel_actor_id, month=month,
                                 inputs=[{"id": r["id"], "identifier": r["identifier"]} for r in chunk],
                                 budget_units=len(chunk), status="reserved")
                db.add(run)
                db.flush()
                db.add_all(VtApifyWork(video_id=r["id"], run_id=run.id) for r in chunk)
                runs.append({**_data(run), "new": True})
            return runs, refused, ("Apify application monthly result limit reached; increase the limit or retry next month"
                                   if refused else None)
    finally:
        release_job_lock("apify_reservations", owner)


def update_run(local_id: str, **values) -> None:
    allowed = {"remote_run_id", "status", "finished_at", "returned_items", "charged_events", "reported_cost_usd", "message",
               "budget_units"}
    with session_scope() as db:
        run = db.get(VtApifyRun, local_id)
        if run:
            for name, value in values.items():
                if name in allowed:
                    setattr(run, name, value)
            if values.get("returned_items") is not None:
                run.budget_units = max(run.budget_units, values["returned_items"])


def acknowledge(video_id: int, local_id: str) -> None:
    """Release duplicate suppression only after the tracked record was saved, or a terminal failure recorded."""
    with session_scope() as db:
        run = db.get(VtApifyRun, local_id)
        if run and run.status not in ACTIVE:
            work = db.get(VtApifyWork, video_id)
            if work and work.run_id == local_id and work.finished_at is None:
                work.finished_at = utcnow()


def reconcile(local_id: str, remote_run_id: str | None) -> bool:
    """Operator recovery for the unavoidable crash/timeout between remote POST and saving its ID.

    Attaching resumes the existing remote run. Releasing requires the caller to confirm Console was checked;
    it never refunds the conservative usage reservation and never starts a run itself.
    """
    with session_scope() as db:
        run = db.get(VtApifyRun, local_id)
        if not run or run.status not in ("reserved", "starting", "unknown") or run.remote_run_id:
            return False
        if remote_run_id:
            run.remote_run_id, run.status = remote_run_id, "running"
        else:
            run.status, run.finished_at = "released", utcnow()
            run.message = "Operator verified remote state and released uncertain submission; usage retained"
            db.execute(delete(VtApifyWork).where(VtApifyWork.run_id == local_id))
        return True


def usage(settings: Settings) -> dict:
    month = utcnow().astimezone(timezone.utc).strftime("%Y-%m")
    with read_scope() as db:
        runs = list(db.scalars(select(VtApifyRun).where(or_(VtApifyRun.month == month, VtApifyRun.status.in_(ACTIVE)))))
        used = sum(run.budget_units for run in runs)
        return {
            "enabled": settings.apify_instagram_reel_enabled,
            "configured": settings.apify_reels_configured,
            "actor_id": settings.apify_instagram_reel_actor_id,
            "month": month,
            "limit": settings.apify_instagram_reel_monthly_result_limit,
            "used_units": used,
            "remaining": max(0, settings.apify_instagram_reel_monthly_result_limit - used),
            "reserved_units": sum(run.budget_units for run in runs if run.status in ACTIVE),
            "returned_items": sum(run.returned_items or 0 for run in runs),
            "active_runs": sum(run.status in ACTIVE for run in runs),
            "recent_runs": [{"id": run.id, "remote_run_id": run.remote_run_id, "status": run.status,
                             "started_at": run.started_at, "finished_at": run.finished_at, "message": run.message}
                            for run in sorted(runs, key=lambda r: r.started_at, reverse=True)[:10]],
            "note": "Application result-unit estimate, not Apify billing. Failed/uncertain submissions retain reservations; "
                    "other Actors, paid extras and other clients also consume account credits.",
        }
