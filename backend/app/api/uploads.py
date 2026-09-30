import logging
from collections import Counter

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.agents.ingestion_agent import FileIngestionAgent, IngestionError
from app.api.deps import get_orchestrator
from app.config.settings import get_settings
from app.models.db import get_db, get_read_db
from app.models.entities import Creator, CreatorStatus, Upload, UploadItem, UploadStatus
from app.schemas.api import DeleteResult, UploadDetailOut, UploadItemOut, UploadOut
from app.services.creator_service import CreatorBusy, delete_upload
from app.services.orchestrator import EnrichmentOrchestrator
from app.services.upload_service import create_upload, store_upload_file, upload_status_counts
from app.utils.logging import log_event

logger = logging.getLogger("creatorintel.api.uploads")
router = APIRouter(prefix="/api/uploads", tags=["uploads"])


def _summary(upload: Upload, counts: Counter) -> dict:
    partial = counts[CreatorStatus.PARTIAL]
    completed = counts[CreatorStatus.COMPLETED]
    return {
        "id": upload.id,
        "filename": upload.filename,
        "total_rows": sum(counts.values()),
        "successful_rows": completed + partial,
        "partial_rows": partial,
        "failed_rows": counts[CreatorStatus.FAILED],
        "duplicate_rows": upload.duplicate_rows,
        "completed_rows": completed,
        "processed_rows": sum(counts[s] for s in CreatorStatus.FINAL),
        "status": upload.status,
        "error_message": upload.error_message,
        "created_at": upload.created_at,
        "completed_at": upload.completed_at,
    }


def load_upload_detail(db: Session, upload_id: str) -> UploadDetailOut:
    """Upload + its items in two queries (no per-row lookups)."""
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    rows = db.execute(
        select(
            UploadItem.creator_id, UploadItem.position, UploadItem.source_row, UploadItem.from_cache,
            Creator.channel_name, Creator.platform, Creator.status, Creator.error_message,
        )
        .join(Creator, Creator.id == UploadItem.creator_id)
        .where(UploadItem.upload_id == upload_id)
        .order_by(UploadItem.position)
    ).all()
    items = [UploadItemOut(**row._mapping) for row in rows]
    counts = Counter(item.status for item in items)
    return UploadDetailOut(**_summary(upload, counts), items=items)


@router.post("", response_model=UploadDetailOut, status_code=202)
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    orchestrator: EnrichmentOrchestrator = Depends(get_orchestrator),
):
    settings = get_settings()
    content = await file.read(settings.max_upload_bytes + 1)
    agent = FileIngestionAgent(max_rows=settings.max_rows_per_upload)
    try:
        safe_name = agent.validate_file(file.filename, content, settings.max_upload_bytes)
        ingestion = await run_in_threadpool(agent.ingest, safe_name, content)
    except IngestionError as exc:
        log_event(logger, logging.WARNING, "upload_rejected", filename=file.filename, reason=str(exc))
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    def persist() -> str:
        stored = store_upload_file(settings, safe_name, content)
        upload = create_upload(db, settings, safe_name, stored, ingestion)
        db.commit()
        return upload.id

    upload_id = await run_in_threadpool(persist)
    orchestrator.start_upload(upload_id)
    return await run_in_threadpool(load_upload_detail, db, upload_id)


@router.get("", response_model=list[UploadOut])
def list_uploads(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_read_db)):
    uploads = db.scalars(select(Upload).order_by(Upload.created_at.desc()).limit(limit)).all()
    counts = upload_status_counts(db, [upload.id for upload in uploads])
    return [UploadOut(**_summary(upload, counts.get(upload.id, Counter()))) for upload in uploads]


@router.post("/{upload_id}/retry-failed", response_model=UploadDetailOut, status_code=202)
async def retry_failed(
    upload_id: str,
    db: Session = Depends(get_db),
    orchestrator: EnrichmentOrchestrator = Depends(get_orchestrator),
):
    """Re-run the pipeline for every Failed / Partial creator of this upload (valid links only)."""
    await run_in_threadpool(_queue_failed, db, upload_id)
    orchestrator.start_upload(upload_id)
    return await run_in_threadpool(load_upload_detail, db, upload_id)


def _queue_failed(db: Session, upload_id: str) -> None:
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    if upload.status == UploadStatus.PROCESSING:
        raise HTTPException(status_code=409, detail="This upload is still being processed")
    queued = db.execute(
        update(Creator)
        .where(
            Creator.id.in_(select(UploadItem.creator_id).where(UploadItem.upload_id == upload_id)),
            Creator.status.in_((CreatorStatus.FAILED, CreatorStatus.PARTIAL)),
            Creator.platform.in_(("youtube", "instagram")),
        )
        .values(status=CreatorStatus.PENDING)
        .execution_options(synchronize_session=False)
    ).rowcount
    if queued == 0:
        raise HTTPException(status_code=400, detail="There are no failed creators with valid links to retry")
    upload.status = UploadStatus.PROCESSING
    upload.completed_at = None
    upload.error_message = None
    db.commit()
    log_event(logger, logging.INFO, "upload_retry_failed", upload_id=upload_id, queued=queued)


@router.get("/{upload_id}", response_model=UploadDetailOut)
def get_upload(upload_id: str, db: Session = Depends(get_read_db)):
    return load_upload_detail(db, upload_id)


@router.delete("/{upload_id}", response_model=DeleteResult)
def remove_upload(
    upload_id: str,
    delete_creators: bool = Query(default=False, description="Also delete creators that belong to no other upload"),
    db: Session = Depends(get_db),
):
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    try:
        removed = delete_upload(db, get_settings(), upload, delete_creators)
    except CreatorBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    suffix = f" and {removed} creator{'s' if removed != 1 else ''}" if delete_creators else ""
    return DeleteResult(deleted=1, message=f"Deleted upload{suffix}")
