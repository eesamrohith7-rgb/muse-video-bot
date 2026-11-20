from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

from .models import MediaMetadata


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_media (
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
    mtime_ns INTEGER,
    first_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(project_id, file_path),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_source_media_project
ON source_media(project_id);

CREATE INDEX IF NOT EXISTS idx_source_media_type
ON source_media(media_type);

CREATE INDEX IF NOT EXISTS idx_source_media_filename
ON source_media(filename);

CREATE TABLE IF NOT EXISTS scene_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_media_id INTEGER NOT NULL,
    segment_index INTEGER NOT NULL,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    thumbnail_path TEXT NOT NULL,
    caption TEXT NOT NULL DEFAULT '',
    tags_json TEXT NOT NULL DEFAULT '[]',
    model TEXT NOT NULL DEFAULT '',
    source_mtime_ns INTEGER,
    threshold REAL NOT NULL DEFAULT 0.35,
    max_segment_seconds REAL NOT NULL DEFAULT 8.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_media_id, segment_index),
    FOREIGN KEY(source_media_id) REFERENCES source_media(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scene_segments_media
ON scene_segments(source_media_id, start_seconds);

CREATE TABLE IF NOT EXISTS scan_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    root_path TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TEXT,
    discovered INTEGER NOT NULL DEFAULT 0,
    indexed INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
"""


class FootageDatabase:
    def __init__(self, database_path: str | Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(SCHEMA)
        self._migrate_source_media()
        self.connection.commit()

    def _migrate_source_media(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(source_media)"
            ).fetchall()
        }
        if "mtime_ns" not in columns:
            self.connection.execute(
                "ALTER TABLE source_media ADD COLUMN mtime_ns INTEGER"
            )
        scene_columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(scene_segments)"
            ).fetchall()
        }
        if "max_segment_seconds" not in scene_columns:
            self.connection.execute(
                "ALTER TABLE scene_segments ADD COLUMN max_segment_seconds REAL NOT NULL DEFAULT 8.0"
            )

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "FootageDatabase":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def create_project(self, name: str) -> int:
        with self.connection:
            cursor = self.connection.execute(
                "INSERT OR IGNORE INTO projects(name) VALUES (?)", (name.strip(),)
            )
        row = self.connection.execute(
            "SELECT id FROM projects WHERE name = ?", (name.strip(),)
        ).fetchone()
        if row is None:
            raise ValueError(f"Unable to create project: {name}")
        return int(row["id"])

    def get_project(self, name: str) -> Optional[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM projects WHERE name = ?", (name.strip(),)
        ).fetchone()

    def list_projects(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT * FROM projects ORDER BY name"
        ).fetchall()

    def start_scan(self, project_id: int, root_path: str) -> int:
        cursor = self.connection.execute(
            "INSERT INTO scan_runs(project_id, root_path) VALUES (?, ?)",
            (project_id, root_path),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def finish_scan(
        self,
        scan_id: int,
        *,
        discovered: int,
        indexed: int,
        updated: int,
        errors: int,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                UPDATE scan_runs
                SET finished_at = CURRENT_TIMESTAMP,
                    discovered = ?, indexed = ?, updated = ?, errors = ?
                WHERE id = ?
                """,
                (discovered, indexed, updated, errors, scan_id),
            )

    def upsert_media(
        self, project_id: int, metadata: MediaMetadata
    ) -> tuple[str, bool]:
        existing = self.connection.execute(
            "SELECT * FROM source_media WHERE project_id = ? AND file_path = ?",
            (project_id, metadata.file_path),
        ).fetchone()

        values = (
            metadata.filename,
            metadata.file_size,
            metadata.media_type,
            metadata.duration_seconds,
            metadata.width,
            metadata.height,
            metadata.fps,
            metadata.creation_date,
            metadata.file_format,
            metadata.mtime_ns,
        )
        with self.connection:
            if existing is None:
                self.connection.execute(
                    """
                    INSERT INTO source_media(
                        project_id, filename, file_path, file_size, media_type,
                        duration_seconds, width, height, fps, creation_date,
                        file_format, mtime_ns
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            project_id,
                            metadata.filename,
                            metadata.file_path,
                            metadata.file_size,
                            metadata.media_type,
                            metadata.duration_seconds,
                            metadata.width,
                            metadata.height,
                            metadata.fps,
                            metadata.creation_date,
                            metadata.file_format,
                            metadata.mtime_ns,
                        ),
                )
                return "indexed", False

            changed = any(
                existing[column] != value
                for column, value in zip(
                    (
                        "filename",
                        "file_size",
                        "media_type",
                        "duration_seconds",
                        "width",
                        "height",
                        "fps",
                        "creation_date",
                        "file_format",
                        "mtime_ns",
                    ),
                    values,
                )
            )
            self.connection.execute(
                """
                UPDATE source_media
                SET filename = ?, file_size = ?, media_type = ?,
                    duration_seconds = ?, width = ?, height = ?, fps = ?,
                    creation_date = ?, file_format = ?, mtime_ns = ?,
                    last_seen_at = CURRENT_TIMESTAMP,
                    last_updated_at = CASE WHEN ? THEN CURRENT_TIMESTAMP ELSE last_updated_at END
                WHERE id = ?
                """,
                (*values, changed, existing["id"]),
            )
            if changed:
                self.connection.execute(
                    "DELETE FROM scene_segments WHERE source_media_id = ?",
                    (existing["id"],),
                )
        return ("updated" if changed else "unchanged"), changed

    def list_media(
        self, project_id: int, media_type: Optional[str] = None
    ) -> list[sqlite3.Row]:
        if media_type:
            return self.connection.execute(
                """
                SELECT * FROM source_media
                WHERE project_id = ? AND media_type = ?
                ORDER BY filename COLLATE NOCASE
                """,
                (project_id, media_type),
            ).fetchall()
        return self.connection.execute(
            """
            SELECT * FROM source_media
            WHERE project_id = ?
            ORDER BY filename COLLATE NOCASE
            """,
            (project_id,),
        ).fetchall()

    def replace_scene_segments(
        self, source_media_id: int, segments: list[dict[str, object]]
    ) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM scene_segments WHERE source_media_id = ?",
                (source_media_id,),
            )
            self.connection.executemany(
                """
                INSERT INTO scene_segments(
                    source_media_id, segment_index, start_seconds, end_seconds,
                    thumbnail_path, caption, tags_json, model,
                    source_mtime_ns, threshold, max_segment_seconds
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        source_media_id,
                        segment["segment_index"],
                        segment["start_seconds"],
                        segment["end_seconds"],
                        segment["thumbnail_path"],
                        segment.get("caption", ""),
                        json.dumps(segment.get("tags", []), ensure_ascii=False),
                        segment.get("model", ""),
                        segment.get("source_mtime_ns"),
                        segment.get("threshold", 0.35),
                        segment.get("max_segment_seconds", 8.0),
                    )
                    for segment in segments
                ],
            )

    def scenes_are_current(
        self,
        source_media_id: int,
        source_mtime_ns: int | None,
        threshold: float,
        model: str,
        max_segment_seconds: float,
    ) -> bool:
        rows = self.connection.execute(
            """
            SELECT source_mtime_ns, threshold, model, max_segment_seconds
            FROM scene_segments WHERE source_media_id = ?
            """,
            (source_media_id,),
        ).fetchall()
        return bool(rows) and all(
            row["source_mtime_ns"] == source_mtime_ns
            and abs(row["threshold"] - threshold) < 1e-9
            and row["model"] == model
            and abs(row["max_segment_seconds"] - max_segment_seconds) < 1e-9
            for row in rows
        )

    def search_scenes(
        self, project_id: int, query: str, limit: int = 20
    ) -> list[sqlite3.Row]:
        query = query.strip()
        pattern = f"%{query}%"
        return self.connection.execute(
            """
            SELECT sm.id AS media_id, sm.filename, sm.file_path,
                   ss.segment_index, ss.start_seconds, ss.end_seconds,
                   ss.thumbnail_path, ss.caption, ss.tags_json, ss.model
            FROM scene_segments AS ss
            JOIN source_media AS sm ON sm.id = ss.source_media_id
            WHERE sm.project_id = ?
              AND (? = '' OR sm.filename LIKE ? OR ss.caption LIKE ? OR ss.tags_json LIKE ?)
            ORDER BY sm.filename COLLATE NOCASE, ss.start_seconds
            LIMIT ?
            """,
            (project_id, query, pattern, pattern, pattern, max(1, min(limit, 100))),
        ).fetchall()

    def export_project_scene_index(self, project_id: int) -> dict[str, object]:
        project = self.connection.execute(
            "SELECT name FROM projects WHERE id = ?", (project_id,)
        ).fetchone()
        if project is None:
            raise ValueError("Project not found")
        rows = self.connection.execute(
            """
            SELECT sm.id AS media_key, sm.filename, ss.segment_index,
                   ss.start_seconds, ss.end_seconds, ss.caption, ss.tags_json
            FROM scene_segments AS ss
            JOIN source_media AS sm ON sm.id = ss.source_media_id
            WHERE sm.project_id = ?
            ORDER BY sm.filename COLLATE NOCASE, ss.start_seconds
            """,
            (project_id,),
        ).fetchall()
        return {
            "project": project["name"],
            "scenes": [
                {
                    "media_key": str(row["media_key"]),
                    "filename": row["filename"],
                    "segment_index": row["segment_index"],
                    "start_seconds": row["start_seconds"],
                    "end_seconds": row["end_seconds"],
                    "caption": row["caption"],
                    "tags": json.loads(row["tags_json"]),
                }
                for row in rows
            ],
        }

    def replace_project_scene_index(self, payload: object) -> int:
        if not isinstance(payload, dict):
            raise ValueError("Index payload must be an object")
        project_name = payload.get("project")
        scenes = payload.get("scenes")
        if not isinstance(project_name, str) or not project_name.strip() or len(project_name) > 200:
            raise ValueError("Project name is invalid")
        if not isinstance(scenes, list) or len(scenes) > 20000:
            raise ValueError("Scenes must be an array with at most 20000 items")

        normalized: list[tuple[str, str, int, float, float, str, str]] = []
        seen: set[tuple[str, int]] = set()
        media_names: dict[str, str] = {}
        for scene in scenes:
            if not isinstance(scene, dict):
                raise ValueError("Each scene must be an object")
            media_key = scene.get("media_key")
            filename = scene.get("filename")
            segment_index = scene.get("segment_index")
            start_seconds = scene.get("start_seconds")
            end_seconds = scene.get("end_seconds")
            caption = scene.get("caption", "")
            tags = scene.get("tags", [])
            if not isinstance(media_key, str) or not media_key or len(media_key) > 64:
                raise ValueError("Scene media key is invalid")
            if (
                not isinstance(filename, str)
                or not filename
                or len(filename) > 512
                or "/" in filename
                or "\\" in filename
            ):
                raise ValueError("Scene filename must be a basename")
            if media_key in media_names and media_names[media_key] != filename:
                raise ValueError("A media key cannot refer to multiple filenames")
            media_names[media_key] = filename
            if type(segment_index) is not int or segment_index < 1:
                raise ValueError("Scene segment index is invalid")
            if (
                isinstance(start_seconds, bool)
                or isinstance(end_seconds, bool)
                or not isinstance(start_seconds, (int, float))
                or not isinstance(end_seconds, (int, float))
            ):
                raise ValueError("Scene timestamps are invalid")
            try:
                start_seconds = float(start_seconds)
                end_seconds = float(end_seconds)
            except (OverflowError, ValueError) as error:
                raise ValueError("Scene timestamps are invalid") from error
            if (
                not math.isfinite(start_seconds)
                or not math.isfinite(end_seconds)
                or start_seconds < 0
                or end_seconds <= start_seconds
            ):
                raise ValueError("Scene timestamps are invalid")
            if not isinstance(caption, str) or len(caption) > 2000:
                raise ValueError("Scene caption is invalid")
            if (
                not isinstance(tags, list)
                or len(tags) > 8
                or any(not isinstance(tag, str) or not tag or len(tag) > 100 for tag in tags)
            ):
                raise ValueError("Scene tags are invalid")
            key = (media_key, segment_index)
            if key in seen:
                raise ValueError("Scene media key and segment index must be unique")
            seen.add(key)
            normalized.append(
                (
                    media_key,
                    filename,
                    segment_index,
                    start_seconds,
                    end_seconds,
                    caption,
                    json.dumps(tags, ensure_ascii=False),
                )
            )

        project_id = self.create_project(project_name)
        with self.connection:
            self.connection.execute(
                "DELETE FROM source_media WHERE project_id = ?", (project_id,)
            )
            media: dict[str, str] = {}
            for media_key, filename, *_rest in normalized:
                media.setdefault(media_key, filename)
            source_ids: dict[str, int] = {}
            for media_key, filename in media.items():
                cursor = self.connection.execute(
                    """
                    INSERT INTO source_media(
                        project_id, filename, file_path, file_size, media_type
                    ) VALUES (?, ?, ?, 0, 'video')
                    """,
                    (project_id, filename, f"muse-index://{project_id}/{media_key}"),
                )
                source_ids[media_key] = int(cursor.lastrowid)
            self.connection.executemany(
                """
                INSERT INTO scene_segments(
                    source_media_id, segment_index, start_seconds, end_seconds,
                    thumbnail_path, caption, tags_json, model, source_mtime_ns,
                    threshold, max_segment_seconds
                ) VALUES (?, ?, ?, ?, '', ?, ?, 'local-index', NULL, 0.35, 8.0)
                """,
                [
                    (
                        source_ids[media_key],
                        segment_index,
                        start_seconds,
                        end_seconds,
                        caption,
                        tags_json,
                    )
                    for (
                        media_key,
                        _filename,
                        segment_index,
                        start_seconds,
                        end_seconds,
                        caption,
                        tags_json,
                    ) in normalized
                ],
            )
        return len(normalized)
