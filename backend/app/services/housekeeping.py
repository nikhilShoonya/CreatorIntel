"""Housekeeping for Creator Analytics: delete old uploaded files after their rows are safely in the database.

Rows are saved at upload time (upload_rows). Uploads made before that existed are back-filled from the
file itself before it is deleted, so the data shown on the History page never depends on the file.
"""

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from sqlalchemy import exists, select

from app.agents.ingestion_agent import FileIngestionAgent, IngestionError
from app.config.settings import Settings
from app.models.db import session_scope
from app.models.entities import Upload, UploadRow
from app.services.upload_service import duplicate_upload_rows
from app.utils.logging import log_event
from app.utils.time import utcnow

logger = logging.getLogger("creatorintel.housekeeping")

STARTUP_DELAY_SECONDS = 30


def _backfill_rows(db, upload: Upload, path: Path) -> int:
    """Save the rows of an old upload from its file (uploads made before rows were stored)."""
    try:
        result = FileIngestionAgent().ingest(upload.filename, path.read_bytes())
    except (OSError, IngestionError) as exc:
        log_event(logger, logging.WARNING, "housekeeping_backfill_failed", upload_id=upload.id, reason=str(exc))
        return 0
    rows = [
        UploadRow(
            upload_id=upload.id,
            row_number=row.source_row,
            channel_name=row.channel_name,
            channel_link=row.channel_link,
            outcome="invalid" if row.error else "queued",
            message=row.error,
        )
        for row in result.rows
    ]
    rows.extend(duplicate_upload_rows(upload.id, result))
    db.add_all(rows)
    return len(rows)


def cleanup_old_upload_files(settings: Settings) -> int:
    """Delete stored files older than the retention period (rows are kept). Returns files removed."""
    if settings.upload_file_retention_days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=settings.upload_file_retention_days)
    removed = 0
    with session_scope() as db:
        uploads = db.scalars(
            select(Upload).where(
                Upload.created_at < cutoff, Upload.file_deleted_at.is_(None), Upload.stored_filename.is_not(None)
            )
        ).all()
        for upload in uploads:
            path = settings.upload_dir / Path(upload.stored_filename).name
            has_rows = db.scalar(select(exists().where(UploadRow.upload_id == upload.id)))
            if not has_rows and path.exists():
                saved = _backfill_rows(db, upload, path)
                if saved == 0:
                    continue  # keep the file: its data could not be saved
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log_event(logger, logging.WARNING, "housekeeping_delete_failed", upload_id=upload.id, reason=str(exc))
                continue
            upload.file_deleted_at = utcnow()
            removed += 1
    if removed:
        log_event(logger, logging.INFO, "housekeeping_files_removed", module="creator_analytics", files=removed)
    return removed


class HousekeepingTask:
    """Runs the clean-up shortly after startup and then every few hours."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="housekeeping")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None

    async def _loop(self) -> None:
        await asyncio.sleep(STARTUP_DELAY_SECONDS)
        while True:
            try:
                await asyncio.to_thread(cleanup_old_upload_files, self.settings)
            except Exception:
                logger.exception("housekeeping_crashed")
            await asyncio.sleep(self.settings.housekeeping_interval_hours * 3600)
