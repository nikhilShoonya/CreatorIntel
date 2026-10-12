"""Video Performance uploads: rename, delete (keeping or removing its videos), re-check, download the original file."""

import pytest
from fastapi.testclient import TestClient

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.video_performance.models import VtUpload, VtUploadRow, VtVideo, VtViewSnapshot

BASE = "/api/video-performance"


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


def _upload(api, name: str, links: list[str]) -> dict:
    csv = ("Video Link\n" + "\n".join(links) + "\n").encode()
    response = api.post(f"{BASE}/uploads", files={"file": (name, csv, "text/csv")})
    assert response.status_code == 201, response.text
    return response.json()["upload"]


def _video_ids(upload_id: str) -> list[int]:
    with session_scope() as db:
        return [v.id for v in db.query(VtVideo).filter(VtVideo.upload_id == upload_id)]


def test_rename_upload(api):
    upload = _upload(api, "rename_me.csv", ["https://youtu.be/crudRen0001"])
    renamed = api.patch(f"{BASE}/uploads/{upload['id']}", json={"filename": "  October campaign.xlsx "})
    assert renamed.status_code == 200 and renamed.json()["filename"] == "October campaign.xlsx"
    assert api.patch(f"{BASE}/uploads/{upload['id']}", json={"filename": "   "}).status_code == 422
    assert api.patch(f"{BASE}/uploads/nope", json={"filename": "x.csv"}).status_code == 404
    api.delete(f"{BASE}/uploads/{upload['id']}", params={"delete_videos": True})


def test_delete_upload_keeps_its_videos_by_default(api):
    upload = _upload(api, "keep_videos.csv", ["https://youtu.be/crudKeep001", "https://youtu.be/crudKeep002"])
    ids = _video_ids(upload["id"])
    assert len(ids) == 2
    stored = get_settings().upload_dir
    with session_scope() as db:
        path = stored / db.get(VtUpload, upload["id"]).stored_filename

    response = api.delete(f"{BASE}/uploads/{upload['id']}")
    assert response.status_code == 200 and "still tracked" in response.json()["message"]
    with session_scope() as db:
        assert db.get(VtUpload, upload["id"]) is None
        assert db.query(VtUploadRow).filter(VtUploadRow.upload_id == upload["id"]).count() == 0
        kept = [db.get(VtVideo, i) for i in ids]
        assert all(v is not None and v.upload_id is None for v in kept)  # still tracked, just not linked
    assert not path.exists()
    assert api.delete(f"{BASE}/uploads/{upload['id']}").status_code == 404
    with session_scope() as db:
        for vid in ids:
            db.delete(db.get(VtVideo, vid))


def test_delete_upload_with_its_videos(api):
    before = _upload(api, "older.csv", ["https://youtu.be/crudOld0001"])
    upload = _upload(api, "with_videos.csv", ["https://youtu.be/crudOld0001", "https://youtu.be/crudDel0001"])
    own = _video_ids(upload["id"])
    older = _video_ids(before["id"])
    assert len(own) == 1 and len(older) == 1
    with session_scope() as db:
        db.add(VtViewSnapshot(video_id=own[0], views=10))

    response = api.delete(f"{BASE}/uploads/{upload['id']}", params={"delete_videos": True})
    assert response.status_code == 200 and response.json()["message"] == "Upload deleted and 1 video"
    with session_scope() as db:
        assert db.get(VtVideo, own[0]) is None
        assert db.query(VtViewSnapshot).filter(VtViewSnapshot.video_id == own[0]).count() == 0
        assert db.get(VtVideo, older[0]) is not None  # already tracked before this file: never touched
    api.delete(f"{BASE}/uploads/{before['id']}", params={"delete_videos": True})


def test_refresh_and_download_original_file(api):
    upload = _upload(api, "download_me.csv", ["https://youtu.be/crudDl00001"])
    listed = next(u for u in api.get(f"{BASE}/uploads").json() if u["id"] == upload["id"])
    assert listed["file_available"] is True

    file = api.get(f"{BASE}/uploads/{upload['id']}/file")
    assert file.status_code == 200 and file.content == b"Video Link\nhttps://youtu.be/crudDl00001\n"
    assert "download_me.csv" in file.headers["content-disposition"]

    refreshed = api.post(f"{BASE}/uploads/{upload['id']}/refresh")
    assert refreshed.status_code == 202 and refreshed.json()["message"].startswith("Refreshing")
    assert api.post(f"{BASE}/uploads/nope/refresh").status_code == 404

    with session_scope() as db:  # a file removed earlier (old retention setting) cannot be downloaded
        path = get_settings().upload_dir / db.get(VtUpload, upload["id"]).stored_filename
    path.unlink()
    missing = api.get(f"{BASE}/uploads/{upload['id']}/file")
    assert missing.status_code == 404 and "no longer stored" in missing.json()["detail"]
    api.delete(f"{BASE}/uploads/{upload['id']}", params={"delete_videos": True})
