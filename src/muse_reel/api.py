from __future__ import annotations

import hmac
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .database import FootageDatabase


class _ReadOnlyHandler(BaseHTTPRequestHandler):
    database_path: Path
    api_token: str

    def do_GET(self) -> None:
        supplied_token = self.headers.get("Authorization", "")
        expected_token = f"Bearer {self.api_token}"
        if not hmac.compare_digest(supplied_token, expected_token):
            self._send_json(401, {"error": "authentication required"})
            return

        route = urlsplit(self.path)
        try:
            with FootageDatabase(self.database_path) as database:
                if route.path == "/health":
                    self._send_json(200, {"status": "ok"})
                    return
                if route.path == "/v1/projects":
                    projects = [
                        {"id": row["id"], "name": row["name"]}
                        for row in database.list_projects()
                    ]
                    self._send_json(200, {"projects": projects})
                    return
                if route.path == "/v1/scenes/search":
                    self._search_scenes(database, parse_qs(route.query))
                    return
        except (OSError, ValueError) as error:
            self._send_json(500, {"error": str(error)})
            return
        self._send_json(404, {"error": "route not found"})

    def _search_scenes(self, database: FootageDatabase, query: dict[str, list[str]]) -> None:
        project_name = query.get("project", [""])[0].strip()
        search_text = query.get("q", [""])[0].strip()
        try:
            limit = max(1, min(int(query.get("limit", ["20"])[0]), 100))
        except ValueError:
            self._send_json(400, {"error": "limit must be an integer"})
            return
        if not project_name:
            self._send_json(400, {"error": "project is required"})
            return

        project = database.get_project(project_name)
        if project is None:
            self._send_json(404, {"error": "project not found"})
            return

        scenes = database.search_scenes(int(project["id"]), search_text, limit)
        self._send_json(
            200,
            {
                "project": project_name,
                "query": search_text,
                "scenes": [
                    {
                        "filename": row["filename"],
                        "segment_index": row["segment_index"],
                        "start_seconds": row["start_seconds"],
                        "end_seconds": row["end_seconds"],
                        "caption": row["caption"],
                        "tags": json.loads(row["tags_json"]),
                    }
                    for row in scenes
                ],
            },
        )

    def _send_json(self, status: int, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        # Search terms and construction metadata can be sensitive; keep request logs off.
        return


def serve_read_only_api(
    database_path: str | Path,
    *,
    api_token: str,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    if not api_token:
        raise ValueError("An API token is required")
    if host not in {"127.0.0.1", "localhost"}:
        raise ValueError("The development API can only bind to localhost")

    handler = type(
        "MuseReelReadOnlyHandler",
        (_ReadOnlyHandler,),
        {
            "database_path": Path(database_path).expanduser().resolve(),
            "api_token": api_token,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
