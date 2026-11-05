"""Durable ledger of Facebook Apify runs and the app's own monthly result limit.

Separate from Instagram's ledger on purpose: that one totals every run in its table against Instagram's limit.
"""

import uuid
from datetime import timezone

from sqlalchemy import func, or_, select

from app.config.settings import Settings
from app.models.db import session_scope
from app.utils.time import utcnow
from app.video_performance.models import VtFacebookApifyRun
from app.video_performance.repository import acquire_job_lock, read_scope, release_job_lock

ACTIVE = ("reserved", "starting", "running")
_LOCK = "apify_fb_reservations"


def _data(run: VtFacebookApifyRun) -> dict:
    return {name: getattr(run, name) for name in ("id", "remote_run_id", "actor_id", "inputs", "status")}


def reserve(rows: list[dict], settings: Settings) -> tuple[list[dict], list[dict], str | None]:
    """(runs to follow, rows refused, why). A video already in an unfinished run rejoins it: never a second paid run."""
    owner = uuid.uuid4().hex
    if not acquire_job_lock(_LOCK, owner, hours=1 / 60):
        return [], rows, "Another Facebook Apify submission is being prepared; this check will run again later"
    try:
        with session_scope() as db:
            wanted = {row["id"] for row in rows}
            runs, joined = [], set()
            for run in db.scalars(select(VtFacebookApifyRun).where(VtFacebookApifyRun.status.in_(ACTIVE))):
                ids = {item["id"] for item in run.inputs}
                if ids & wanted:
                    runs.append(_data(run))
                    joined |= ids
            remaining = [row for row in rows if row["id"] not in joined]
            month = utcnow().astimezone(timezone.utc).strftime("%Y-%m")
            used = db.scalar(select(func.coalesce(func.sum(VtFacebookApifyRun.budget_units), 0)).where(
                or_(VtFacebookApifyRun.month == month, VtFacebookApifyRun.status.in_(ACTIVE)),
            ))
            capacity = max(0, settings.apify_facebook_monthly_result_limit - used)
            allowed, refused = remaining[:capacity], remaining[capacity:]
            for start in range(0, len(allowed), settings.apify_facebook_batch_size):
                chunk = allowed[start:start + settings.apify_facebook_batch_size]
                run = VtFacebookApifyRun(
                    actor_id=settings.apify_facebook_actor_id, month=month, status="reserved", budget_units=len(chunk),
                    inputs=[{"id": r["id"], "identifier": r["identifier"], "url": r["video_url"], "seen": bool(r.get("seen"))} for r in chunk],
                )
                db.add(run)
                db.flush()
                runs.append({**_data(run), "new": True})
            reason = "Facebook Apify monthly result limit reached; increase APIFY_FACEBOOK_MONTHLY_RESULT_LIMIT or wait for next month"
            return runs, refused, reason if refused else None
    finally:
        release_job_lock(_LOCK, owner)


def update_run(local_id: str, **values) -> None:
    allowed = {"remote_run_id", "status", "finished_at", "returned_items", "summary", "charged_events", "reported_cost_usd",
               "message", "budget_units"}
    with session_scope() as db:
        run = db.get(VtFacebookApifyRun, local_id)
        if run:
            for name, value in values.items():
                if name in allowed:
                    setattr(run, name, value)
            if values.get("returned_items") is not None:
                run.budget_units = max(run.budget_units, values["returned_items"])


def usage(settings: Settings) -> dict:
    month = utcnow().astimezone(timezone.utc).strftime("%Y-%m")
    with read_scope() as db:
        runs = list(db.scalars(select(VtFacebookApifyRun).where(
            or_(VtFacebookApifyRun.month == month, VtFacebookApifyRun.status.in_(ACTIVE)),
        )))
    used = sum(run.budget_units for run in runs)
    limit = settings.apify_facebook_monthly_result_limit
    return {
        "enabled": settings.apify_facebook_enabled,
        "configured": settings.facebook_configured,
        "actor_id": settings.apify_facebook_actor_id,
        "month": month,
        "limit": limit,
        "used_units": used,
        "remaining": max(0, limit - used),
        "reserved_units": sum(run.budget_units for run in runs if run.status in ACTIVE),
        "returned_items": sum(run.returned_items or 0 for run in runs),
        "active_runs": sum(run.status in ACTIVE for run in runs),
        "recent_runs": [{"id": run.id, "remote_run_id": run.remote_run_id, "status": run.status,
                         "started_at": run.started_at, "finished_at": run.finished_at, "message": run.message}
                        for run in sorted(runs, key=lambda r: r.started_at, reverse=True)[:10]],
        "note": "Application result-unit estimate, not Apify billing. Each row the Actor writes is charged, including "
                "rows for links with no public metrics. A start with an uncertain outcome keeps its reservation.",
    }
