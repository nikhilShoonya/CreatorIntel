"""REST API of the Video Performance module: /api/video-performance/*"""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any, Literal, cast

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from app.config.settings import get_settings
from app.utils.logging import log_event
from app.utils.spreadsheet import local_time, table_bytes, tz_label
from app.utils.time import as_utc, utcnow
from app.utils.text import clean_text
from app.utils.url_parser import parse_channel_link
from app.video_performance import queries
from app.video_performance import repository as repo
from app.video_performance.ingest import ImportError_, parse_import
from app.video_performance.models import JobType, VideoStatus
from app.video_performance.schemas import (
    ActionOut,
    BulkIn,
    CreatorCreateIn,
    CreatorOut,
    CreatorUpdateIn,
    DashboardOut,
    Facets,
    HistoryOut,
    ImportRowOut,
    JobInfo,
    UploadOut,
    UploadResultOut,
    UploadRowsOut,
    VideoCreateIn,
    VideoDetailOut,
    VideoListOut,
    VideoOut,
    VideoUpdateIn,
)
from app.video_performance.scheduler import VideoTrackingScheduler
from app.video_performance.tracker import VideoTracker
from app.video_performance.share_links import ShareLinkResolver, get_share_resolver, is_share_link
from app.video_performance.urls import parse_instagram_handle, parse_video_link

logger = logging.getLogger("creatorintel.video_performance.api")
router = APIRouter(prefix="/api/video-performance", tags=["video-performance"])

PAGE_SIZES = (10, 25, 50, 100)


def get_tracker(request: Request) -> VideoTracker:
    tracker = getattr(request.app.state, "video_tracker", None)
    if tracker is None:
        raise HTTPException(status_code=503, detail="Video tracking engine is not ready")
    return tracker


def _conflict(exc: repo.Conflict) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


def _username(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    handle = parse_instagram_handle(value)
    if handle is None:
        raise HTTPException(status_code=422, detail="Instagram username is not valid")
    return handle


def _share_resolver(request: Request) -> ShareLinkResolver:
    return getattr(request.app.state, "share_resolver", None) or get_share_resolver()


async def _parse_link(link: str, resolver: ShareLinkResolver):
    """parse_video_link, after turning a Facebook share link into its real video link."""
    if is_share_link(link):
        resolution = await resolver.resolve(link.strip())
        if not resolution.resolved_url:
            raise HTTPException(status_code=422, detail=resolution.error)
        link = resolution.resolved_url
    parsed = parse_video_link(link)
    if not parsed.ok:
        raise HTTPException(status_code=422, detail=parsed.error)
    return parsed


# ---------------------------------------------------------------- uploads
@router.post("/uploads", response_model=UploadResultOut, status_code=201)
async def upload_videos(
    request: Request, file: UploadFile = File(...), tracker: VideoTracker = Depends(get_tracker)
):
    settings = get_settings()
    content = await file.read(settings.max_upload_bytes + 1)
    name = Path(file.filename or "videos").name[:200]
    try:
        result = await run_in_threadpool(parse_import, name, content, settings.max_upload_bytes)
        share_links = [r.raw_link for r in result.rows if r.raw_link and is_share_link(r.raw_link)]
        if share_links:  # resolve them, then re-read so duplicates are detected on the real video IDs
            resolutions = await _share_resolver(request).resolve_many(share_links)
            result = await run_in_threadpool(parse_import, name, content, settings.max_upload_bytes, resolutions)
    except ImportError_ as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def persist():
        settings.upload_dir.mkdir(parents=True, exist_ok=True)
        stored = f"vt_{uuid.uuid4().hex}{Path(name).suffix.lower()}"
        (settings.upload_dir / stored).write_bytes(content)
        return repo.import_rows(name, stored, result.rows)

    upload_id, new_ids = await run_in_threadpool(persist)
    tracker.start_processing(new_ids)
    upload = next(u for u in await run_in_threadpool(queries.uploads, 5) if u.id == upload_id)
    log_event(logger, logging.INFO, "vt_upload", upload_id=upload_id, rows=len(result.rows), added=len(new_ids))
    return UploadResultOut(
        upload=upload,
        rows=[
            ImportRowOut(
                row=r.row, creator_name=r.creator_name, platform=r.parsed.platform,
                video_url=r.parsed.url, status=r.status, message=r.message,
            )
            for r in result.rows
        ],
    )


@router.get("/uploads", response_model=list[UploadOut])
def list_uploads():
    return queries.uploads()


_PLATFORM_LABELS = {"youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook"}
_ROW_LABELS = {"added": "Added", "already_tracked": "Already tracked", "duplicate": "Duplicate", "invalid": "Invalid"}


@router.get("/uploads/{upload_id}/rows", response_model=UploadRowsOut)
def upload_rows(upload_id: str):
    """The uploaded file's rows as stored in the database (available after the file is deleted)."""
    found = queries.upload_rows(upload_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    upload, rows = found
    return UploadRowsOut(upload=upload, rows=rows)


def _upload_scope(upload_id: str | None) -> list[int] | None:
    """The tracked videos of one uploaded file (None = no upload filter)."""
    if not upload_id:
        return None
    ids = queries.upload_video_ids(upload_id)
    if ids is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    return ids


def _TZ_LABEL() -> str:
    return tz_label(get_settings().video_tracking_timezone)


def _local_text(value) -> str:
    """'08 Oct 2026, 15:55 IST' for the About sheet."""
    when = local_time(as_utc(value), get_settings().video_tracking_timezone)
    return f"{when:%d %b %Y, %H:%M} {_TZ_LABEL()}" if when else "-"


def _file_response(content: bytes, kind: str, stem: str) -> Response:
    ext, media = ("csv", "text/csv; charset=utf-8") if kind == "csv" else (
        "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return Response(content=content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{stem}.{ext}"'})


@router.get("/uploads/{upload_id}/rows/export")
async def export_upload_rows(upload_id: str, format: Literal["csv", "excel"] = "excel"):
    found = await run_in_threadpool(queries.upload_rows, upload_id)
    if found is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    upload, rows = found
    table = [[r.row_number, r.creator_name, r.platform, r.video_link, r.username, _ROW_LABELS.get(r.status, r.status), r.message] for r in rows]
    columns = ["Row", "Creator Name", "Platform", "Video Link", "Username", "Result", "Notes"]
    content = await run_in_threadpool(
        table_bytes, columns, table, "csv" if format == "csv" else "excel", "Uploaded rows",
        {"Source file": upload.filename, "Uploaded": _local_text(upload.created_at)},
    )
    return _file_response(content, "csv" if format == "csv" else "excel", f"{Path(upload.filename).stem[:80] or 'videos'}_rows")


@router.get("/exports/{kind}")
async def export_videos(
    kind: Literal["excel", "csv"],
    layout: Literal["performance", "upload"] = "performance",
    q: str | None = Query(default=None, max_length=200),
    platform: Literal["youtube", "instagram", "facebook"] | None = None,
    creator: str | None = Query(default=None, max_length=300),
    status: str | None = Query(default=None, max_length=20),
    sort_by: queries.SortKey | None = None,
    sort_dir: Literal["asc", "desc"] = "desc",
    upload_id: str | None = Query(default=None, max_length=32),
):
    """Tracked videos (respecting the table's filters) as a report or import-ready list."""
    video_ids = await run_in_threadpool(_upload_scope, upload_id)
    filters = queries.VideoFilters(q=q or None, platform=platform, creator=creator or None, status=status,
                                   sort_by=sort_by, sort_dir=sort_dir, video_ids=video_ids)
    videos = await run_in_threadpool(queries.export_videos, filters)
    stem = "video_performance"
    info: dict[str, object] = {"Generated": _local_text(utcnow())}
    if upload_id:
        uploaded = await run_in_threadpool(queries.upload_rows, upload_id)
        if uploaded:
            stem = f"{Path(uploaded[0].filename).stem[:80] or 'upload'}_tracking"
            info["Source file"] = f"{uploaded[0].filename} (uploaded {_local_text(uploaded[0].created_at)})"
    applied = {"Search": q, "Platform": platform, "Creator": creator, "Status": status,
               "Sorted by": f"{sort_by} ({sort_dir})" if sort_by else None}
    info["Filters"] = ", ".join(f"{k}: {v}" for k, v in applied.items() if v) or "None (all videos)"
    if layout == "upload":
        columns = ["Video Link", "Creator Name", "Platform", "Username"]
        table = [
            [v.video_url, v.creator_name, _PLATFORM_LABELS.get(v.platform, v.platform),
             v.owner_username if v.platform == "instagram" else None]
            for v in videos
        ]
        info["How to use"] = "Edit if needed, then upload it again in Tracking Library > Upload Excel"
        content = await run_in_threadpool(table_bytes, columns, table, kind, "Video list", info)
        return _file_response(content, kind, f"video_list_upload_ready_{utcnow().strftime('%Y%m%d_%H%M')}")
    columns = [
        "Video", "Video URL", "Platform", "Creator", "Current Views", "Previous Views", "Views Gained", "Growth %",
        "Likes", "Comments", "Engagement Rate (%)", "Sentiment", "Sentiment Confidence", "Tracking Status", "Notes",
        f"Last Checked ({_TZ_LABEL()})", f"Published ({_TZ_LABEL()})", "Source",
    ]
    tz_name = get_settings().video_tracking_timezone

    def fmt(value):
        return local_time(as_utc(value), tz_name) if value else None

    def shown(value, missing="N/A"):
        """Use the library's visible placeholder without changing real zero values."""
        return missing if value is None or value == "" else value

    table = [
        [
            VideoOut.model_validate(v).display_title, v.video_url, _PLATFORM_LABELS.get(v.platform, v.platform),
            shown(v.creator_name), shown(v.current_views), shown(v.previous_views, "-"),
            shown(v.views_gained, "-"), shown(v.growth_pct, "-"), shown(v.likes), shown(v.comments),
            shown(v.engagement_rate), shown(v.sentiment), shown(v.sentiment_confidence), v.status,
            shown(v.status_reason), shown(fmt(v.last_checked_at), "Never"), shown(fmt(v.published_at)),
            shown(v.source),
        ]
        for v in videos
    ]
    info["Times"] = f"Shown in {tz_name} ({_TZ_LABEL()})"
    content = await run_in_threadpool(table_bytes, columns, table, kind, "Tracked videos", info)
    return _file_response(content, kind, f"{stem}_{utcnow().strftime('%Y%m%d_%H%M')}")


# ----------------------------------------------------------------- videos
@router.get("/videos", response_model=VideoListOut)
def list_videos(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10),
    q: str | None = Query(default=None, max_length=200),
    platform: Literal["youtube", "instagram", "facebook"] | None = None,
    creator: str | None = Query(default=None, max_length=300),
    creator_id: int | None = None,
    status: str | None = Query(default=None, max_length=20),
    sort_by: queries.SortKey | None = None,
    sort_dir: Literal["asc", "desc"] = "desc",
    upload_id: str | None = Query(default=None, max_length=32),
):
    if page_size not in PAGE_SIZES:
        raise HTTPException(status_code=422, detail=f"page_size must be one of {PAGE_SIZES}")
    if status and status not in VideoStatus.ALL:
        raise HTTPException(status_code=422, detail="Unknown status")
    filters = queries.VideoFilters(q=q or None, platform=platform, creator=creator or None, creator_id=creator_id,
                                   status=status, sort_by=sort_by, sort_dir=sort_dir, video_ids=_upload_scope(upload_id))
    items, total, total_pages, counts = queries.list_videos(filters, page, page_size)
    return VideoListOut(items=items, total=total, page=min(page, total_pages), page_size=page_size,
                        total_pages=total_pages, status_counts=counts)


@router.get("/videos/facets", response_model=Facets)
def video_facets():
    return Facets(creators=queries.creator_names(), statuses=list(VideoStatus.ALL))


@router.get("/videos/{video_id}", response_model=VideoDetailOut)
def get_video(video_id: int):
    video = queries.video(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    detail = VideoDetailOut.model_validate(video)
    detail.caption_text = video.caption
    return detail


@router.post("/videos", response_model=VideoOut, status_code=201)
async def add_video(body: VideoCreateIn, request: Request, tracker: VideoTracker = Depends(get_tracker)):
    parsed = await _parse_link(body.video_url, _share_resolver(request))
    owner = _username(body.instagram_username) if parsed.platform == "instagram" else None
    creator = clean_text(body.creator_name)[:300] if body.creator_name else None
    if parsed.platform == "instagram" and not owner:
        owner = parse_instagram_handle(creator)
    try:
        video_id = await run_in_threadpool(repo.add_video, parsed, creator, owner, "manual")
    except repo.Conflict as exc:
        raise _conflict(exc) from exc
    tracker.start_processing([video_id])
    return VideoOut.model_validate(await run_in_threadpool(queries.video, video_id))


@router.put("/videos/{video_id}", response_model=VideoOut)
async def update_video(
    video_id: int, body: VideoUpdateIn, request: Request, tracker: VideoTracker = Depends(get_tracker)
):
    parsed = None
    if body.video_url is not None:
        parsed = await _parse_link(body.video_url, _share_resolver(request))
    owner = None
    if body.instagram_username is not None:
        owner = _username(body.instagram_username) or ""
    elif parsed is not None and parsed.platform == "instagram":
        # Match the create endpoint: when the permalink does not include the
        # owner, a creator name that is an Instagram handle is still usable.
        owner = parsed.owner_username or parse_instagram_handle(body.creator_name)
    try:
        reprocess = await run_in_threadpool(
            lambda: repo.update_video(
                video_id,
                creator_name=clean_text(body.creator_name)[:300] if body.creator_name is not None else None,
                parsed=parsed,
                owner_username=owner,
                tracking=body.tracking_status,
            )
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except repo.Conflict as exc:
        raise _conflict(exc) from exc
    if reprocess:
        tracker.start_processing([video_id])
    return VideoOut.model_validate(await run_in_threadpool(queries.video, video_id))


@router.delete("/videos/{video_id}", response_model=ActionOut)
def delete_video(video_id: int):
    if repo.delete_videos([video_id]) == 0:
        raise HTTPException(status_code=404, detail="Video not found")
    return ActionOut(affected=1, message="Video deleted with its history")


async def _require_video(video_id: int):
    video = await run_in_threadpool(queries.video, video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return video


@router.post("/videos/{video_id}/refresh", response_model=ActionOut, status_code=202)
async def refresh_video(video_id: int, tracker: VideoTracker = Depends(get_tracker)):
    """Fetch current numbers now (works for paused videos too)."""
    video = await _require_video(video_id)
    if video.status in VideoStatus.ACTIVE_WORK:
        raise HTTPException(status_code=409, detail="This video is already being processed")
    tracker.start_processing([video_id])
    return ActionOut(affected=1, message="Refreshing views")


@router.post("/videos/{video_id}/retry", response_model=ActionOut, status_code=202)
async def retry_video(video_id: int, tracker: VideoTracker = Depends(get_tracker)):
    await _require_video(video_id)
    ids = await run_in_threadpool(
        repo.queue_for_processing,
        [video_id],
        (VideoStatus.FAILED, VideoStatus.UNSUPPORTED, VideoStatus.PARTIAL, VideoStatus.VIDEO_DOWN),
    )
    if not ids:
        raise HTTPException(
            status_code=409, detail="Only Failed, Partial, Unsupported or Video Down videos can be retried"
        )
    tracker.start_processing(ids)
    return ActionOut(affected=1, message="Retrying")


@router.post("/videos/{video_id}/pause", response_model=ActionOut)
async def pause_video(video_id: int):
    await _require_video(video_id)
    await run_in_threadpool(repo.set_paused, [video_id], True)
    return ActionOut(affected=1, message="Tracking stopped")


@router.post("/videos/{video_id}/resume", response_model=ActionOut, status_code=202)
async def resume_video(video_id: int, tracker: VideoTracker = Depends(get_tracker)):
    await _require_video(video_id)
    ids = await run_in_threadpool(repo.set_paused, [video_id], False)
    tracker.start_processing(ids)
    return ActionOut(affected=len(ids), message="Tracking started" if ids else "Video is already being tracked")


@router.post("/videos/retry-failed", response_model=ActionOut, status_code=202)
async def retry_failed(tracker: VideoTracker = Depends(get_tracker)):
    failed = await run_in_threadpool(repo.failed_video_ids)
    ids = await run_in_threadpool(repo.queue_for_processing, failed, (VideoStatus.FAILED,)) if failed else []
    tracker.start_processing(ids)
    return ActionOut(affected=len(ids), message=f"Retrying {len(ids)} failed video{'s' if len(ids) != 1 else ''}")


@router.post("/videos/bulk", response_model=ActionOut)
async def bulk_videos(body: BulkIn, tracker: VideoTracker = Depends(get_tracker)):
    ids = list(dict.fromkeys(body.ids))
    if body.action == "delete":
        n = await run_in_threadpool(repo.delete_videos, ids)
        return ActionOut(affected=n, message=f"Deleted {n} video{'s' if n != 1 else ''}")
    if body.action == "pause":
        await run_in_threadpool(repo.set_paused, ids, True)
        return ActionOut(affected=len(ids), message=f"Stopped tracking {len(ids)} video{'s' if len(ids) != 1 else ''}")
    if body.action == "resume":
        resumed = await run_in_threadpool(repo.set_paused, ids, False)
        tracker.start_processing(resumed)
        return ActionOut(affected=len(resumed), message=f"Started tracking {len(resumed)} video{'s' if len(resumed) != 1 else ''}")
    queued = await run_in_threadpool(repo.queue_for_processing, ids)
    tracker.start_processing(queued)
    return ActionOut(affected=len(queued), message=f"Refreshing {len(queued)} video{'s' if len(queued) != 1 else ''}")


@router.get("/history/{video_id}", response_model=HistoryOut)
def video_history(video_id: int):
    video = queries.video(video_id)
    if video is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return HistoryOut(video=VideoOut.model_validate(video), snapshots=queries.history(video_id))


# --------------------------------------------------------------- creators
@router.get("/creators", response_model=list[CreatorOut])
def list_creators():
    return queries.creators()


@router.post("/creators", response_model=CreatorOut, status_code=201)
async def add_creator(body: CreatorCreateIn, tracker: VideoTracker = Depends(get_tracker)):
    parsed = parse_channel_link(body.channel_url)
    if not parsed.is_supported or parsed.identifier_type == "video":
        raise HTTPException(
            status_code=422,
            detail=parsed.error if not parsed.is_supported else "Use the channel/profile link, not a video link",
        )
    name = clean_text(body.creator_name)[:300] if body.creator_name else (
        f"@{parsed.identifier}" if parsed.identifier_type in ("handle", "username") or parsed.platform == "instagram" else parsed.identifier
    )
    try:
        creator_id = await run_in_threadpool(repo.create_creator, name, parsed)
    except repo.Conflict as exc:
        raise _conflict(exc) from exc
    tracker.start_creator_setup(creator_id, body.backfill)
    return next(c for c in await run_in_threadpool(queries.creators) if c.id == creator_id)


@router.put("/creators/{creator_id}", response_model=CreatorOut)
async def update_creator(creator_id: int, body: CreatorUpdateIn, tracker: VideoTracker = Depends(get_tracker)):
    try:
        enabled_now = await run_in_threadpool(
            repo.update_creator, creator_id, clean_text(body.creator_name)[:300] if body.creator_name else None, body.enabled
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if enabled_now:
        tracker.start_creator_setup(creator_id, 0)
    return next(c for c in await run_in_threadpool(queries.creators) if c.id == creator_id)


@router.delete("/creators/{creator_id}", response_model=ActionOut)
def delete_creator(creator_id: int, delete_videos: bool = Query(default=False)):
    try:
        removed = repo.delete_creator(creator_id, delete_videos)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    suffix = f" and {removed} video{'s' if removed != 1 else ''}" if delete_videos else ""
    return ActionOut(affected=1, message=f"Creator tracking removed{suffix}")


@router.post("/creators/{creator_id}/discover", response_model=ActionOut, status_code=202)
async def discover_now(creator_id: int, tracker: VideoTracker = Depends(get_tracker)):
    row = await run_in_threadpool(repo.creator_row, creator_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    if not row["enabled"]:
        raise HTTPException(status_code=409, detail="Enable tracking for this creator first")
    tracker.start_discovery_for([creator_id])
    return ActionOut(affected=1, message="Checking for new videos")


# --------------------------------------------------------- dashboard & jobs
def _scheduler(request: Request) -> VideoTrackingScheduler | None:
    return getattr(request.app.state, "video_scheduler", None)


def _jobs(request: Request, tracker: VideoTracker) -> list[JobInfo]:
    scheduler = _scheduler(request)
    next_runs = scheduler.next_runs() if scheduler else {}
    runs = {run.job_type: run for run in repo.latest_runs()}
    jobs = []
    for job_type in (JobType.CREATOR_DISCOVERY, JobType.METRICS_REFRESH):
        run = runs.get(job_type)
        progress = tracker.job_progress(job_type)
        jobs.append(
            JobInfo(
                job_type=job_type, running=tracker.job_running(job_type), next_run_at=next_runs.get(job_type),
                last_status=run.status if run else None, last_started_at=run.started_at if run else None,
                last_finished_at=run.finished_at if run else None, last_message=run.message if run else None,
                progress_done=progress[0] if progress else None, progress_total=progress[1] if progress else None,
            )
        )
    return jobs


@router.get("/dashboard", response_model=DashboardOut)
async def dashboard(
    request: Request,
    days: int = Query(7, ge=2, le=90, description="Length of the trend range in days"),
    tracker: VideoTracker = Depends(get_tracker),
):
    # Independent read queries run in parallel (each has its own session) to keep remote-DB latency low.
    # Keep the number of parallel queries below DB_POOL_SIZE: an extra connection to a remote DB costs ~1 s.
    (totals, sentiment, recent, top, engagement, latest, jobs, trend) = await asyncio.gather(
        asyncio.to_thread(queries.dashboard_totals),
        asyncio.to_thread(queries.sentiment_breakdown),
        asyncio.to_thread(queries.recent_sentiment),
        asyncio.to_thread(queries.ranked, "top"),
        asyncio.to_thread(queries.ranked, "engagement"),
        asyncio.to_thread(queries.ranked, "latest", 20),
        asyncio.to_thread(_jobs, request, tracker),
        asyncio.to_thread(queries.trend, days),
    )
    totals = cast(dict[str, Any], totals)
    listed = {v.id for v in (*top, *engagement)}
    overall, by_platform = sentiment
    return DashboardOut(
        **totals, sentiment_overall=overall, sentiment_by_platform=by_platform,
        recent_sentiment=recent, top_performing=top, highest_engagement=engagement,
        latest_detected=latest, jobs=jobs, timezone=get_settings().video_tracking_timezone,
        range_days=days, trend=trend.points, videos_added_in_range=trend.videos_added,
        views_gained_in_range=trend.views_gained, views_growth_pct=trend.views_growth_pct,
        video_trends={video_id: series for video_id, series in trend.video_gains.items() if video_id in listed},
    )


@router.get("/jobs", response_model=list[JobInfo])
async def jobs(request: Request, tracker: VideoTracker = Depends(get_tracker)):
    return await asyncio.to_thread(_jobs, request, tracker)


@router.post("/jobs/{job_type}/run", response_model=ActionOut, status_code=202)
async def run_job(job_type: Literal["metrics_refresh", "creator_discovery"], tracker: VideoTracker = Depends(get_tracker)):
    if not tracker.start_job(job_type, "manual"):
        raise HTTPException(status_code=409, detail="This job is already running")
    return ActionOut(affected=0, message="Job started")
