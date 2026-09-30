import math

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session, defer

from app.api.deps import creator_filters, get_orchestrator
from app.config.settings import get_settings
from app.models.db import get_db, get_read_db
from app.models.entities import Creator, CreatorStatus, UploadItem
from app.schemas.api import (
    ActionResponse,
    BulkDeleteIn,
    CreatorCreateIn,
    CreatorDetailOut,
    CreatorListResponse,
    CreatorOut,
    CreatorUpdateIn,
    DeleteResult,
    FacetsResponse,
)
from app.services.creator_service import (
    CreatorBusy,
    CreatorConflict,
    InvalidLink,
    create_creator,
    delete_creators,
    update_creator,
)
from app.services.creator_query import CreatorFilters, build_creator_query
from app.services.orchestrator import EnrichmentOrchestrator

router = APIRouter(prefix="/api/creators", tags=["creators"])

PAGE_SIZES = (10, 25, 50, 100)
_TABLE_DEFERRED = (Creator.content_sample, Creator.provenance, Creator.issues, Creator.evidence_topics)


@router.get("", response_model=CreatorListResponse)
def list_creators(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10),
    filters: CreatorFilters = Depends(creator_filters),
    db: Session = Depends(get_read_db),
):
    if page_size not in PAGE_SIZES:
        raise HTTPException(status_code=422, detail=f"page_size must be one of {PAGE_SIZES}")
    # Page rows + total in ONE query (window count); large JSON columns are not needed for the table.
    stmt = (
        build_creator_query(filters)
        .add_columns(func.count().over().label("total_count"))
        .options(*(defer(column) for column in _TABLE_DEFERRED))
    )
    rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    if rows:
        total = rows[0].total_count
    else:
        total = db.scalar(select(func.count()).select_from(build_creator_query(filters).order_by(None).subquery())) or 0
        if total and page > 1:  # requested page is past the end: serve the last page
            page = max(1, math.ceil(total / page_size))
            rows = db.execute(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    total_pages = max(1, math.ceil(total / page_size))
    return CreatorListResponse(
        items=[CreatorOut.model_validate(row[0]) for row in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/facets", response_model=FacetsResponse)
def creator_facets(
    upload_id: str | None = Query(default=None, max_length=32, pattern=r"^[a-f0-9]{32}$"),
    db: Session = Depends(get_read_db),
):
    # One query for all filter values (distinct combinations are few), instead of one per column.
    stmt = select(Creator.platform, Creator.genre, Creator.language, Creator.sentiment, Creator.status).distinct()
    if upload_id:
        stmt = stmt.join(UploadItem, UploadItem.creator_id == Creator.id).where(UploadItem.upload_id == upload_id)
    columns: list[set[str]] = [set() for _ in range(5)]
    for row in db.execute(stmt):
        for index, value in enumerate(row):
            if value:
                columns[index].add(value)
    platforms, genres, languages, sentiments, statuses = (sorted(values) for values in columns)
    return FacetsResponse(
        platforms=platforms, genres=genres, languages=languages, sentiments=sentiments, statuses=statuses
    )


def _get_creator(db: Session, creator_id: int) -> Creator:
    creator = db.get(Creator, creator_id)
    if creator is None:
        raise HTTPException(status_code=404, detail="Creator not found")
    return creator


@router.get("/{creator_id}", response_model=CreatorDetailOut)
def get_creator(creator_id: int, db: Session = Depends(get_read_db)):
    return CreatorDetailOut.model_validate(_get_creator(db, creator_id))


@router.post("/{creator_id}/retry", response_model=ActionResponse, status_code=202)
async def retry_creator(
    creator_id: int,
    db: Session = Depends(get_db),
    orchestrator: EnrichmentOrchestrator = Depends(get_orchestrator),
):
    """Re-run the full pipeline for this creator only (bypasses the cache)."""
    await run_in_threadpool(_queue_creator, db, creator_id, False)
    orchestrator.start_retry(creator_id)
    return ActionResponse(creator_id=creator_id, status=CreatorStatus.PENDING, message="Creator queued for refresh")


@router.post("/{creator_id}/reanalyze", response_model=ActionResponse, status_code=202)
async def reanalyze_creator(
    creator_id: int,
    db: Session = Depends(get_db),
    orchestrator: EnrichmentOrchestrator = Depends(get_orchestrator),
):
    """Re-run only the AI analysis using the stored content sample."""
    if not get_settings().ai_configured:
        raise HTTPException(status_code=400, detail="AI analysis is not configured. Set GROQ_API_KEY in backend/.env")
    await run_in_threadpool(_queue_creator, db, creator_id, True)
    orchestrator.start_reanalyze(creator_id)
    return ActionResponse(creator_id=creator_id, status=CreatorStatus.PENDING, message="Creator queued for re-analysis")


def _queue_creator(db: Session, creator_id: int, reanalyze: bool) -> None:
    creator = _get_creator(db, creator_id)
    if creator.status in CreatorStatus.ACTIVE:
        raise HTTPException(status_code=409, detail="This creator is already being processed")
    if creator.platform not in ("youtube", "instagram"):
        detail = (
            "There is no content to analyse for an invalid link" if reanalyze
            else "This row has an invalid or unsupported link. Fix the link in your file and upload it again."
        )
        raise HTTPException(status_code=400, detail=detail)
    creator.status = CreatorStatus.PENDING
    db.commit()


# ------------------------------------------------------------------------ CRUD
def _to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, (CreatorConflict, CreatorBusy)):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, LookupError):
        return HTTPException(status_code=404, detail=str(exc))
    return HTTPException(status_code=422, detail=str(exc))


_CRUD_ERRORS = (CreatorConflict, CreatorBusy, InvalidLink, ValueError, LookupError)


def _detail(db: Session, creator_id: int) -> CreatorDetailOut:
    return CreatorDetailOut.model_validate(_get_creator(db, creator_id))


@router.post("", response_model=CreatorDetailOut, status_code=201)
async def add_creator(
    body: CreatorCreateIn,
    db: Session = Depends(get_db),
    orchestrator: EnrichmentOrchestrator = Depends(get_orchestrator),
):
    """Add a single creator without an Excel file; enrichment starts immediately."""

    def work() -> int:
        try:
            creator = create_creator(db, body.channel_name, body.channel_link, body.upload_id)
            db.commit()
            return creator.id
        except _CRUD_ERRORS as exc:
            db.rollback()
            raise _to_http(exc) from exc

    creator_id = await run_in_threadpool(work)
    orchestrator.start_retry(creator_id)
    return await run_in_threadpool(_detail, db, creator_id)


@router.post("/bulk-delete", response_model=DeleteResult)
def bulk_delete_creators(body: BulkDeleteIn, db: Session = Depends(get_db)):
    deleted = delete_creators(db, list(dict.fromkeys(body.ids)))
    db.commit()
    return DeleteResult(deleted=deleted, message=f"Deleted {deleted} creator{'s' if deleted != 1 else ''}")


@router.patch("/{creator_id}", response_model=CreatorDetailOut)
async def edit_creator(
    creator_id: int,
    body: CreatorUpdateIn,
    db: Session = Depends(get_db),
    orchestrator: EnrichmentOrchestrator = Depends(get_orchestrator),
):
    """Edit the name and/or link. A new link clears old data and re-runs enrichment."""
    if body.channel_name is None and body.channel_link is None:
        raise HTTPException(status_code=422, detail="Nothing to update")

    def work() -> bool:
        creator = _get_creator(db, creator_id)
        try:
            changed = update_creator(db, creator, body.channel_name, body.channel_link)
            db.commit()
            return changed
        except _CRUD_ERRORS as exc:
            db.rollback()
            raise _to_http(exc) from exc

    if await run_in_threadpool(work):
        orchestrator.start_retry(creator_id)
    return await run_in_threadpool(_detail, db, creator_id)


@router.delete("/{creator_id}", response_model=DeleteResult)
def remove_creator(creator_id: int, db: Session = Depends(get_db)):
    creator = _get_creator(db, creator_id)
    name = creator.channel_name
    delete_creators(db, [creator_id])
    db.commit()
    return DeleteResult(deleted=1, message=f"Deleted {name}")
