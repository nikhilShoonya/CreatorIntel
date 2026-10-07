"""Housekeeping: uploaded files are deleted after the retention period while their rows stay in the
database (and on the website); Video Performance export; Meta token health."""

import asyncio
import io
import time
from datetime import timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.models.entities import Upload, UploadRow
from app.services import meta_token
from app.services.housekeeping import cleanup_old_upload_files
from app.services.http_client import HttpRequestError
from app.video_performance import repository as vt_repo
from app.video_performance.models import VtUpload


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def _age(model, upload_id: str, days: int) -> None:
    with session_scope() as db:
        upload = db.get(model, upload_id)
        upload.created_at = upload.created_at - timedelta(days=days)


def _stored_path(model, upload_id: str):
    with session_scope() as db:
        return get_settings().upload_dir / db.get(model, upload_id).stored_filename


def test_creator_analytics_file_deleted_but_rows_kept(client):
    csv = b"Channel Name,Channel Link\nBroken,not a link\nUnsupported,https://twitter.com/x\n"
    upload = client.post("/api/uploads", files={"file": ("keep.csv", csv, "text/csv")}).json()
    path = _stored_path(Upload, upload["id"])
    assert path.exists()
    listed = next(u for u in client.get("/api/uploads").json() if u["id"] == upload["id"])
    assert listed["file_delete_after"] is not None and listed["file_deleted_at"] is None

    _age(Upload, upload["id"], 40)
    assert cleanup_old_upload_files(get_settings()) >= 1
    assert not path.exists()

    data = client.get(f"/api/uploads/{upload['id']}/rows").json()
    assert data["upload"]["file_deleted_at"] is not None
    assert [(r["row_number"], r["channel_name"], r["outcome"]) for r in data["rows"]] == [
        (2, "Broken", "invalid"), (3, "Unsupported", "invalid"),
    ]
    export = client.get(f"/api/uploads/{upload['id']}/rows/export", params={"format": "excel"})
    assert export.status_code == 200
    frame = pd.read_excel(io.BytesIO(export.content))
    assert list(frame["Channel Name"]) == ["Broken", "Unsupported"]


def test_old_upload_without_saved_rows_is_backfilled_before_delete(client):
    csv = b"Channel Name,Channel Link\nOld row,not a link\n"
    upload = client.post("/api/uploads", files={"file": ("old.csv", csv, "text/csv")}).json()
    with session_scope() as db:  # simulate an upload made before rows were stored
        db.execute(delete(UploadRow).where(UploadRow.upload_id == upload["id"]))
    _age(Upload, upload["id"], 40)
    cleanup_old_upload_files(get_settings())
    with session_scope() as db:
        rows = db.scalars(select(UploadRow).where(UploadRow.upload_id == upload["id"])).all()
        assert [r.channel_name for r in rows] == ["Old row"]
        assert db.get(Upload, upload["id"]).file_deleted_at is not None


def test_video_performance_rows_cleanup_and_export(client):
    csv = b"Creator Name,Video Link\nBroken,not a link\n"
    result = client.post("/api/video-performance/uploads", files={"file": ("v.csv", csv, "text/csv")}).json()
    upload_id = result["upload"]["id"]
    path = _stored_path(VtUpload, upload_id)
    assert path.exists()
    _age(VtUpload, upload_id, 40)
    assert vt_repo.cleanup_upload_files(get_settings().upload_dir, 30) >= 1
    assert not path.exists()
    data = client.get(f"/api/video-performance/uploads/{upload_id}/rows").json()
    assert data["upload"]["file_deleted_at"] is not None
    assert data["rows"][0]["creator_name"] == "Broken" and data["rows"][0]["status"] == "invalid"
    assert client.get(f"/api/video-performance/uploads/{upload_id}/rows/export", params={"format": "csv"}).status_code == 200

    export = client.get("/api/video-performance/exports/excel", params={"layout": "performance"})
    assert export.status_code == 200
    frame = pd.read_excel(io.BytesIO(export.content))
    assert "Current Views" in frame.columns and "Views Gained" in frame.columns


def test_retention_zero_keeps_files(client):
    settings = get_settings().model_copy(update={"upload_file_retention_days": 0})
    assert cleanup_old_upload_files(settings) == 0


@pytest.mark.parametrize(
    "response, state",
    [
        ({"data": {"is_valid": True, "expires_at": int(time.time()) + 3 * 86400, "scopes": ["instagram_basic"]}}, "expiring"),
        ({"data": {"is_valid": True, "expires_at": int(time.time()) + 50 * 86400,
                   "scopes": list(meta_token.REQUIRED_PERMISSIONS)}}, "ok"),
        ({"data": {"is_valid": True, "expires_at": 0, "scopes": []}}, "ok"),
        ({"data": {"is_valid": False, "error": {"message": "Session has expired on Tuesday"}}}, "expired"),
        (HttpRequestError("x", status_code=400, payload={"error": {"code": 200, "message": "API access blocked."}}), "invalid"),
    ],
)
def test_token_status_states(monkeypatch, response, state):
    async def fake_request_json(*_args, **_kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(meta_token, "request_json", fake_request_json)
    meta_token._cache = None
    settings = get_settings().model_copy(update={"meta_access_token": "EAAtesttoken", "meta_app_id": "1", "meta_app_secret": "s"})
    status = asyncio.run(meta_token.instagram_token_status(settings, refresh=True))
    assert status.state == state
    if state == "expiring":
        assert status.days_left in (2, 3) and "instagram_manage_insights" in status.missing_permissions


def test_token_status_wrong_token_type():
    meta_token._cache = None
    settings = get_settings().model_copy(update={"meta_access_token": "IGAAexample"})
    assert asyncio.run(meta_token.instagram_token_status(settings, refresh=True)).state == "wrong_type"
