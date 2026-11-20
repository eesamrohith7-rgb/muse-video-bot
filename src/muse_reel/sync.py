from __future__ import annotations

import json
from urllib.error import URLError
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .database import FootageDatabase


MAX_SYNC_BYTES = 2 * 1024 * 1024


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, *_args: object, **_kwargs: object) -> None:
        return None


def sync_project_index(
    database: FootageDatabase,
    project_id: int,
    *,
    endpoint: str,
    token: str,
    timeout_seconds: int = 60,
) -> int:
    """Send captions and timestamps to a hosted index, without local file paths."""
    if not token:
        raise ValueError("Set MUSE_REEL_SYNC_TOKEN before syncing the index")
    parsed = urlsplit(endpoint)
    local_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.scheme != "https" and not local_http)
    ):
        raise ValueError("Index sync requires an HTTPS endpoint")

    payload = database.export_project_scene_index(project_id)
    encoded_payload = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(encoded_payload) > MAX_SYNC_BYTES:
        raise ValueError("The index is larger than the 2 MB sync limit")
    request = Request(
        endpoint,
        data=encoded_payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with build_opener(_NoRedirectHandler).open(
            request, timeout=timeout_seconds
        ) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (URLError, HTTPError, TimeoutError, OSError) as error:
        raise RuntimeError("Could not sync the index to the hosted service") from error
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError("The hosted service returned an invalid response") from error

    if not isinstance(result, dict) or result.get("status") != "ok":
        raise RuntimeError("The hosted service rejected the index")
    try:
        return int(result["scenes"])
    except (KeyError, TypeError, ValueError) as error:
        raise RuntimeError("The hosted service returned an invalid scene count") from error
