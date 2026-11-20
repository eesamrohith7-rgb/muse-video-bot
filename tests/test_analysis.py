import base64
import json
import subprocess

import pytest

import muse_reel.analysis as analysis
from muse_reel.database import FootageDatabase
from muse_reel.models import MediaMetadata


def test_detect_segments_uses_scene_changes_and_caps_long_segments(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.mov"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(analysis.shutil, "which", lambda name: "/mock/ffmpeg")

    def fake_run(command, **kwargs):
        assert command[command.index("-vf") + 1] == r"select=gt(scene\,0.35),showinfo"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout="",
            stderr=(
                "pts_time:5.000\n"
                "pts_time:5.500\n"  # ignored because it is too close to the prior cut
                "pts_time:19.000\n"
            ),
        )

    monkeypatch.setattr(analysis.subprocess, "run", fake_run)

    segments = analysis.detect_segments(video_path, 27.0)

    assert segments == [
        (0.0, 5.0),
        (5.0, 13.0),
        (13.0, 19.0),
        (19.0, 27.0),
    ]


def test_detect_segments_handles_video_without_cuts(tmp_path, monkeypatch):
    video_path = tmp_path / "clip.mov"
    video_path.write_bytes(b"video")
    monkeypatch.setattr(analysis.shutil, "which", lambda name: "/mock/ffmpeg")
    monkeypatch.setattr(
        analysis.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 0, stdout="", stderr=""
        ),
    )

    assert analysis.detect_segments(video_path, 18.0) == [
        (0.0, 8.0),
        (8.0, 16.0),
        (16.0, 18.0),
    ]


def test_describe_frame_sends_image_only_to_local_ollama(tmp_path, monkeypatch):
    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"sample-image")
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            content = json.dumps(
                {"caption": "A worker installs wall framing.", "tags": ["Framing", "lumber"]}
            )
            return json.dumps({"message": {"content": content}}).encode()

    def fake_urlopen(request, timeout):
        captured["request"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr(analysis, "urlopen", fake_urlopen)

    caption, tags = analysis.describe_frame(image_path)

    assert caption == "A worker installs wall framing."
    assert tags == ["framing", "lumber"]
    assert base64.b64decode(captured["request"]["messages"][0]["images"][0]) == b"sample-image"
    assert captured["request"]["model"] == analysis.DEFAULT_VISION_MODEL
    assert captured["request"]["stream"] is False


def test_describe_frame_rejects_remote_endpoint(tmp_path):
    image_path = tmp_path / "frame.jpg"
    image_path.write_bytes(b"sample-image")

    with pytest.raises(ValueError, match="local Ollama"):
        analysis.describe_frame(image_path, endpoint="https://example.com/api/chat")


def test_scene_search_and_stale_scene_invalidation(tmp_path):
    with FootageDatabase(tmp_path / "index.sqlite") as database:
        project_id = database.create_project("House")
        media = MediaMetadata(
            filename="wall.mov",
            file_path="/footage/wall.mov",
            file_size=123,
            media_type="video",
            duration_seconds=12.0,
            width=1080,
            height=1920,
            fps=25.0,
            creation_date="2026-01-01T00:00:00",
            file_format="mov",
            mtime_ns=100,
        )
        database.upsert_media(project_id, media)
        media_row = database.list_media(project_id, "video")[0]
        database.replace_scene_segments(
            media_row["id"],
            [
                {
                    "segment_index": 1,
                    "start_seconds": 0.0,
                    "end_seconds": 8.0,
                    "thumbnail_path": "/preview/1.jpg",
                    "caption": "Wall framing is underway.",
                    "tags": ["framing", "lumber"],
                    "model": analysis.DEFAULT_VISION_MODEL,
                    "source_mtime_ns": 100,
                    "threshold": 0.35,
                }
            ],
        )

        found = database.search_scenes(project_id, "lumber")
        assert len(found) == 1
        assert found[0]["filename"] == "wall.mov"
        assert json.loads(found[0]["tags_json"]) == ["framing", "lumber"]
        assert database.scenes_are_current(
            media_row["id"], 100, 0.35, analysis.DEFAULT_VISION_MODEL, 8.0
        )
        assert not database.scenes_are_current(
            media_row["id"], 100, 0.35, analysis.DEFAULT_VISION_MODEL, 4.0
        )

        changed = MediaMetadata(
            **{**media.__dict__, "mtime_ns": 200}
        )
        database.upsert_media(project_id, changed)
        assert database.search_scenes(project_id, "lumber") == []
        assert not database.scenes_are_current(
            media_row["id"], 200, 0.35, analysis.DEFAULT_VISION_MODEL, 8.0
        )
