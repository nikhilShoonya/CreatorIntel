"""CreatorIntel API - FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import creators, exports, system, uploads
from app.config.settings import get_settings
from app.models.db import init_db
from app.services.api_usage import UsageFlusher
from app.services.db_keepalive import DatabaseKeepAlive
from app.services.housekeeping import HousekeepingTask
from app.services.http_client import close_http_client
from app.services.orchestrator import EnrichmentOrchestrator
from app.utils.logging import configure_logging, log_event
from app.video_performance import models as _video_tracking_models  # noqa: F401  (register video_tracking_* tables)
from app.video_performance.api import router as video_performance_router
from app.video_performance.scheduler import VideoTrackingScheduler
from app.video_performance.tracker import VideoTracker

settings = get_settings()
configure_logging(settings.log_level, settings.log_dir, settings.log_retention_days)
logger = logging.getLogger("creatorintel")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    orchestrator = EnrichmentOrchestrator(settings)
    app.state.orchestrator = orchestrator
    log_event(
        logger, logging.INFO, "startup",
        youtube_configured=settings.youtube_configured,
        instagram_configured=settings.instagram_configured,
        ai_configured=settings.ai_configured,
    )
    orchestrator.resume_incomplete()
    orchestrator.start_ai_backlog()

    # Video Performance module: its own tracker + daily scheduler (independent of Creator Analytics)
    video_tracker = VideoTracker(settings)
    video_scheduler = VideoTrackingScheduler(video_tracker, settings)
    app.state.video_tracker = video_tracker
    app.state.video_scheduler = video_scheduler
    await video_tracker.resume_after_restart()
    video_scheduler.start()
    housekeeping = HousekeepingTask(settings)  # deletes old Creator Analytics files after saving their rows
    housekeeping.start()
    keepalive = DatabaseKeepAlive(settings)  # warm connection pool + keep a serverless DB awake in work hours
    keepalive.start()
    usage = UsageFlusher()  # writes YouTube quota usage counts to the database
    usage.start()
    try:
        yield
    finally:
        await usage.stop()
        await keepalive.stop()
        await housekeeping.stop()
        await video_scheduler.stop()
        await video_tracker.shutdown()
        await orchestrator.shutdown()
        await close_http_client()


app = FastAPI(title="CreatorIntel API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)


@app.exception_handler(RequestValidationError)
async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "request"
    return JSONResponse(status_code=422, content={"detail": f"Invalid {field}: {first.get('msg', 'invalid value')}"})


@app.exception_handler(Exception)
async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled_error")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


app.include_router(system.router)
app.include_router(uploads.router)
app.include_router(creators.router)
app.include_router(exports.router)
app.include_router(video_performance_router)
