"""Create / update / delete operations for creators and uploads.

Only identity fields (name, link) are user-editable. Metrics and AI fields
always come from the pipeline, so changing a link clears them and re-enriches.
"""

import logging
from pathlib import Path

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.agents.platform_agent import PlatformResolver
from app.config.settings import Settings
from app.models.entities import Creator, CreatorStatus, Upload, UploadItem, UploadStatus
from app.utils.logging import log_event
from app.utils.text import clean_text

logger = logging.getLogger("creatorintel.creators")

# Everything the pipeline produces; reset when a creator's identity (link) changes.
PIPELINE_FIELDS = (
    "platform_id", "platform_display_name", "account_access", "followers_count", "subscriber_count",
    "average_views", "average_views_sample_count", "median_views", "average_views_long", "average_views_long_count",
    "average_views_short", "average_views_short_count", "top_video_title", "top_video_url",
    "top_video_views", "engagement_rate", "engagement_rate_basis", "engagement_sample_count",
    "genre", "sub_genre", "language", "secondary_language", "sentiment", "sentiment_score",
    "genre_confidence", "language_confidence", "sentiment_confidence", "evidence_topics",
    "issues", "provenance", "content_sample", "data_fetched_at", "analyzed_at", "error_message",
)


class CreatorConflict(Exception):
    def __init__(self, existing: Creator):
        super().__init__(f"This channel already exists as '{existing.channel_name}'")
        self.existing = existing


class CreatorBusy(Exception):
    pass


class InvalidLink(ValueError):
    pass


def _clean_name(name: str) -> str:
    value = clean_text(name)[:300]
    if not value:
        raise ValueError("Channel name cannot be empty")
    return value


def _resolve(link: str):
    parsed = PlatformResolver().resolve(clean_text(link)[:2048])
    if not parsed.is_supported:
        raise InvalidLink(parsed.error or "Channel link is not a valid YouTube or Instagram link")
    return parsed


def _find(db: Session, platform: str, identifier: str) -> Creator | None:
    return db.scalar(
        select(Creator).where(Creator.platform == platform, Creator.normalized_identifier == identifier)
    )


def create_creator(db: Session, channel_name: str, channel_link: str, upload_id: str | None = None) -> Creator:
    """Add one creator manually (optionally into an existing upload). Raises on duplicates."""
    name = _clean_name(channel_name)
    parsed = _resolve(channel_link)
    identifier = parsed.normalized_identifier or ""
    existing = _find(db, parsed.platform, identifier)
    if existing is not None:
        raise CreatorConflict(existing)

    upload = None
    if upload_id:
        upload = db.get(Upload, upload_id)
        if upload is None:
            raise LookupError("Upload not found")
        if upload.status == UploadStatus.PROCESSING:
            raise CreatorBusy("This upload is still being processed")

    creator = Creator(
        channel_name=name,
        platform=parsed.platform,
        channel_url=parsed.canonical_url or channel_link,
        normalized_identifier=identifier,
        identifier_type=parsed.identifier_type,
        status=CreatorStatus.PENDING,
    )
    db.add(creator)
    db.flush()
    if upload is not None:
        position = (db.scalar(select(func.max(UploadItem.position)).where(UploadItem.upload_id == upload.id)) or 0) + 1
        db.add(UploadItem(upload_id=upload.id, creator_id=creator.id, position=position, source_row=0))
        upload.total_rows = position
    log_event(logger, logging.INFO, "creator_created", creator_id=creator.id, platform=creator.platform, upload_id=upload_id)
    return creator


def update_creator(db: Session, creator: Creator, channel_name: str | None, channel_link: str | None) -> bool:
    """Edit name and/or link. Returns True when the link changed and the creator must be re-enriched."""
    if channel_name is not None:
        creator.channel_name = _clean_name(channel_name)

    if channel_link is None:
        return False
    parsed = _resolve(channel_link)
    identifier = parsed.normalized_identifier or ""
    if (parsed.platform, identifier) == (creator.platform, creator.normalized_identifier):
        return False  # same channel (e.g. only tracking parameters differ)
    if creator.status in CreatorStatus.ACTIVE:
        raise CreatorBusy("This creator is being processed; try again when it has finished")
    existing = _find(db, parsed.platform, identifier)
    if existing is not None and existing.id != creator.id:
        raise CreatorConflict(existing)

    creator.platform = parsed.platform
    creator.normalized_identifier = identifier
    creator.identifier_type = parsed.identifier_type
    creator.channel_url = parsed.canonical_url or channel_link
    for field in PIPELINE_FIELDS:
        setattr(creator, field, None)
    creator.genre_needs_review = False
    creator.status = CreatorStatus.PENDING
    log_event(logger, logging.INFO, "creator_link_changed", creator_id=creator.id, platform=creator.platform)
    return True


def delete_creators(db: Session, creator_ids: list[int]) -> int:
    """Delete creators (and their upload memberships). Returns how many were removed."""
    if not creator_ids:
        return 0
    db.execute(delete(UploadItem).where(UploadItem.creator_id.in_(creator_ids)))
    deleted = db.execute(delete(Creator).where(Creator.id.in_(creator_ids))).rowcount or 0
    log_event(logger, logging.INFO, "creators_deleted", count=deleted)
    return deleted


def delete_upload(db: Session, settings: Settings, upload: Upload, delete_creators_too: bool) -> int:
    """Delete an upload. Optionally delete its creators that belong to no other upload. Returns creators removed."""
    if upload.status == UploadStatus.PROCESSING:
        raise CreatorBusy("This upload is still being processed")
    creator_ids = list(db.scalars(select(UploadItem.creator_id).where(UploadItem.upload_id == upload.id)))
    db.execute(delete(UploadItem).where(UploadItem.upload_id == upload.id))

    removed = 0
    if delete_creators_too and creator_ids:
        still_used = set(db.scalars(select(UploadItem.creator_id).where(UploadItem.creator_id.in_(creator_ids))))
        orphans = [cid for cid in creator_ids if cid not in still_used]
        removed = delete_creators(db, orphans)

    if upload.stored_filename:
        path = settings.upload_dir / Path(upload.stored_filename).name
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log_event(logger, logging.WARNING, "upload_file_not_removed", upload_id=upload.id)
    db.execute(delete(Upload).where(Upload.id == upload.id))
    log_event(logger, logging.INFO, "upload_deleted", upload_id=upload.id, creators_removed=removed)
    return removed
