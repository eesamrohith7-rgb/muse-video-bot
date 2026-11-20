import json

import pytest

import muse_reel.sync as sync
from muse_reel.database import FootageDatabase
from muse_reel.models import MediaMetadata


def _indexed_database(path):
    database = FootageDatabase(path)
    project_id = database.create_project("Napoleon")
    media = MediaMetadata(
        filename="status.mov",
        file_path="/Users/example/private/status.mov",
        file_size=123,
        media_type="video",
        duration_seconds=27.2,
        width=1080,
        height=1920,
        fps=25.0,
        creation_date="2026-01-01T00:00:00",
        file_format="mov",
        mtime_ns=100,
    )
    database.upsert_media(project_id, media)
    media_id = database.list_media(project_id, "video")[0]["id"]
    database.replace_scene_segments(
        media_id,
        [
            {
                "segment_index": 1,
                "start_seconds": 8.0,
                "end_seconds": 16.0,
                "thumbnail_path": "/tmp/private-preview.jpg",
                "caption": "A worker cuts lumber.",
                "tags": ["lumber", "power tool"],
                "model": "local-model",
                "source_mtime_ns": 100,
            }
        ],
    )
    return database, project_id


def test_sync_sends_only_search_metadata_over_https(tmp_path, monkeypatch):
    database, project_id = _indexed_database(tmp_path / "local.sqlite")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"status":"ok","scenes":1}'

    class FakeOpener:
        def open(self, request, timeout):
            captured["url"] = request.full_url
            captured["body"] = json.loads(request.data)
            captured["auth"] = request.get_header("Authorization")
            captured["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(sync, "build_opener", lambda *_handlers: FakeOpener())
    try:
        count = sync.sync_project_index(
            database,
            project_id,
            endpoint="https://muse-reel-search.onrender.com/v1/admin/index",
            token="sync-secret",
        )
    finally:
        database.close()

    body = json.dumps(captured["body"])
    assert count == 1
    assert captured["auth"] == "Bearer sync-secret"
    assert captured["url"].startswith("https://")
    assert "/Users/" not in body
    assert "/tmp/" not in body
    assert "thumbnail_path" not in body
    assert captured["body"]["scenes"][0]["filename"] == "status.mov"


def test_sync_rejects_nonlocal_http_endpoints(tmp_path):
    database, project_id = _indexed_database(tmp_path / "local.sqlite")
    try:
        with pytest.raises(ValueError, match="HTTPS"):
            sync.sync_project_index(
                database,
                project_id,
                endpoint="http://example.com/v1/admin/index",
                token="sync-secret",
            )
    finally:
        database.close()
