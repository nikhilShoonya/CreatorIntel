"""Owner recovery uses supplied metadata and official Business Discovery only; all HTTP is mocked."""

import asyncio
import io
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select

from app.config.settings import get_settings
from app.main import app
from app.models.db import session_scope
from app.video_performance import queries, repository as repo
from app.video_performance.ingest import parse_import
from app.video_performance.models import (
    CreatorTrackingStatus, VideoStatus, VtCreator, VtUpload, VtUploadRow, VtVideo, VtViewSnapshot,
)
from app.video_performance.platforms import InstagramVideoClient, PlatformError
from app.video_performance.tracker import OWNER_SEARCH_MAX_CALLS, VideoTracker
from app.video_performance.urls import parse_instagram_handle, parse_video_link
from tests.test_vp_reel_owner import FakeInstagram, NoSentiment, media


@pytest.fixture(scope="module")
def api():
    with TestClient(app) as client:
        yield client


@pytest.fixture(autouse=True)
def cleanup(api):
    with session_scope() as db:
        before = {model: set(db.scalars(select(model.id))) for model in (VtVideo, VtCreator, VtUpload)}
    yield
    with session_scope() as db:
        new_videos = set(db.scalars(select(VtVideo.id))) - before[VtVideo]
    repo.delete_videos(list(new_videos))
    with session_scope() as db:
        for model in (VtCreator, VtUpload):
            for row in db.scalars(select(model).where(model.id.not_in(before[model]))):
                db.delete(row)


def code():
    return "OwnerFix" + uuid.uuid4().hex[:12]


def video(shortcode=None, **values):
    shortcode = shortcode or code()
    with session_scope() as db:
        row = VtVideo(platform="instagram", video_identifier=shortcode,
                      video_url=f"https://www.instagram.com/reel/{shortcode}/", status=values.pop("status", VideoStatus.FAILED),
                      **values)
        db.add(row)
        db.flush()
        return row.id, shortcode


def stored(video_id):
    with session_scope() as db:
        row = db.get(VtVideo, video_id)
        db.expunge(row)
        return row


def creator(username="linked_owner", **values):
    with session_scope() as db:
        row = VtCreator(platform="instagram", creator_name="Display Name", username=username,
                        channel_url=f"https://www.instagram.com/{username}/", normalized_identifier=username,
                        status=values.pop("status", CreatorTrackingStatus.ACTIVE), **values)
        db.add(row)
        db.flush()
        return row.id


def tracker(instagram):
    return VideoTracker(get_settings(), instagram=instagram, sentiment=NoSentiment())


@pytest.mark.parametrize("url,owner", [
    ("https://instagram.com/reel/AbCdE_123-/", None),
    ("https://www.instagram.com/reel/AbCdE_123-?igsh=abc", None),
    ("instagram.com/reels/AbCdE_123-///", None),
    ("https://m.instagram.com/p/AbCdE_123-/", None),
    ("https://instagr.am/tv/AbCdE_123-/", None),
    ("https://instagram.com/Posting.Owner/reel/AbCdE_123-/?utm_source=x", "posting.owner"),
])
def test_url_preserves_shortcode_and_only_explicit_owner(url, owner):
    parsed = parse_video_link(url)
    assert parsed.identifier == "AbCdE_123-" and parsed.owner_username == owner
    assert parsed.url == "https://www.instagram.com/reel/AbCdE_123-/"


@pytest.mark.parametrize("value", [
    "https://instagram.com.evil.example/real_owner", "https://evil.example/instagram.com/real_owner",
    "https://instagram.com@evil.example/real_owner", "https://instagram.com/reel/AbCdE_123-/",
    "https://instagram.com/p/AbCdE_123-/", "https://instagram.com/accounts/login/",
    "https://instagram.com/owner/unrelated/path", "https://[", "@bad..name", "@@owner", "reel", "None",
])
def test_invalid_or_ownerless_metadata_is_not_a_username(value):
    assert parse_instagram_handle(value) is None


@pytest.mark.parametrize("header", ["Username", "Instagram Username", "Profile URL", "Creator Link", "Owner Username"])
def test_recognized_owner_columns(header):
    result = parse_import("owners.csv", f"Video Link,{header}\nhttps://instagram.com/reel/AbCdE_123-/,@Real.Owner\n".encode(), 100_000)
    assert result.rows[0].owner_username == "real.owner"


@pytest.mark.parametrize("owner_column,formula", [("Profile Link", False), ("Creator Name", False), ("Instagram Profile URL", True)])
def test_workbook_hyperlink_targets_supply_video_and_owner(owner_column, formula):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Title above headers"])
    sheet.append(["Video Link", owner_column])
    sheet.append(["Watch this Reel", "Creator display name"])
    sheet["A3"].hyperlink = "https://instagram.com/reel/AbCdE_123-/?igsh=x"
    if formula:
        sheet["B3"] = '=HYPERLINK("https://instagram.com/Real.Owner/", "Creator display name")'
    else:
        sheet["B3"].hyperlink = "https://instagram.com/Real.Owner/"
    buffer = io.BytesIO()
    workbook.save(buffer)
    row = parse_import("owners.xlsx", buffer.getvalue(), 100_000).rows[0]
    assert row.status == "pending" and row.parsed.identifier == "AbCdE_123-"
    assert row.owner_username == "real.owner" and row.row == 3


def test_conflicting_columns_are_preserved_as_invalid_upload_row():
    result = parse_import("owners.csv", b"Video Link,Username,Profile URL\nhttps://instagram.com/reel/AbCdE12345/,alpha,https://instagram.com/beta/\n", 100_000)
    row = result.rows[0]
    assert row.status == "invalid" and row.owner_username is None
    assert "Username=@alpha" in row.message and "Profile URL=@beta" in row.message
    upload_id, queued = repo.import_rows("owners.csv", None, result.rows)
    assert queued == []
    with session_scope() as db:
        persisted = db.scalar(select(VtUploadRow).where(VtUploadRow.upload_id == upload_id))
        assert persisted.status == "invalid" and "Conflicting" in persisted.message


def test_source_url_owner_conflict_is_not_silently_selected():
    row = parse_import("owners.csv", b"Video Link,Username\nhttps://instagram.com/alpha/reel/AbCdE12345/,beta\n", 100_000).rows[0]
    assert row.status == "invalid" and "source URL=@alpha" in row.message


def test_profile_hyperlink_friendly_label_is_not_treated_as_an_owner():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Video Link", "Profile URL"])
    sheet.append(["https://instagram.com/reel/AbCdE12345/", "Profile"])
    sheet["B2"].hyperlink = "https://instagram.com/real_owner/"
    buffer = io.BytesIO()
    workbook.save(buffer)
    row = parse_import("owners.xlsx", buffer.getvalue(), 100_000).rows[0]
    assert row.status == "pending" and row.owner_username == "real_owner"


def test_linked_creator_owner_recovers_and_persists_metrics(monkeypatch):
    creator_id = creator()
    vid, shortcode = video(creator_id=creator_id)
    instagram = FakeInstagram({"linked_owner": [media(shortcode, 1234)]})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: pytest.fail("linked creator should resolve without discovery"))
    assert asyncio.run(tracker(instagram).process_videos([vid])) == (1, 0)
    row = stored(vid)
    assert (row.owner_username, row.current_views, row.status, row.creator_id) == ("linked_owner", 1234, VideoStatus.TRACKING, creator_id)
    with session_scope() as db:
        assert db.scalar(select(VtViewSnapshot.views).where(VtViewSnapshot.video_id == vid)) == 1234


def test_exact_associated_upload_metadata_is_reused(monkeypatch):
    vid, shortcode = video()
    with session_scope() as db:
        upload = VtUpload(filename="original.xlsx")
        db.add(upload)
        db.flush()
        db.get(VtVideo, vid).upload_id = upload.id
        db.add_all([
            VtUploadRow(upload_id=upload.id, row_number=2, platform="instagram", video_link=f"https://instagram.com/reel/{shortcode}/",
                        username="actual_owner", status="added"),
            VtUploadRow(upload_id=upload.id, row_number=3, platform="instagram", video_link=f"https://instagram.com/reel/{shortcode}X/",
                        username="unrelated_owner", status="added"),
        ])
    instagram = FakeInstagram({"actual_owner": [media(shortcode, 500)]})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])
    assert asyncio.run(tracker(instagram).process_videos([vid])) == (1, 0)
    assert stored(vid).owner_username == "actual_owner" and instagram.calls == ["actual_owner"]


def test_reimport_recovers_existing_ownerless_video_and_preserves_relationships(api, monkeypatch):
    vid, shortcode = video(status=VideoStatus.UNSUPPORTED)
    with session_scope() as db:
        original = VtUpload(filename="original.csv")
        db.add(original)
        db.flush()
        original_id = original.id
        db.get(VtVideo, vid).upload_id = original_id
    started = []
    monkeypatch.setattr(app.state.video_tracker, "start_processing", lambda ids: started.append(ids))
    content = f"Video Link,Username\nhttps://instagram.com/reel/{shortcode}/,@Recovered.Owner\n".encode()
    response = api.post("/api/video-performance/uploads", files={"file": ("recovery.csv", content, "text/csv")})
    assert response.status_code == 201
    body = response.json()
    assert started == [[vid]] and body["upload"]["added"] == 0 and body["upload"]["already_tracked"] == 1
    assert "metrics retrieval queued" in body["rows"][0]["message"]
    row = stored(vid)
    assert (row.upload_id, row.owner_username, row.status) == (original_id, "recovered.owner", VideoStatus.PENDING)
    assert queries.upload_video_ids(body["upload"]["id"]) == [vid]
    instagram = FakeInstagram({"recovered.owner": [media(shortcode, 900)]})
    assert asyncio.run(tracker(instagram).process_videos([vid])) == (1, 0)
    assert stored(vid).status == VideoStatus.TRACKING


@pytest.mark.parametrize("incoming", ["", "not a valid handle", "conflicting_owner"])
def test_reimport_cannot_replace_verified_owner(incoming, caplog):
    vid, shortcode = video(owner_username="verified_owner", current_views=100, status=VideoStatus.TRACKING)
    result = parse_import("owners.csv", f"Video Link,Username\nhttps://instagram.com/reel/{shortcode}/,{incoming}\n".encode(), 100_000)
    _, queued = repo.import_rows("owners.csv", None, result.rows)
    assert queued == [] and stored(vid).owner_username == "verified_owner"
    if incoming == "conflicting_owner":
        assert "stored @verified_owner" in result.rows[0].message
        assert "vt_import_owner_conflict" in caplog.text


def test_reimport_duplicate_row_can_supply_missing_owner_and_paused_video_stays_paused():
    vid, shortcode = video()
    result = parse_import("owners.csv", f"Video Link,Username\nhttps://instagram.com/reel/{shortcode}/,\nhttps://instagram.com/reel/{shortcode}/,real_owner\n".encode(), 100_000)
    _, queued = repo.import_rows("owners.csv", None, result.rows)
    assert queued == [vid] and stored(vid).owner_username == "real_owner"
    paused, shortcode = video(status=VideoStatus.PAUSED)
    result = parse_import("owners.csv", f"Video Link,Username\nhttps://instagram.com/reel/{shortcode}/,real_owner\n".encode(), 100_000)
    _, queued = repo.import_rows("owners.csv", None, result.rows)
    assert queued == [] and stored(paused).owner_username == "real_owner" and stored(paused).status == VideoStatus.PAUSED


def test_verified_owner_is_kept_during_edits():
    vid, _ = video(owner_username="verified_owner", current_views=100, status=VideoStatus.TRACKING)
    assert not repo.update_video(vid, creator_name=None, parsed=None, owner_username="", tracking=None)
    with pytest.raises(repo.Conflict, match="verified owner"):
        repo.update_video(vid, creator_name=None, parsed=None, owner_username="other_owner", tracking=None)
    assert stored(vid).owner_username == "verified_owner"


def test_verified_exact_match_without_available_counts_still_protects_owner():
    vid, shortcode = video(owner_username="paged_owner")
    item = graph_item(shortcode, view_count=None, like_count=None, comments_count=None)
    assert asyncio.run(tracker(graph_client(lambda request: graph_response([item]))).process_videos([vid])) == (1, 0)
    assert stored(vid).status == VideoStatus.PARTIAL and stored(vid).current_views is None
    with pytest.raises(repo.Conflict, match="verified owner"):
        repo.update_video(vid, creator_name=None, parsed=None, owner_username="other_owner", tracking=None)
    assert repo.claim_videos([vid])[0]["owner_verified"] is True


def test_disagreeing_unverified_relationship_fails_without_api_call(monkeypatch, caplog):
    vid, _ = video(owner_username="source_owner", creator_id=creator("different_owner"))
    instagram = FakeInstagram({})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])
    assert asyncio.run(tracker(instagram).process_videos([vid])) == (0, 1)
    assert instagram.calls == []
    assert stored(vid).status == VideoStatus.FAILED and "Conflicting" in stored(vid).status_reason
    assert "linked creator=@different_owner" in caplog.text


def test_verified_owner_wins_conflict_with_visible_warning(monkeypatch):
    vid, shortcode = video(owner_username="verified_owner", current_views=100, creator_id=creator("different_owner"))
    instagram = FakeInstagram({"verified_owner": [media(shortcode, 200)]})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])
    assert asyncio.run(tracker(instagram).process_videos([vid])) == (1, 0)
    assert stored(vid).owner_username == "verified_owner"
    assert "verified stored owner retained" in stored(vid).status_reason


def test_explicit_correction_supersedes_bad_upload_metadata_without_erasing_it(monkeypatch):
    shortcode = code()
    result = parse_import("original.csv", f"Video Link,Username\nhttps://instagram.com/reel/{shortcode}/,wrong_owner\n".encode(), 100_000)
    upload_id, ids = repo.import_rows("original.csv", None, result.rows)
    vid = ids[0]
    with session_scope() as db:
        db.get(VtVideo, vid).status = VideoStatus.FAILED
    assert repo.update_video(vid, creator_name=None, parsed=None, owner_username="correct_owner", tracking=None)
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])
    assert asyncio.run(tracker(FakeInstagram({"correct_owner": [media(shortcode, 321)]})).process_videos([vid])) == (1, 0)
    with session_scope() as db:
        row = db.scalar(select(VtUploadRow).where(VtUploadRow.upload_id == upload_id))
        assert row.username == "wrong_owner" and "superseded by explicit edit" in row.message
    assert stored(vid).owner_username == "correct_owner"


def test_unavailable_unverified_account_does_not_prove_reel_unsupported(monkeypatch):
    vid, _ = video(owner_username="unavailable_owner")
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])
    assert asyncio.run(tracker(FakeInstagram({})).process_videos([vid])) == (0, 1)
    assert stored(vid).status == VideoStatus.FAILED and "owner unresolved" in stored(vid).status_reason


def graph_item(shortcode, **extra):
    return {"id": "17890000001", "permalink": f"https://www.instagram.com/reel/{shortcode}/", "media_type": "VIDEO",
            "media_product_type": "REELS", "view_count": 456, "like_count": 10, "comments_count": 1,
            "caption": "A useful public caption", **extra}


def graph_client(handler):
    return InstagramVideoClient(get_settings(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def graph_response(items, after=None):
    block = {"data": items}
    if after:
        block["paging"] = {"cursors": {"after": after}}  # Business Discovery need not return paging.next
    return httpx.Response(200, json={"business_discovery": {"id": "1784001", "username": "paged_owner", "media": block}})


def test_fallback_finds_exact_case_on_second_cursor_page(monkeypatch):
    vid, shortcode = video()
    calls = []

    def graph(request):
        fields = request.url.params["fields"]
        calls.append(fields)
        return graph_response([graph_item(shortcode)]) if ".after(CURSOR_ONE)" in fields else graph_response(
            [graph_item(shortcode.lower())], "CURSOR_ONE")

    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["paged_owner"])
    assert asyncio.run(tracker(graph_client(graph)).process_videos([vid])) == (1, 0)
    assert len(calls) == 2 and stored(vid).owner_username == "paged_owner" and stored(vid).current_views == 456


def test_scan_limit_is_incomplete_and_never_unsupported(monkeypatch):
    vid, _ = video(owner_username="paged_owner", current_views=100)
    instance = tracker(graph_client(lambda request: graph_response([graph_item("OtherCode1")], "MORE")))
    instance.settings = instance.settings.model_copy(update={"video_tracking_instagram_scan_pages": 1})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])
    assert asyncio.run(instance.process_videos([vid])) == (0, 1)
    row = stored(vid)
    assert row.status == VideoStatus.FAILED and "search incomplete" in row.status_reason
    assert row.current_views == 100


def test_genuine_no_match_describes_known_accounts_only(monkeypatch):
    vid, _ = video()
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["paged_owner"])
    assert asyncio.run(tracker(graph_client(lambda request: graph_response([]))).process_videos([vid])) == (0, 1)
    row = stored(vid)
    assert row.status == VideoStatus.FAILED
    assert "no exact match" in row.status_reason and "not a global Instagram search" in row.status_reason


def test_candidate_batch_rotates_to_account_beyond_first_thirty(monkeypatch):
    vid, shortcode = video()
    names = [f"owner_candidate_{n}" for n in range(35)]
    instagram = FakeInstagram({name: [media(shortcode, 700)] if i == 34 else [] for i, name in enumerate(names)})
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: names)
    instance = tracker(instagram)
    assert asyncio.run(instance.process_videos([vid])) == (0, 1)
    assert len(instagram.calls) == 30 and "search incomplete" in stored(vid).status_reason
    assert asyncio.run(instance.process_videos([vid])) == (1, 0)
    assert instagram.calls[-1] == names[34] and stored(vid).current_views == 700


def test_fallback_pagination_respects_total_call_budget(monkeypatch):
    vid, _ = video()
    calls = []

    def graph(request):
        calls.append(request.url.params["fields"])
        # Match username to the actual requested candidate, not a hardcoded unrelated profile.
        username = calls[-1].split("username(")[1].split(")")[0]
        response = graph_response([graph_item("OtherCode1")], f"CURSOR_{len(calls)}").json()
        response["business_discovery"]["username"] = username
        return httpx.Response(200, json=response)

    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [f"candidate_{n}" for n in range(50)])
    assert asyncio.run(tracker(graph_client(graph)).process_videos([vid])) == (0, 1)
    assert len(calls) == OWNER_SEARCH_MAX_CALLS
    assert "search incomplete" in stored(vid).status_reason


@pytest.mark.parametrize("error_code,expected_status,reason", [
    (190, VideoStatus.FAILED, "token"), (10, VideoStatus.FAILED, "permissions"),
    (4, VideoStatus.PENDING, "finding this reel's owner continues automatically"),
])
def test_discovery_auth_permissions_and_rate_limits_are_not_owner_failures(monkeypatch, error_code, expected_status, reason):
    vid, _ = video()
    calls = []

    def graph(request):
        calls.append(request)
        return httpx.Response(400, json={"error": {"code": error_code, "message": "sanitized test error"}})

    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: ["paged_owner", "unused_owner"])
    assert asyncio.run(tracker(graph_client(graph)).process_videos([vid])) == (0, 1)
    row = stored(vid)
    assert row.status == expected_status and reason in row.status_reason
    assert len(calls) == 1


def test_partial_page_error_keeps_found_reel_and_errors_for_missing_reel(monkeypatch):
    first, shortcode = video(owner_username="paged_owner")
    missing, _ = video(owner_username="paged_owner")

    def graph(request):
        if ".after(" in request.url.params["fields"]:
            return httpx.Response(400, json={"error": {"code": 190, "message": "expired"}})
        return graph_response([graph_item(shortcode)], "NEXT")

    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])
    assert asyncio.run(tracker(graph_client(graph)).process_videos([first, missing])) == (1, 1)
    assert stored(first).current_views == 456 and stored(first).status == VideoStatus.TRACKING
    assert stored(missing).status == VideoStatus.FAILED and "token" in stored(missing).status_reason


def test_only_exact_non_video_media_is_unsupported(monkeypatch):
    vid, shortcode = video(owner_username="paged_owner")
    client = graph_client(lambda request: graph_response([graph_item(shortcode, media_type="IMAGE", media_product_type="FEED")]))
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])
    assert asyncio.run(tracker(client).process_videos([vid])) == (0, 1)
    assert stored(vid).status == VideoStatus.UNSUPPORTED and "exact matching" in stored(vid).status_reason


def test_view_count_field_fallback_retries_same_page_and_missing_views_are_partial():
    vid, shortcode = video(owner_username="paged_owner")
    calls = []

    def graph(request):
        fields = request.url.params["fields"]
        calls.append(fields)
        if "view_count" in fields:
            return httpx.Response(400, json={"error": {"code": 100, "message": "Unknown field view_count"}})
        item = graph_item(shortcode)
        del item["view_count"]
        return graph_response([item])

    instance = tracker(graph_client(graph))
    instance.settings = instance.settings.model_copy(update={"video_tracking_instagram_scan_pages": 1})
    assert asyncio.run(instance.process_videos([vid])) == (1, 0)
    assert len(calls) == 2 and stored(vid).status == VideoStatus.PARTIAL and stored(vid).current_views is None
    assert stored(vid).owner_username == "paged_owner"


def test_request_budget_also_bounds_http_retries(monkeypatch):
    from app.services import http_client

    calls = []

    def graph(request):
        calls.append(request)
        return httpx.Response(500, json={"error": {"message": "temporary failure"}})

    monkeypatch.setattr(http_client, "_backoff_seconds", lambda *args: 0)
    budget = [1]
    with pytest.raises(PlatformError, match="request limit reached") as error:
        asyncio.run(graph_client(graph).profile_media("paged_owner", request_budget=budget))
    assert error.value.code == PlatformError.OWNER_UNRESOLVED and budget == [0] and len(calls) == 1


def test_retry_bulk_refresh_and_api_acceptance_do_not_claim_metrics_success(api, monkeypatch):
    vid, shortcode = video()
    started = []
    monkeypatch.setattr(app.state.video_tracker, "start_processing", lambda ids: started.append(ids))
    response = api.post("/api/video-performance/videos/bulk", json={"ids": [vid], "action": "refresh"})
    assert response.status_code == 200 and "Queued 1" in response.json()["message"]
    assert started == [[vid]] and stored(vid).current_views is None and stored(vid).status == VideoStatus.PENDING
    monkeypatch.setattr(repo, "instagram_owner_candidates", lambda: [])
    assert asyncio.run(tracker(FakeInstagram({})).process_videos([vid])) == (0, 1)
    assert stored(vid).status == VideoStatus.FAILED and "owner unresolved" in stored(vid).status_reason
    creator_id = creator("retry_owner")
    with session_scope() as db:
        db.get(VtVideo, vid).creator_id = creator_id
    response = api.post(f"/api/video-performance/videos/{vid}/retry")
    assert response.status_code == 202
    assert asyncio.run(tracker(FakeInstagram({"retry_owner": [media(shortcode, 888)]})).process_videos([vid])) == (1, 0)
    assert stored(vid).status == VideoStatus.TRACKING and stored(vid).current_views == 888


def test_persistence_failure_is_not_reported_as_success(monkeypatch):
    vid, shortcode = video(owner_username="known_owner")
    monkeypatch.setattr(repo, "save_metrics", lambda *args: False)
    finished = []
    assert asyncio.run(tracker(FakeInstagram({"known_owner": [media(shortcode, 200)]})).process_videos([vid], lambda: finished.append(vid))) == (0, 1)
    assert finished == [vid] and stored(vid).current_views is None
