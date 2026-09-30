from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import creator_filters
from app.models.db import get_read_db
from app.services.creator_query import CreatorFilters, build_creator_query
from app.services.export_service import to_csv_bytes, to_excel_bytes
from app.utils.time import utcnow

router = APIRouter(prefix="/api/exports", tags=["exports"])

MAX_EXPORT_ROWS = 20000


def _load(db: Session, filters: CreatorFilters):
    return db.scalars(build_creator_query(filters).limit(MAX_EXPORT_ROWS)).all()


def _filename(ext: str) -> str:
    return f"creatorintel_export_{utcnow().strftime('%Y%m%d_%H%M')}.{ext}"


@router.get("/excel")
async def export_excel(filters: CreatorFilters = Depends(creator_filters), db: Session = Depends(get_read_db)):
    content = await run_in_threadpool(lambda: to_excel_bytes(_load(db, filters)))
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{_filename("xlsx")}"'},
    )


@router.get("/csv")
async def export_csv(filters: CreatorFilters = Depends(creator_filters), db: Session = Depends(get_read_db)):
    content = await run_in_threadpool(lambda: to_csv_bytes(_load(db, filters)))
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_filename("csv")}"'},
    )
