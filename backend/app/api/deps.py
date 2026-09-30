from typing import Literal

from fastapi import HTTPException, Query, Request

from app.services.creator_query import CreatorFilters, SortKey
from app.services.orchestrator import EnrichmentOrchestrator


def get_orchestrator(request: Request) -> EnrichmentOrchestrator:
    orchestrator = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Processing engine is not ready")
    return orchestrator


def creator_filters(
    q: str | None = Query(default=None, max_length=200),
    platform: Literal["youtube", "instagram", "invalid", "unsupported"] | None = None,
    genre: str | None = Query(default=None, max_length=80),
    language: str | None = Query(default=None, max_length=40),
    sentiment: str | None = Query(default=None, max_length=20),
    status: Literal["Pending", "Processing", "Completed", "Partial", "Failed"] | None = None,
    upload_id: str | None = Query(default=None, max_length=32, pattern=r"^[a-f0-9]{32}$"),
    sort_by: SortKey | None = None,
    sort_dir: Literal["asc", "desc"] = "desc",
) -> CreatorFilters:
    return CreatorFilters(
        q=q or None, platform=platform, genre=genre or None, language=language or None,
        sentiment=sentiment or None, status=status, upload_id=upload_id, sort_by=sort_by, sort_dir=sort_dir,
    )
