from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable

from PIL import Image

from .models import MediaMetadata


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}
SUPPORTED_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS


def find_media(root: str | Path) -> Iterable[Path]:
    root_path = Path(root).expanduser()
    if not root_path.exists():
        raise FileNotFoundError(f"Footage folder not found: {root_path}")
    if not root_path.is_dir():
        raise NotADirectoryError(f"Footage path is not a folder: {root_path}")

    for directory, dirnames, filenames in os.walk(root_path):
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in {".git", ".DS_Store", "__pycache__", ".venv"}
        )
        for filename in sorted(filenames):
            path = Path(directory) / filename
            if path.suffix.lower() in SUPPORTED_EXTENSIONS:
                yield path


def get_creation_date(path: Path) -> str | None:
    stat = path.stat()
    timestamp = getattr(stat, "st_birthtime", None)
    if timestamp is None:
        timestamp = stat.st_mtime
    try:
        return datetime.fromtimestamp(timestamp).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def parse_fps(value: str | None) -> float | None:
    if not value:
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            if float(denominator) == 0:
                return None
            return float(numerator) / float(denominator)
        return float(value)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def probe_video(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        raise RuntimeError(
            "ffprobe was not found. Install FFmpeg with: brew install ffmpeg"
        )

    command = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration,format_name",
        "-show_entries",
        "stream=index,codec_type,width,height,r_frame_rate,avg_frame_rate",
        "-of",
        "json",
        str(path),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"ffprobe failed for {path.name}: {result.stderr.strip()}"
        )
    return json.loads(result.stdout)


def scan_media(path: Path) -> MediaMetadata:
    path = path.resolve()
    suffix = path.suffix.lower()
    stat = path.stat()
    creation_date = get_creation_date(path)

    if suffix in IMAGE_EXTENSIONS:
        with Image.open(path) as image:
            width, height = image.size
        return MediaMetadata(
            filename=path.name,
            file_path=str(path),
            file_size=stat.st_size,
            media_type="image",
            duration_seconds=None,
            width=width,
            height=height,
            fps=None,
            creation_date=creation_date,
            file_format=suffix.lstrip(".").upper(),
        )

    probe = probe_video(path)
    video_stream = next(
        (
            stream
            for stream in probe.get("streams", [])
            if stream.get("codec_type") == "video"
        ),
        {},
    )
    duration = probe.get("format", {}).get("duration")
    try:
        duration_seconds = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_seconds = None

    fps = parse_fps(
        video_stream.get("avg_frame_rate")
        or video_stream.get("r_frame_rate")
    )
    file_format = probe.get("format", {}).get("format_name")
    return MediaMetadata(
        filename=path.name,
        file_path=str(path),
        file_size=stat.st_size,
        media_type="video",
        duration_seconds=duration_seconds,
        width=video_stream.get("width"),
        height=video_stream.get("height"),
        fps=fps,
        creation_date=creation_date,
        file_format=file_format,
    )

