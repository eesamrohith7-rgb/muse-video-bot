import os
import sqlite3
import subprocess
import sys

from PIL import Image

import muse_reel.cli as cli
import muse_reel.scanner as scanner
from muse_reel.database import FootageDatabase
from muse_reel.models import MediaMetadata
from muse_reel.scanner import find_media, scan_media


def test_finds_supported_media_recursively(tmp_path):
    (tmp_path / "nested").mkdir()
    (tmp_path / "clip.mp4").write_bytes(b"")
    (tmp_path / "nested" / "photo.png").touch()
    (tmp_path / "nested" / "ignored.txt").touch()

    found = {path.name for path in find_media(tmp_path)}
    assert found == {"clip.mp4", "photo.png"}


def test_scans_image_metadata(tmp_path):
    image_path = tmp_path / "frame.jpg"
    Image.new("RGB", (1920, 1080), color=(30, 60, 90)).save(image_path)

    metadata = scan_media(image_path)

    assert metadata.filename == "frame.jpg"
    assert metadata.media_type == "image"
    assert metadata.width == 1920
    assert metadata.height == 1080
    assert metadata.duration_seconds is None
    assert metadata.fps is None
    assert metadata.file_format == "JPG"
    assert metadata.mtime_ns == image_path.stat().st_mtime_ns


def test_traversal_errors_are_reported(tmp_path, monkeypatch):
    errors = []

    def fake_walk(root, onerror=None):
        onerror(PermissionError(f"cannot read {tmp_path / 'locked'}"))
        yield str(root), [], []

    monkeypatch.setattr(scanner.os, "walk", fake_walk)

    assert list(find_media(tmp_path, on_error=errors.append)) == []
    assert len(errors) == 1
    assert "cannot read" in str(errors[0])


def test_database_creation_rescan_and_migration(tmp_path):
    db_path = tmp_path / "footage.sqlite"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE source_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            media_type TEXT NOT NULL,
            duration_seconds REAL,
            width INTEGER,
            height INTEGER,
            fps REAL,
            creation_date TEXT,
            file_format TEXT,
            first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            last_updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.commit()
    connection.close()

    with FootageDatabase(db_path) as database:
        columns = {
            row["name"]
            for row in database.connection.execute(
                "PRAGMA table_info(source_media)"
            ).fetchall()
        }
        assert "mtime_ns" in columns

        project_id = database.create_project("Test Project")
        metadata = MediaMetadata(
            filename="frame.jpg",
            file_path="/footage/frame.jpg",
            file_size=100,
            media_type="image",
            duration_seconds=None,
            width=1920,
            height=1080,
            fps=None,
            creation_date="2024-01-02T03:04:05",
            file_format="JPG",
            mtime_ns=1000,
        )
        assert database.upsert_media(project_id, metadata)[0] == "indexed"
        assert database.upsert_media(project_id, metadata)[0] == "unchanged"

        replaced = MediaMetadata(
            filename=metadata.filename,
            file_path=metadata.file_path,
            file_size=metadata.file_size,
            media_type=metadata.media_type,
            duration_seconds=metadata.duration_seconds,
            width=metadata.width,
            height=metadata.height,
            fps=metadata.fps,
            creation_date=metadata.creation_date,
            file_format=metadata.file_format,
            mtime_ns=2000,
        )
        status, changed = database.upsert_media(project_id, replaced)
        assert status == "updated"
        assert changed

        row = database.connection.execute(
            "SELECT mtime_ns FROM source_media"
        ).fetchone()
        assert row["mtime_ns"] == 2000


def test_same_size_replacement_is_detected(tmp_path):
    db_path = tmp_path / "footage.sqlite"
    image_path = tmp_path / "frame.png"
    Image.new("RGB", (20, 20), color=(10, 20, 30)).save(
        image_path,
        compress_level=9,
    )
    first_mtime = image_path.stat().st_mtime_ns
    os.utime(image_path, ns=(first_mtime, first_mtime))

    with FootageDatabase(db_path) as database:
        project_id = database.create_project("Same Size")
        first = scan_media(image_path)
        assert database.upsert_media(project_id, first)[0] == "indexed"

        Image.new("RGB", (20, 20), color=(200, 100, 50)).save(
            image_path,
            compress_level=9,
        )
        replacement_mtime = first_mtime + 1_000
        os.utime(image_path, ns=(replacement_mtime, replacement_mtime))

        second = scan_media(image_path)
        assert second.file_size == first.file_size
        assert second.width == first.width
        assert second.height == first.height

        status, changed = database.upsert_media(project_id, second)
        assert status == "updated"
        assert changed


def test_video_metadata_uses_mocked_probe(tmp_path, monkeypatch):
    video_path = tmp_path / "house.mp4"
    video_path.write_bytes(b"test")

    monkeypatch.setattr(scanner.shutil, "which", lambda name: "/mock/ffprobe")

    def fake_run(command, capture_output, text, check):
        assert command[0] == "/mock/ffprobe"
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"format":{"duration":"12.5","format_name":"mov,mp4,m4a,3gp"},"streams":[{"codec_type":"video","width":1920,"height":1080,"avg_frame_rate":"30000/1001"}]}',
            stderr="",
        )

    monkeypatch.setattr(scanner.subprocess, "run", fake_run)

    metadata = scan_media(video_path)
    assert metadata.duration_seconds == 12.5
    assert metadata.width == 1920
    assert metadata.height == 1080
    assert abs(metadata.fps - 29.97) < 0.01
    assert metadata.file_format == "mov,mp4,m4a,3gp"


def test_missing_ffprobe_raises_clear_error(tmp_path, monkeypatch):
    video_path = tmp_path / "house.mp4"
    video_path.write_bytes(b"test")
    monkeypatch.setattr(scanner.shutil, "which", lambda name: None)

    try:
        scan_media(video_path)
    except RuntimeError as error:
        assert "brew install ffmpeg" in str(error)
    else:
        raise AssertionError("scan_media should fail when ffprobe is unavailable")


def test_cli_scan_indexes_and_reports_results(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "footage.sqlite"
    image_path = tmp_path / "frame.png"
    Image.new("RGB", (640, 360), color=(20, 40, 60)).save(image_path)

    monkeypatch.setattr(
        sys,
        "argv",
        ["construction-reel", "--db", str(db_path), "project", "create", "CLI Test"],
    )
    cli.main()

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "construction-reel",
            "--db",
            str(db_path),
            "scan",
            str(tmp_path),
            "--project",
            "CLI Test",
        ],
    )
    cli.main()
    output = capsys.readouterr().out

    assert "Scan complete" in output
    assert "indexed=1" in output
    assert "errors=0" in output


def test_cli_scan_counts_traversal_errors(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "footage.sqlite"

    monkeypatch.setattr(
        sys,
        "argv",
        ["construction-reel", "--db", str(db_path), "project", "create", "CLI Test"],
    )
    cli.main()

    def fake_find_media(root, on_error=None):
        on_error(PermissionError("cannot read locked folder"))
        return iter(())

    monkeypatch.setattr(cli, "find_media", fake_find_media)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "construction-reel",
            "--db",
            str(db_path),
            "scan",
            str(tmp_path),
            "--project",
            "CLI Test",
        ],
    )
    cli.main()
    output = capsys.readouterr().out

    assert "ERROR accessing directory" in output
    assert "cannot read locked folder" in output
    assert "errors=1" in output
