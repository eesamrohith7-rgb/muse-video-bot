from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class MediaMetadata:
    filename: str
    file_path: str
    file_size: int
    media_type: str
    duration_seconds: Optional[float]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    creation_date: Optional[str]
    file_format: Optional[str]

