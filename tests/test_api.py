import json
from email.message import Message
from io import BytesIO

import muse_reel.api as api
from muse_reel.database import FootageDatabase
from muse_reel.models import MediaMetadata


def _invoke_read_only_handler(path, database_path, token="secret"):
    handler = object.__new__(api._ReadOnlyHandler)
    handler.database_path = database_path
    handler.api_token = "secret"
    handler.headers = {"Authorization": f"Bearer {token}"}
    handler.path = path
    captured = {}
    handler._send_json = lambda status, payload: captured.update(
        status=status, payload=payload
    )
    handler.do_GET()
    return captured


def test_read_only_api_lists_projects_and_searches_tags(tmp_path):
    database_path = tmp_path / "index.sqlite"
    with FootageDatabase(database_path) as database:
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
                    "caption": "A worker cuts lumber with a power tool.",
                    "tags": ["wood cutting", "power tool"],
                    "model": "local-model",
                    "source_mtime_ns": 100,
                }
            ],
        )

    projects = _invoke_read_only_handler(
        "/v1/projects", database_path
    )
    assert projects == {
        "status": 200,
        "payload": {"projects": [{"id": 1, "name": "Napoleon"}]},
    }

    result = _invoke_read_only_handler(
        "/v1/scenes/search?project=Napoleon&q=lumber", database_path
    )
    assert result["status"] == 200
    scene = result["payload"]["scenes"][0]
    assert scene["filename"] == "status.mov"
    assert scene["start_seconds"] == 8.0
    assert scene["tags"] == ["wood cutting", "power tool"]
    assert "/Users/" not in json.dumps(result)
    assert "/tmp/" not in json.dumps(result)


def test_read_only_api_requires_token_and_stays_loopback_only(tmp_path):
    database_path = tmp_path / "index.sqlite"
    result = _invoke_read_only_handler("/v1/projects", database_path, token="wrong")
    assert result["status"] == 401

    try:
        api.serve_read_only_api(database_path, api_token="secret", host="0.0.0.0")
    except ValueError as error:
        assert "localhost" in str(error)
    else:
        raise AssertionError("The development API should reject non-local binding")


def _invoke_index_sync(path, database_path, payload, token="sync-secret"):
    handler = object.__new__(api._ReadOnlyHandler)
    body = json.dumps(payload).encode("utf-8")
    handler.database_path = database_path
    handler.api_token = "api-secret"
    handler.sync_token = "sync-secret"
    handler.headers = Message()
    handler.headers["Authorization"] = f"Bearer {token}"
    handler.headers["Content-Type"] = "application/json"
    handler.headers["Content-Length"] = str(len(body))
    handler.rfile = BytesIO(body)
    handler.path = path
    captured = {}
    handler._send_json = lambda status, response: captured.update(
        status=status, payload=response
    )
    handler.do_POST()
    return captured


def test_index_sync_imports_only_searchable_metadata(tmp_path):
    database_path = tmp_path / "hosted.sqlite"
    payload = {
        "project": "Napoleon",
        "scenes": [
            {
                "media_key": "7",
                "filename": "status.mov",
                "segment_index": 2,
                "start_seconds": 8.0,
                "end_seconds": 16.0,
                "caption": "A worker cuts lumber.",
                "tags": ["lumber", "power tool"],
            }
        ],
    }

    result = _invoke_index_sync("/v1/admin/index", database_path, payload)

    assert result == {"status": 200, "payload": {"status": "ok", "scenes": 1}}
    with FootageDatabase(database_path) as database:
        project = database.get_project("Napoleon")
        scenes = database.search_scenes(project["id"], "lumber")
        assert len(scenes) == 1
        assert scenes[0]["filename"] == "status.mov"
        assert scenes[0]["file_path"].startswith("muse-index://")


def test_index_sync_requires_separate_token_and_valid_filenames(tmp_path):
    database_path = tmp_path / "hosted.sqlite"
    payload = {
        "project": "Napoleon",
        "scenes": [
            {
                "media_key": "7",
                "filename": "../status.mov",
                "segment_index": 1,
                "start_seconds": 0,
                "end_seconds": 8,
                "caption": "A worker cuts lumber.",
                "tags": ["lumber"],
            }
        ],
    }
    unauthorized = _invoke_index_sync(
        "/v1/admin/index", database_path, payload, token="wrong"
    )
    invalid_payload = _invoke_index_sync("/v1/admin/index", database_path, payload)

    assert unauthorized["status"] == 401
    assert invalid_payload["status"] == 400


def test_health_is_public_and_public_binding_requires_explicit_opt_in(tmp_path, monkeypatch):
    health = _invoke_read_only_handler("/health", tmp_path / "index.sqlite", token="")
    assert health == {"status": 200, "payload": {"status": "ok"}}

    captured = {}

    class FakeServer:
        def __init__(self, address, handler):
            captured["address"] = address
            captured["handler"] = handler

        def serve_forever(self):
            raise KeyboardInterrupt

        def server_close(self):
            captured["closed"] = True

    monkeypatch.setattr(api, "ThreadingHTTPServer", FakeServer)
    api.serve_read_only_api(
        tmp_path / "index.sqlite",
        api_token="api-secret",
        sync_token="sync-secret",
        host="0.0.0.0",
        port=10000,
        allow_public_bind=True,
    )
    assert captured["address"] == ("0.0.0.0", 10000)
    assert captured["closed"]
