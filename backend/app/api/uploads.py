import logging
from collections import Counter
from datetime import timedelta
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.ingestion_agent import FileIngestionAgent, IngestionError
from app.api.deps import get_orchestrator
from app.config.settings import get_settings
from app.models.db import get_db, get_read_db
from app.models.entities import Creator, CreatorStatus, Upload, UploadItem, UploadRow, UploadStatus
from app.schemas.api import DeleteResult, UploadDetailOut, UploadItemOut, UploadOut, UploadRowOut, UploadRowsOut
from app.services.creator_service import CreatorBusy, delete_upload, remove_upload_file
from app.services.orchestrator import EnrichmentOrchestrator
from app.utils.spreadsheet import table_bytes
from app.services.upload_service import create_upload, store_upload_file, upload_status_counts
from app.utils.logging import log_event
from app.utils.time import as_utc

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
        **_file_status(upload),
    }


def _file_status(upload: Upload) -> dict:
    retention = get_settings().upload_file_retention_days
    delete_after = None
    if upload.file_deleted_at is None and upload.stored_filename and retention > 0 and upload.created_at:
        delete_after = as_utc(upload.created_at) + timedelta(days=retention)
    return {"file_deleted_at": upload.file_deleted_at, "file_delete_after": delete_after}


_ITEM_COLUMNS = (
    UploadItem.creator_id, UploadItem.position, UploadItem.source_row, UploadItem.from_cache,
    Creator.channel_name, Creator.platform, Creator.status, Creator.error_message,
)


def load_upload_detail(db: Session, upload_id: str) -> UploadDetailOut:
    """Upload + its items in ONE query (each database round trip is slow on a remote database)."""
    rows = db.execute(
        select(Upload, *_ITEM_COLUMNS)
        .outerjoin(UploadItem, UploadItem.upload_id == Upload.id)
        .outerjoin(Creator, Creator.id == UploadItem.creator_id)
        .where(Upload.id == upload_id)
        .order_by(UploadItem.position)
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Upload not found")
    upload = rows[0][0]
    items = [
        UploadItemOut(**{key: value for key, value in row._mapping.items() if key != "Upload"})
        for row in rows
        if row.creator_id is not None
    ]
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
        for attempt in (1, 2):
            try:
                upload = create_upload(db, settings, safe_name, stored, ingestion)
                db.commit()
                return upload.id
            except IntegrityError:
                # Another upload created one of these creators at the same moment: retry once, it now exists.
                db.rollback()
                if attempt == 2:
                    raise HTTPException(status_code=409, detail="Another upload is adding the same creators right now. Please try again.")
        raise AssertionError("unreachable")

    upload_id = await run_in_threadpool(persist)
    orchestrator.start_upload(upload_id)
    return await run_in_threadpool(load_upload_detail, db, upload_id)


@router.get("", response_model=list[UploadOut])
def list_uploads(limit: int = Query(default=50, ge=1, le=200), db: Session = Depends(get_read_db)):
    # Uploads + per-status creator counts in ONE query.
    counts = (
        select(UploadItem.upload_id, Creator.status, func.count().label("n"))
        .join(Creator, Creator.id == UploadItem.creator_id)
        .group_by(UploadItem.upload_id, Creator.status)
        .subquery()
    )
    latest = select(Upload.id).order_by(Upload.created_at.desc()).limit(limit).scalar_subquery()
    rows = db.execute(
        select(Upload, counts.c.status, counts.c.n)
        .outerjoin(counts, counts.c.upload_id == Upload.id)
        .where(Upload.id.in_(latest))
        .order_by(Upload.created_at.desc())
    ).all()
    uploads: dict[str, tuple[Upload, Counter]] = {}
    for upload, status, n in rows:
        entry = uploads.setdefault(upload.id, (upload, Counter()))
        if status is not None:
            entry[1][status] = n
    return [UploadOut(**_summary(upload, status_counts)) for upload, status_counts in uploads.values()]


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


_ROW_LABELS = {"queued": "Analysed", "cached": "Reused recent data", "duplicate": "Duplicate", "invalid": "Invalid link"}


def _upload_rows(db: Session, upload_id: str) -> tuple[Upload, list[UploadRow]]:
    upload = db.get(Upload, upload_id)
    if upload is None:
        raise HTTPException(status_code=404, detail="Upload not found")
    rows = db.scalars(select(UploadRow).where(UploadRow.upload_id == upload_id).order_by(UploadRow.row_number)).all()
    return upload, list(rows)


@router.get("/{upload_id}/rows", response_model=UploadRowsOut)
def upload_rows(upload_id: str, db: Session = Depends(get_read_db)):
    """The uploaded file's rows as stored in the database (available even after the file is deleted)."""
    upload, rows = _upload_rows(db, upload_id)
    counts = upload_status_counts(db, [upload_id]).get(upload_id, Counter())
    return UploadRowsOut(upload=UploadOut(**_summary(upload, counts)), rows=[UploadRowOut.model_validate(r) for r in rows])


@router.get("/{upload_id}/rows/export")
async def export_upload_rows(
    upload_id: str, format: Literal["csv", "excel"] = "excel", db: Session = Depends(get_read_db)
):
    upload, rows = await run_in_threadpool(_upload_rows, db, upload_id)
    table = [[r.row_number, r.channel_name, r.channel_link, _ROW_LABELS.get(r.outcome, r.outcome), r.message] for r in rows]
    columns = ["Row", "Channel Name", "Channel Link", "Result", "Notes"]
    kind = "csv" if format == "csv" else "excel"
    content = await run_in_threadpool(table_bytes, columns, table, kind, "Uploaded rows", {"Source file": upload.filename})
    stem = Path(upload.filename).stem[:80] or "upload"
    ext, media = ("csv", "text/csv; charset=utf-8") if kind == "csv" else (
        "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    return Response(content=content, media_type=media, headers={"Content-Disposition": f'attachment; filename="{stem}_rows.{ext}"'})


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
    stored_filename = upload.stored_filename
    try:
        removed = delete_upload(db, upload, delete_creators)
    except CreatorBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    remove_upload_file(get_settings(), stored_filename, upload_id)  # only once the delete is committed
    suffix = f" and {removed} creator{'s' if removed != 1 else ''}" if delete_creators else ""
    return DeleteResult(deleted=1, message=f"Deleted upload{suffix}")
