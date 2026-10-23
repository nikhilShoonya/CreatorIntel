"""Apify's official async SDK, used only for direct Reel metrics in Video Performance."""

import asyncio
import math
import re
import uuid
from datetime import datetime, timezone

from apify_client import ApifyClientAsync

from app.config.settings import Settings
from app.utils.time import utcnow
from app.video_performance import apify_repository as jobs
from app.video_performance.platforms import PlatformError, VideoMetrics
from app.video_performance.repository import acquire_job_lock, release_job_lock
from app.video_performance.urls import parse_instagram_handle, parse_video_link

SOURCE = "apify_instagram_reel"
BUSY = "apify_busy"
ERROR = "apify_error"  # deliberately outside Meta's automatic retry queue
NOT_FOUND = "apify_not_found"  # Apify: "Post does not exist" (deleted, or a wrong link)
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
COUNT_FIELDS = ("videoPlayCount", "igPlayCount", "videoViewCount", "likesCount", "commentsCount", "sharesCount")
MIN_RUN_CHARGE_USD = 0.01  # Apify refuses runs whose cost cap is below its minimum ($0.0073 for this Actor)
START_CHARGE_USD = 0.001  # the Actor's per-run "actor-start" event
SHARES_ADDON_CAP_USD = 0.01  # per-Reel headroom for the paid shares-count add-on when enabled
_ERROR_TYPE = re.compile(r"[a-z0-9-]{1,80}")


def run_charge_cap(reels: int, settings: Settings) -> float:
    """Cost ceiling for one run: enough for every Reel in it, never below Apify's minimum."""
    per_reel = settings.apify_max_charge_per_reel_usd + (SHARES_ADDON_CAP_USD if settings.apify_include_shares else 0)
    return round(max(MIN_RUN_CHARGE_USD, reels * per_reel + START_CHARGE_USD), 4)


def _error_type(exc: Exception) -> str | None:
    """Apify's error type slug (e.g. max-total-charge-usd-below-minimum) - no free text, so nothing secret."""
    value = getattr(exc, "type", None)
    return value if isinstance(value, str) and _ERROR_TYPE.fullmatch(value) else None


def count(value):
    """No booleans, fractions, negative/overflow values, NaN, or fabricated zeroes."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, str):
        if len(value) > 19 or not value.isascii() or not value.isdecimal():
            return None
        value = int(value)
    if not isinstance(value, (int, float)) or not 0 <= value <= 2**63 - 1 or not math.isfinite(value) or int(value) != value:
        return None
    return int(value)


def result_code(item: dict) -> str | None:
    """Every supplied identity must agree, including inputUrl. Never fall back to result order."""
    codes = set()
    for field in ("shortCode", "url", "inputUrl"):
        value = item.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return None
        parsed = parse_video_link(value, instagram_reel_hint=field == "shortCode")
        if parsed.platform != "instagram" or not parsed.identifier:
            return None
        codes.add(parsed.identifier)
    return next(iter(codes)) if len(codes) == 1 else None


def metrics(item: dict, code: str, local_id: str, include_shares: bool) -> VideoMetrics | PlatformError:
    if item.get("error") == "not_found":
        return PlatformError(NOT_FOUND, "Apify reports this Reel does not exist on Instagram (deleted, or the link is wrong)")
    if item.get("error") or item.get("errorDescription"):
        # Do not copy arbitrary provider error text (possibly containing secrets/URLs) into the app.
        return PlatformError(ERROR, "Apify could not retrieve this Reel; it may be private, deleted or restricted. Retry later")
    if item.get("type") not in (None, "Video") or item.get("productType") not in (None, "clips"):
        return PlatformError(ERROR, "Apify returned a non-Reel item; stored metrics retained")
    values = {name: count(item.get(name)) for name in COUNT_FIELDS if name != "sharesCount" or include_shares}
    metric = next((name for name in ("videoPlayCount", "igPlayCount", "videoViewCount") if values[name] is not None), None)
    if not any(values.get(name) is not None for name in COUNT_FIELDS):
        return PlatformError(ERROR, "Apify returned no usable metric counts for this Reel; stored metrics retained")
    owner = parse_instagram_handle(item.get("ownerUsername"))
    timestamp = None
    if isinstance(item.get("timestamp"), str):
        try:
            timestamp = datetime.fromisoformat(item["timestamp"].replace("Z", "+00:00"))
            timestamp = timestamp.replace(tzinfo=timezone.utc) if timestamp.tzinfo is None else timestamp
        except ValueError:
            pass
    raw = {**values, "ownerUsername": owner}
    duration = item.get("videoDuration")
    if isinstance(duration, (float, int)) and not isinstance(duration, bool) and math.isfinite(duration) and duration >= 0:
        raw["videoDuration"] = duration
    notes = ["Public Reel metrics from Apify"]
    if metric and metric != "videoPlayCount":
        notes.append(f"Current Views uses {metric}; videoPlayCount was unavailable")
    if values.get("likesCount") is None or values.get("commentsCount") is None:
        notes.append("Some engagement counts are unavailable")
    return VideoMetrics(
        identifier=code, views=values[metric] if metric else None,
        likes=values.get("likesCount"), comments=values.get("commentsCount"),
        caption=item.get("caption") if isinstance(item.get("caption"), str) else None,
        published_at=timestamp, owner_username=owner, notes=notes,
        metrics_source=SOURCE, view_metric=metric, source_metrics=raw, source_run_id=local_id,
    )


class ApifyReelClient:
    def __init__(self, settings: Settings, client=None):
        self.settings = settings
        self._client = client

    @property
    def client(self):
        if self._client is None:
            # Never retry a billable POST automatically: a lost response may still have created a run.
            self._client = ApifyClientAsync(self.settings.apify_api_token.strip(), max_retries=0, timeout_secs=35)
        return self._client

    async def videos(self, rows: list[dict], *, requested_at=None) -> tuple[dict[int, VideoMetrics | PlatformError], dict[int, str]]:
        if not self.settings.apify_reels_configured:
            return ({r["id"]: PlatformError(ERROR, "Apify Reel integration is disabled or APIFY_API_TOKEN is missing") for r in rows}, {})
        runs, refused, reason = await asyncio.to_thread(jobs.reserve, rows, self.settings, requested_at)
        outcomes = {r["id"]: PlatformError(ERROR, reason) for r in refused}
        associations = {}
        for run in runs:
            associations.update({r["id"]: run["id"] for r in run["inputs"]})
            outcomes.update(await self._run(run))
        return outcomes, associations

    async def _run(self, run: dict) -> dict:
        def errors(message, code=ERROR):
            return {r["id"]: PlatformError(code, message) for r in run["inputs"]}

        owner = uuid.uuid4().hex
        lock = "apify:" + run["id"]
        if not await asyncio.to_thread(acquire_job_lock, lock, owner, 0.25):
            return errors("This Apify batch is already being processed; no duplicate Actor run was started", BUSY)
        remote_id = run["remote_run_id"]
        try:
            if not remote_id and not run.get("new"):
                return errors(f"Apify submission outcome is uncertain (job {run['id']}). An operator must reconcile it before another run")
            async with asyncio.timeout(660):
                if not remote_id:
                    await asyncio.to_thread(jobs.update_run, run["id"], status="starting")
                    remote = await self.client.actor(run["actor_id"]).start(
                        run_input={
                            "username": [f"https://www.instagram.com/reel/{r['identifier']}/" for r in run["inputs"]],
                            "includeSharesCount": self.settings.apify_include_shares,
                            "includeTranscript": False,
                            "includeDownloadedVideo": False,
                        }, timeout_secs=600, max_items=len(run["inputs"]),
                        # explicit: a cap derived from max_items alone falls below Apify's minimum for 1-2 Reels
                        max_total_charge_usd=run_charge_cap(len(run["inputs"]), self.settings),
                    )
                    remote_id = remote.get("id") if isinstance(remote, dict) else None
                    if not isinstance(remote_id, str) or not remote_id:
                        raise ValueError("Missing run ID")
                    await asyncio.to_thread(jobs.update_run, run["id"], remote_run_id=remote_id, status="running")
                else:
                    remote = await self.client.run(remote_id).get()
                while isinstance(remote, dict) and remote.get("status") not in TERMINAL:
                    await asyncio.sleep(3)
                    remote = await self.client.run(remote_id).get()
                if not isinstance(remote, dict):
                    raise ValueError("Missing run")
                dataset_id = remote.get("defaultDatasetId")
                items = []
                if dataset_id:
                    # Fetch all items in bounded pages, including unsuccessful-run partial datasets.
                    offset = 0
                    while True:
                        page = await self.client.dataset(dataset_id).list_items(offset=offset, limit=100, clean=True)
                        if not isinstance(page.items, list):
                            raise ValueError("Invalid dataset")
                        items.extend(page.items)
                        offset += len(page.items)
                        if not page.items and offset < page.total:
                            raise ValueError("Incomplete dataset page")
                        if offset >= page.total:
                            break
                        if offset >= max(1000, len(run["inputs"]) * 10):
                            raise ValueError("Unexpectedly large dataset")
                else:
                    await asyncio.to_thread(jobs.update_run, run["id"], status="failed", finished_at=utcnow(),
                                            message="Actor returned no dataset")
                    return errors("Apify Actor returned no dataset; no Reel metrics were retrieved. Retry later")

                events = remote.get("chargedEventCounts")
                safe_events = {str(k)[:100]: n for k, v in events.items() if (n := count(v)) is not None} if isinstance(events, dict) else None
                cost = remote.get("usageTotalUsd")
                cost = float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) and math.isfinite(cost) and cost >= 0 else None
                await asyncio.to_thread(jobs.update_run, run["id"], status="completed" if remote["status"] == "SUCCEEDED" else "failed",
                                        finished_at=utcnow(), returned_items=len(items), charged_events=safe_events,
                                        reported_cost_usd=cost, message=f"Actor {remote['status']}; {len(items)} dataset items")
                wanted = {r["identifier"]: r["id"] for r in run["inputs"]}
                found = {}
                duplicates = set()
                for item in items:
                    code = result_code(item) if isinstance(item, dict) else None
                    if code not in wanted:
                        continue
                    if code in found:
                        duplicates.add(code)
                    found[code] = item
                results = {}
                for code, video_id in wanted.items():
                    if code in duplicates:
                        results[video_id] = PlatformError(ERROR, "Apify returned duplicate/conflicting items for this shortcode; stored metrics retained")
                    elif code in found:
                        results[video_id] = metrics(found[code], code, run["id"], self.settings.apify_include_shares)
                    else:
                        results[video_id] = PlatformError(ERROR, f"Apify {remote['status']}: no exact matching result for this Reel; retry later")
                return results
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(jobs.update_run, run["id"],
                                                  status="running" if remote_id else "unknown",
                                                  **({"remote_run_id": remote_id} if remote_id else {}),
                                                  message="Worker interrupted; resume existing run, never resubmit automatically"))
            raise
        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            # Apify answered with a client error before any run ID existed: no run was created (never uncertain).
            definitive_rejection = not remote_id and isinstance(status_code, int) and 400 <= status_code < 500
            status = "failed" if definitive_rejection else "running" if remote_id else "unknown"
            kind = _error_type(exc)
            if status_code in (401, 403):
                message = "Apify authentication/permission failed; check the backend APIFY_API_TOKEN and Actor access"
            elif status_code == 402:
                message = "Apify account has no usage credits left for this Actor; add credits or wait for the next billing cycle"
            elif status_code == 429:
                message = "Apify is temporarily throttling requests; no immediate automatic retry. Retry later"
            elif definitive_rejection:
                message = f"Apify rejected the Actor request (HTTP {status_code}{', ' + kind if kind else ''})"
            else:
                message = (f"Apify timed out or returned an invalid response (job {run['id']}); "
                           + ("Retry resumes the existing run" if remote_id else "submission outcome is uncertain; operator reconciliation required"))
            # Exception messages/tracebacks can contain credentials: store only controlled text.
            # A run Apify refused was never created or billed: it does not count against the monthly limit.
            await asyncio.to_thread(jobs.update_run, run["id"], status=status, message=message,
                                    **({"remote_run_id": remote_id} if remote_id else {}),
                                    **({"finished_at": utcnow(), "budget_units": 0} if definitive_rejection else {}))
            return errors(message)
        finally:
            await asyncio.to_thread(release_job_lock, lock, owner)
