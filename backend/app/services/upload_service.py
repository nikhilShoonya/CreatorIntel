"""Turns an ingested file into Upload / Creator / UploadItem records (creators already known are reused)."""

import logging
import uuid
from collections import Counter, defaultdict
from pathlib import Path

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.agents.ingestion_agent import IngestionResult
from app.config.settings import Settings
from app.models.entities import Creator, CreatorStatus, Upload, UploadItem, UploadRow, UploadStatus
from app.utils.logging import log_event

logger = logging.getLogger("creatorintel.uploads")


def store_upload_file(settings: Settings, safe_name: str, content: bytes) -> str:
    """Persist the original file under a random name (never executed or served)."""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}{Path(safe_name).suffix.lower()}"
    (settings.upload_dir / stored).write_bytes(content)
    return stored


def _reused_note(creator: Creator) -> str:
    """Upload-row note for a creator that is already in the database (its stored data is reused as it is)."""
    if creator.status in CreatorStatus.ACTIVE:
        return "Already in the creator list and being analysed - not fetched again"
    if creator.status == CreatorStatus.COMPLETED:
        return "Already in the creator list - existing data reused (use Refresh Profile in its details to update)"
    return (
        f"Already in the creator list ({creator.status}) - existing data reused, not retried; "
        "use Refresh Profile or Retry in its details"
    )


def duplicate_upload_rows(upload_id: str, ingestion: IngestionResult) -> list[UploadRow]:
    return [
        UploadRow(
            upload_id=upload_id,
            row_number=dup.source_row,
            channel_name=dup.channel_name,
            channel_link=dup.channel_link,
            outcome="duplicate",
            message=f"Same channel as row {dup.duplicate_of_row}",
        )
        for dup in ingestion.duplicates
    ]


def upload_status_counts(db: Session, upload_ids: list[str]) -> dict[str, Counter]:
    """Creator status counts per upload, in a single grouped query."""
    if not upload_ids:
        return {}
    rows = db.execute(
        select(UploadItem.upload_id, Creator.status, func.count())
        .join(Creator, Creator.id == UploadItem.creator_id)
        .where(UploadItem.upload_id.in_(upload_ids))
        .group_by(UploadItem.upload_id, Creator.status)
    ).all()
    counts: dict[str, Counter] = defaultdict(Counter)
    for upload_id, status, count in rows:
        counts[upload_id][status] = count
    return counts


def _existing_creators(db: Session, ingestion: IngestionResult) -> dict[tuple[str, str], Creator]:
    """All already-known creators of this file in one query."""
    by_platform: dict[str, set[str]] = defaultdict(set)
    for row in ingestion.rows:
        by_platform[row.platform].add(row.normalized_identifier)
    conditions = [
        and_(Creator.platform == platform, Creator.normalized_identifier.in_(identifiers))
        for platform, identifiers in by_platform.items()
    ]
    creators = db.scalars(select(Creator).where(or_(*conditions))).all() if conditions else []
    return {(c.platform, c.normalized_identifier): c for c in creators}


def create_upload(db: Session, settings: Settings, filename: str, stored_filename: str | None, ingestion: IngestionResult) -> Upload:
    upload = Upload(
        filename=filename,
        stored_filename=stored_filename,
        duplicate_rows=ingestion.duplicate_rows,
        total_rows=len(ingestion.rows),
        status=UploadStatus.PROCESSING,
    )
    db.add(upload)
    db.flush()  # assigns upload.id for the saved file rows

    existing = _existing_creators(db, ingestion)
    resolved: list[tuple[int, object, Creator, bool]] = []
    new_creators: list[Creator] = []
    cached = 0
    for position, row in enumerate(ingestion.rows, start=1):
        creator = existing.get(row.dedupe_key)
        from_cache = False
        note: str | None = None
        if creator is None:
            creator = Creator(
                channel_name=row.channel_name,
                platform=row.platform,
                channel_url=row.canonical_url,
                normalized_identifier=row.normalized_identifier,
                identifier_type=row.identifier_type,
                status=CreatorStatus.FAILED if row.error else CreatorStatus.PENDING,
                error_message=row.error,
                issues=[row.error] if row.error else None,
            )
            new_creators.append(creator)
        elif row.error:
            creator.status = CreatorStatus.FAILED
            creator.error_message = row.error
        else:
            # Already known: reuse the stored data as it is, however old - no platform or AI calls on upload.
            # Failed / partial creators are not retried here; they are refreshed from their details.
            from_cache = True
            cached += 1
            note = _reused_note(creator)
        resolved.append((position, row, creator, from_cache))
        db.add(
            UploadRow(
                upload_id=upload.id,
                row_number=row.source_row,
                channel_name=row.channel_name,
                channel_link=row.channel_link,
                outcome="invalid" if row.error else ("cached" if from_cache else "queued"),
                message=row.error or note,
            )
        )
    db.add_all(duplicate_upload_rows(upload.id, ingestion))

    # One batched INSERT for new creators (ids come back via RETURNING), then one for the items.
    db.add_all(new_creators)
    db.flush()
    db.add_all(
        UploadItem(
            upload_id=upload.id,
            creator_id=creator.id,
            position=position,
            source_row=row.source_row,
            from_cache=from_cache,
        )
        for position, row, creator, from_cache in resolved
    )
    db.flush()
    log_event(
        logger, logging.INFO, "upload_created",
        upload_id=upload.id, rows=len(ingestion.rows), duplicates=ingestion.duplicate_rows, cached=cached,
    )
    return upload
