from __future__ import annotations

import json
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
