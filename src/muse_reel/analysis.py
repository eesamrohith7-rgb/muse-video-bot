from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


DEFAULT_VISION_MODEL = "qwen2.5vl:3b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
SCENE_TIME_RE = re.compile(r"pts_time:([0-9]+(?:\.[0-9]+)?)")


def detect_segments(
    video_path: str | Path,
    duration_seconds: float,
    *,
    threshold: float = 0.35,
    max_segment_seconds: float = 8.0,
    min_scene_seconds: float = 1.5,
    timeout_seconds: int = 900,
) -> list[tuple[float, float]]:
    """Split video at strong FFmpeg scene changes and cap long segments."""
    if not 0.0 < threshold <= 1.0:
        raise ValueError("Scene threshold must be greater than 0 and at most 1")
    if max_segment_seconds <= 0:
        raise ValueError("Maximum segment length must be greater than 0")
    if min_scene_seconds < 0:
        raise ValueError("Minimum scene length cannot be negative")
    if duration_seconds <= 0:
        raise ValueError("Video duration must be greater than 0")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg was not found. Install FFmpeg with: brew install ffmpeg")

    path = Path(video_path).expanduser().resolve()
    filter_expression = f"select=gt(scene\\,{threshold}),showinfo"
    command = [
        ffmpeg,
        "-hide_banner",
        "-v",
        "info",
        "-i",
        str(path),
        "-vf",
        filter_expression,
        "-an",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Scene detection timed out for {path.name}") from error
    except OSError as error:
        raise RuntimeError(f"Unable to run ffmpeg for {path.name}: {error}") from error

    if result.returncode != 0:
        raise RuntimeError(
            f"Scene detection failed for {path.name}: {result.stderr.strip()}"
        )

    candidate_times = sorted(
        {
            float(value)
            for value in SCENE_TIME_RE.findall(result.stderr)
            if 0.0 < float(value) < duration_seconds
        }
    )
    cut_times: list[float] = []
    previous = 0.0
    for timestamp in candidate_times:
        if timestamp - previous >= min_scene_seconds:
            cut_times.append(timestamp)
            previous = timestamp

    boundaries = [0.0, *cut_times, float(duration_seconds)]
    segments: list[tuple[float, float]] = []
    for start, end in zip(boundaries, boundaries[1:]):
        while end - start > max_segment_seconds:
            next_boundary = start + max_segment_seconds
            segments.append((round(start, 3), round(next_boundary, 3)))
            start = next_boundary
        if end > start:
            segments.append((round(start, 3), round(end, 3)))
    return segments


def extract_frame(
    video_path: str | Path,
    timestamp_seconds: float,
    output_path: str | Path,
    *,
    max_width: int = 768,
) -> Path:
    """Write one small JPEG preview at a timestamp; the source stays untouched."""
    if timestamp_seconds < 0:
        raise ValueError("Frame timestamp cannot be negative")
    if max_width < 64:
        raise ValueError("Preview width must be at least 64 pixels")

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("ffmpeg was not found. Install FFmpeg with: brew install ffmpeg")

    video = Path(video_path).expanduser().resolve()
    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{timestamp_seconds:.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-vf",
        f"scale={max_width}:-2",
        "-q:v",
        "3",
        "-y",
        str(output),
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=120
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(f"Frame extraction timed out for {video.name}") from error
    except OSError as error:
        raise RuntimeError(f"Unable to extract a frame from {video.name}: {error}") from error
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        details = result.stderr.strip() or "ffmpeg produced no preview frame"
        raise RuntimeError(f"Frame extraction failed for {video.name}: {details}")
    return output


def describe_frame(
    image_path: str | Path,
    *,
    model: str = DEFAULT_VISION_MODEL,
    endpoint: str = DEFAULT_OLLAMA_URL,
    timeout_seconds: int = 240,
) -> tuple[str, list[str]]:
    """Ask a loopback-only Ollama vision model for a factual caption and tags."""
    parsed_endpoint = urlsplit(endpoint)
    if (
        parsed_endpoint.scheme != "http"
        or parsed_endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
    ):
        raise ValueError("Visual tagging is restricted to a local Ollama endpoint")

    image = Path(image_path).expanduser().resolve()
    prompt = (
        "Describe the visible construction or renovation activity in this image. "
        "Use only what is visually supported; do not guess the building, people, "
        "location, or project. Return JSON with exactly two keys: caption (one "
        "short sentence) and tags (an array of 2 to 8 short lowercase visual tags). "
        "Prefer concrete stages, materials, tools, rooms, and visible actions. "
        "If uncertain, use broad tags rather than guessing."
    )
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(image.read_bytes()).decode("ascii")],
            }
        ],
        "format": "json",
        "options": {"temperature": 0},
        "stream": False,
    }
    request = Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_body = json.loads(response.read().decode("utf-8"))
    except (URLError, TimeoutError, OSError) as error:
        raise RuntimeError(
            "Could not reach local Ollama. Start Ollama and install the vision "
            f"model with: ollama pull {model}"
        ) from error
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RuntimeError("Ollama returned an invalid response") from error

    try:
        result = json.loads(response_body["message"]["content"])
        caption = result["caption"]
        raw_tags = result["tags"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RuntimeError("The vision model did not return caption and tags JSON") from error

    if not isinstance(caption, str) or not isinstance(raw_tags, list):
        raise RuntimeError("The vision model returned the wrong caption/tag types")
    tags = list(
        dict.fromkeys(
            tag.strip().lower()
            for tag in raw_tags
            if isinstance(tag, str) and tag.strip()
        )
    )[:8]
    return caption.strip()[:500], tags
