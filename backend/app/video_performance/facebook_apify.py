"""Facebook video metrics from the Apify Actor chronometrica/facebook-metrics-scraper (public data, any Page).

One Actor run per batch of links: started, followed by its saved run ID, read from its dataset and OUTPUT record.
Results are matched to videos by the returned inputUrl, never by position. The token only ever travels in the
Authorization header, and only controlled text reaches the database and logs.
"""

import asyncio
import logging
import random
import uuid
from urllib.parse import urlsplit

import httpx

from app.config.settings import Settings, get_settings
from app.services.http_client import HttpRequestError, request_json
from app.utils.logging import log_event, redact
from app.utils.time import utcnow
from app.video_performance import facebook_apify_repository as jobs
from app.video_performance.apify_reels import BUSY, count
from app.video_performance.platforms import FACEBOOK_METRICS_SOURCE, PlatformError, VideoMetrics, _dt
from app.video_performance.repository import acquire_job_lock, release_job_lock
from app.video_performance.schemas import EXACT_VIEW_PRECISIONS
from app.video_performance.urls import parse_video_link

logger = logging.getLogger("creatorintel.video_performance.facebook_apify")

API = "https://api.apify.com/v2"
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED-OUT", "ABORTED"}
START_ATTEMPTS = 3
PAGE_SIZE = 100
MIN_RUN_CHARGE_USD = 0.02
NO_PUBLIC_METRICS = "Facebook shows no public metrics for this link (it may be private, deleted, login-restricted or region-limited)"
SUMMARY_FIELDS = ("providedUrls", "resolvedUrls", "datasetRows", "metricRows", "noPublicMetricRows", "failedRows", "invalidRows",
                  "skippedNoPublicMetricRows", "skippedFailedRows", "skippedInvalidRows", "statusCounts")


def actor_path(actor_id: str) -> str:
    return actor_id.replace("/", "~")


def run_charge_cap(videos: int, settings: Settings) -> float:
    """Spending ceiling for one run, not its price: Apify bills only the rows actually written."""
    return round(max(MIN_RUN_CHARGE_USD, videos * settings.apify_facebook_max_charge_per_result_usd), 4)


def _normal_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
    except ValueError:
        return None
    return f"{(parts.hostname or '').lower().removeprefix('www.')}{parts.path.rstrip('/')}?{parts.query}"


def _video_key(value: object) -> str | None:
    """The Facebook video ID in a link, however the link is written."""
    parsed = parse_video_link(value) if isinstance(value, str) else None
    return parsed.identifier if parsed and parsed.ok and parsed.platform == "facebook" else None


def match_rows(inputs: list[dict], items: list) -> dict[int, list[dict]]:
    """Dataset rows per video, by inputUrl only: the exact normalized link first, then the video ID inside it."""
    by_url = {_normal_url(item["url"]): item["id"] for item in inputs}
    by_key = {item["identifier"]: item["id"] for item in inputs}
    matched: dict[int, list[dict]] = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        video_id = by_url.get(_normal_url(row.get("inputUrl")))
        if video_id is None:
            video_id = by_key.get(_video_key(row.get("inputUrl")))
        if video_id is not None:
            matched.setdefault(video_id, []).append(row)
    return matched


def _metric_status(entries: object) -> dict[str, dict]:
    status = {}
    for entry in entries if isinstance(entries, list) else []:
        if isinstance(entry, dict) and isinstance(entry.get("metric"), str):
            status[entry["metric"][:40]] = {key: entry.get(key) for key in ("status", "precision", "source")}
    return status


def _reason(row: dict) -> str | None:
    value = row.get("statusReason")
    return redact(value.strip())[:300] if isinstance(value, str) and value.strip() else None


def _no_public_metrics(video: dict, reason: str | None) -> PlatformError:
    detail = f"{NO_PUBLIC_METRICS}. Apify: {reason}" if reason else NO_PUBLIC_METRICS
    # The Actor cannot tell deleted from temporarily restricted, so a video read before keeps its numbers and a note.
    return PlatformError(PlatformError.API if video.get("seen") else PlatformError.NOT_FOUND, detail)


def row_outcome(row: dict, video: dict, local_id: str) -> VideoMetrics | PlatformError:
    status = str(row.get("status") or "").lower()
    if "no_public_metrics" in status:
        return _no_public_metrics(video, _reason(row))
    if "invalid" in status:
        return PlatformError(PlatformError.UNSUPPORTED, f"Apify rejected this Facebook link: {_reason(row) or 'invalid URL'}")
    if status not in ("ok", ""):
        return PlatformError(PlatformError.API, f"Apify could not read this Facebook video: {_reason(row) or status[:60]}")
    content_type = row["contentType"].lower()[:20] if isinstance(row.get("contentType"), str) else None
    if content_type == "photo":
        return PlatformError(PlatformError.UNSUPPORTED, "This Facebook link is a photo, not a video")

    views, plays, likes, comments = (count(row.get(name)) for name in ("views", "plays", "likes", "comments"))
    reactions, shares = count(row.get("reactions")), count(row.get("shares"))
    if all(value is None for value in (views, plays, reactions, likes, comments, shares)):
        return _no_public_metrics(video, _reason(row))

    content_status = _metric_status(row.get("contentMetricStatus"))
    # Current Views is the view counter; plays only stand in when Facebook shows no views. They are never added up.
    view_metric = "views" if views is not None else "plays" if plays is not None else None
    precision = (content_status.get(view_metric) or {}).get("precision") if view_metric else None
    notes = ["Public Facebook metrics from Apify"]
    if view_metric == "plays":
        notes.append("Current Views uses plays; Facebook showed no view count")
    if precision and precision not in EXACT_VIEW_PRECISIONS:
        notes.append("Current Views is a rounded public figure, not an exact count")
    if likes is None or comments is None:
        notes.append("Some engagement counts are unavailable")
    username, name, profile_id = row.get("profileUsername"), row.get("profileName"), row.get("profileId")
    description = row.get("description")
    return VideoMetrics(
        identifier=video["identifier"], views=views if views is not None else plays, likes=likes, comments=comments,
        caption=description if isinstance(description, str) else None, published_at=_dt(row.get("publishedAt")),
        channel_id=profile_id[:100] if isinstance(profile_id, str) else None,
        channel_title=name[:300] if isinstance(name, str) else None,
        owner_username=username[:100] if isinstance(username, str) else None,
        notes=notes, metrics_source=FACEBOOK_METRICS_SOURCE, view_metric=view_metric, source_run_id=local_id,
        source_metrics={key: row.get(key) for key in ("contentId", "contentUrl", "scrapedAt")},
        plays=plays, reactions=reactions, shares=shares, content_type=content_type,
        profile_followers=count(row.get("profileFollowers")), profile_page_likes=count(row.get("profilePageLikes")),
        views_precision=precision,
        metric_status={"content": content_status, "profile": _metric_status(row.get("profileMetricStatus"))},
    )


def _best_outcome(rows: list[dict], video: dict, local_id: str) -> VideoMetrics | PlatformError:
    outcomes = [row_outcome(row, video, local_id) for row in rows]
    good = [outcome for outcome in outcomes if isinstance(outcome, VideoMetrics)]
    if len({(m.views, m.plays, m.likes, m.comments) for m in good}) > 1:
        return PlatformError(PlatformError.API, "Apify returned conflicting rows for this Facebook link; stored metrics retained")
    return good[0] if good else outcomes[0]


def missing_row_error(missing: int, summary: dict | None, run_status: str, video: dict) -> PlatformError:
    """No dataset row for this link: the OUTPUT counts can only name the cause when they account for every missing link."""
    if run_status != "SUCCEEDED":
        return PlatformError(PlatformError.API, f"Apify run {run_status} before reading this Facebook video; it will be checked again")
    summary = summary or {}

    def explains(*fields: str) -> bool:
        return any(count(summary.get(field)) == missing for field in fields)

    if explains("skippedNoPublicMetricRows", "noPublicMetricRows"):
        return _no_public_metrics(video, None)
    if explains("skippedInvalidRows", "invalidRows"):
        return PlatformError(PlatformError.UNSUPPORTED, "Apify rejected this Facebook link as invalid")
    if explains("skippedFailedRows", "failedRows"):
        return PlatformError(PlatformError.API, "Apify could not read this Facebook video; it will be checked again")
    return PlatformError(PlatformError.API, "Apify returned no result for this Facebook link; it will be checked again")


class _StartFailed(Exception):
    def __init__(self, message: str, *, billed_possible: bool):
        super().__init__(message)
        self.message = message
        self.billed_possible = billed_possible


class FacebookApifyClient:
    def __init__(self, settings: Settings | None = None, client: httpx.AsyncClient | None = None):
        self.settings = settings or get_settings()
        self.client = client

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.settings.apify_api_token.strip()}"}

    async def _call(self, method: str, url: str, *, max_retries: int | None = None, **kwargs):
        return await request_json(
            method, url, service="Apify", headers=self._headers, client=self.client, timeout=35, max_retries=max_retries, **kwargs,
        )

    async def videos(self, rows: list[dict]) -> dict[int, VideoMetrics | PlatformError]:
        if not self.settings.facebook_configured:
            message = "Facebook Apify integration is disabled or APIFY_API_TOKEN is missing"
            return {row["id"]: PlatformError(PlatformError.API, message) for row in rows}
        runs, refused, reason = await asyncio.to_thread(jobs.reserve, rows, self.settings)
        outcomes: dict[int, VideoMetrics | PlatformError] = {row["id"]: PlatformError(PlatformError.API, reason) for row in refused}
        for run in runs:
            outcomes.update(await self._run(run))
        return outcomes

    async def _start(self, run: dict) -> dict:
        """Start the Actor. Only a 429 is retried: it proves no run exists. A lost answer may have started a paid run."""
        await asyncio.to_thread(jobs.update_run, run["id"], status="starting")
        s = self.settings
        run_input = {
            "mode": "knownUrls", "startUrls": [item["url"] for item in run["inputs"]],
            "includePosts": False, "includeReels": True, "includeVideos": True, "includePhotos": False,
            "includeAuthorProfileMetrics": s.apify_facebook_include_author_profile_metrics,
            "includeFailedRows": s.apify_facebook_include_failed_rows,
            "maxConcurrency": s.apify_facebook_max_concurrency, "requestDelayMillis": s.apify_facebook_request_delay_ms,
            "maxFetchRetries": s.apify_facebook_max_fetch_retries, "requestTimeoutSecs": s.apify_facebook_request_timeout_secs,
        }
        params = {"timeout": s.apify_facebook_run_timeout_secs, "maxTotalChargeUsd": run_charge_cap(len(run["inputs"]), s)}
        answer = None
        for attempt in range(START_ATTEMPTS):
            try:
                answer = await self._call("POST", f"{API}/acts/{actor_path(run['actor_id'])}/runs", params=params, json=run_input, max_retries=0)
                break
            except HttpRequestError as exc:
                code = exc.status_code
                if code == 429 and attempt + 1 < START_ATTEMPTS:
                    await asyncio.sleep(min(2 ** attempt + random.uniform(0, 0.5), 30))
                    continue
                if code in (401, 403):
                    raise _StartFailed("Apify rejected the credentials; check APIFY_API_TOKEN and access to the Facebook Actor", billed_possible=False) from exc
                if code == 402:
                    raise _StartFailed("The Apify account has no usage credits left for the Facebook Actor", billed_possible=False) from exc
                if code == 404:
                    raise _StartFailed("The Facebook Apify Actor is unavailable; check APIFY_FACEBOOK_ACTOR_ID", billed_possible=False) from exc
                if code == 429:
                    raise _StartFailed("Apify is rate limiting requests; this check will run again later", billed_possible=False) from exc
                if code is not None and 400 <= code < 500:
                    raise _StartFailed(f"Apify refused the Facebook Actor request (HTTP {code})", billed_possible=False) from exc
                raise _StartFailed("Apify did not confirm the Actor start; this check will run again later", billed_possible=True) from exc
        remote = answer.get("data") if isinstance(answer, dict) else None
        remote_id = remote.get("id") if isinstance(remote, dict) else None
        if not isinstance(remote_id, str) or not remote_id:
            raise _StartFailed("Apify did not confirm the Actor start; this check will run again later", billed_possible=True)
        await asyncio.to_thread(jobs.update_run, run["id"], remote_run_id=remote_id, status="running")
        return remote

    async def _get_run(self, remote_id: str) -> dict:
        answer = await self._call("GET", f"{API}/actor-runs/{remote_id}")
        data = answer.get("data") if isinstance(answer, dict) else None
        if not isinstance(data, dict):
            raise HttpRequestError("Apify returned an invalid run")
        return data

    async def _wait(self, remote: dict) -> dict:
        """Poll with growing, jittered delays. A failed status read is not a failed run: keep following it."""
        base = self.settings.apify_facebook_poll_seconds
        delay = base
        while remote.get("status") not in TERMINAL:
            await asyncio.sleep(delay + random.uniform(0, delay / 4))
            delay = min(delay * 1.5, max(base, 20))
            try:
                remote = await self._get_run(remote["id"])
            except HttpRequestError as exc:
                if exc.status_code is not None and exc.status_code not in (408, 429) and exc.status_code < 500:
                    raise
        return remote

    async def _dataset(self, dataset_id: str, expected: int) -> list:
        items: list = []
        while True:
            page = await self._call("GET", f"{API}/datasets/{dataset_id}/items",
                                    params={"clean": "true", "format": "json", "offset": len(items), "limit": PAGE_SIZE})
            if not isinstance(page, list):
                raise HttpRequestError("Apify returned an invalid dataset page")
            items.extend(page)
            if len(page) < PAGE_SIZE:
                return items
            if len(items) >= max(1000, expected * 10):
                raise HttpRequestError("Apify returned an unexpectedly large dataset")

    async def _summary(self, store_id: str | None) -> dict | None:
        """The OUTPUT record explains missing rows; without it the rows still count."""
        if not store_id:
            return None
        try:
            answer = await self._call("GET", f"{API}/key-value-stores/{store_id}/records/OUTPUT")
        except HttpRequestError:
            return None
        return {key: answer.get(key) for key in SUMMARY_FIELDS} if isinstance(answer, dict) else None

    async def _finish(self, run: dict, remote: dict, items: list, summary: dict | None) -> dict[int, VideoMetrics | PlatformError]:
        status = remote["status"]
        events = remote.get("chargedEventCounts")
        cost = remote.get("usageTotalUsd")
        await asyncio.to_thread(
            jobs.update_run, run["id"], status="completed" if status == "SUCCEEDED" else "failed", finished_at=utcnow(),
            returned_items=len(items), summary=summary, message=f"Actor {status}; {len(items)} dataset rows",
            charged_events={str(k)[:100]: n for k, v in events.items() if (n := count(v)) is not None} if isinstance(events, dict) else None,
            reported_cost_usd=float(cost) if isinstance(cost, (int, float)) and not isinstance(cost, bool) else None,
        )
        inputs = run["inputs"]
        matched = match_rows(inputs, items)
        missing = sum(item["id"] not in matched for item in inputs)
        return {
            item["id"]: _best_outcome(matched[item["id"]], item, run["id"]) if item["id"] in matched
            else missing_row_error(missing, summary, status, item)
            for item in inputs
        }

    async def _run(self, run: dict) -> dict[int, VideoMetrics | PlatformError]:
        def errors(message: str, code: str = PlatformError.API) -> dict[int, PlatformError]:
            return {item["id"]: PlatformError(code, message) for item in run["inputs"]}

        owner = uuid.uuid4().hex
        lock = f"fbap:{run['id']}"
        if not await asyncio.to_thread(acquire_job_lock, lock, owner, 0.25):
            return errors("This Facebook batch is already being followed; no duplicate Actor run was started", BUSY)
        remote_id = run.get("remote_run_id")
        try:
            if not remote_id and not run.get("new"):
                message = "A previous start was interrupted before Apify confirmed it; this check will run again later"
                await asyncio.to_thread(jobs.update_run, run["id"], status="uncertain", finished_at=utcnow(), message=message)
                return errors(message)
            async with asyncio.timeout(self.settings.apify_facebook_run_timeout_secs + 60):
                remote = await self._get_run(remote_id) if remote_id else await self._start(run)
                remote_id = remote["id"]
                remote = await self._wait(remote)
                dataset_id = remote.get("defaultDatasetId")
                items = await self._dataset(dataset_id, len(run["inputs"])) if dataset_id else []
                summary = await self._summary(remote.get("defaultKeyValueStoreId"))
            return await self._finish(run, remote, items, summary)
        except asyncio.CancelledError:
            await asyncio.shield(asyncio.to_thread(jobs.update_run, run["id"], status="running" if remote_id else "uncertain"))
            raise
        except _StartFailed as exc:
            values = {} if exc.billed_possible else {"budget_units": 0}
            await asyncio.to_thread(jobs.update_run, run["id"], status="uncertain" if exc.billed_possible else "failed",
                                    finished_at=utcnow(), message=exc.message, **values)
            log_event(logger, logging.WARNING, "vt_facebook_apify_start_failed", run=run["id"], billed_possible=exc.billed_possible)
            return errors(exc.message)
        except (TimeoutError, HttpRequestError) as exc:
            # The Actor run may be fine: the saved run ID lets the next check read it instead of paying for another.
            message = "Apify took too long to answer" if isinstance(exc, TimeoutError) else "Apify could not be read"
            message += "; the next check continues the same Actor run"
            await asyncio.to_thread(jobs.update_run, run["id"], status="running" if remote_id else "uncertain", message=message)
            log_event(logger, logging.WARNING, "vt_facebook_apify_read_failed", run=run["id"], status_code=getattr(exc, "status_code", None))
            return errors(message)
        finally:
            await asyncio.to_thread(release_job_lock, lock, owner)
