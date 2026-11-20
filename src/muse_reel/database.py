from __future__ import annotations

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
