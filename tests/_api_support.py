"""Shared test-only helpers for the ``league_site.api`` test suite.

Not collected by pytest (module name doesn't match ``test_*``); imported
directly by the ``test_api_*`` modules.
"""

from __future__ import annotations

import io
import json
from typing import Any, Callable

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]


def call(
    app: WSGIApp,
    method: str,
    path: str,
    *,
    query: str = "",
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    environ_extra: dict[str, Any] | None = None,
) -> tuple[str, dict[str, str], Any]:
    """Minimal WSGI test client.

    Returns ``(status, headers, parsed_body)`` — ``parsed_body`` is the
    JSON-decoded response body, or the raw ``bytes`` if it doesn't decode
    as JSON (e.g. a plain-text/HTML response from a passed-through path).
    """
    captured: dict[str, Any] = {}

    def start_response(
        status: str, response_headers: list[tuple[str, str]], exc_info: Any = None
    ) -> None:
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    raw_body = b"" if body is None else json.dumps(body).encode("utf-8")
    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query,
        "CONTENT_LENGTH": str(len(raw_body)),
        "CONTENT_TYPE": "application/json",
        "wsgi.input": io.BytesIO(raw_body),
        "wsgi.url_scheme": "https",
        "HTTP_HOST": "league-of-agents.ai",
    }
    for name, value in (headers or {}).items():
        environ[f"HTTP_{name.upper().replace('-', '_')}"] = value
    environ.update(environ_extra or {})

    raw = b"".join(app(environ, start_response))
    parsed: Any
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        parsed = raw
    return captured["status"], captured["headers"], parsed


def bearer(token: str) -> dict[str, str]:
    """``headers=`` value for an agent bearer token."""
    return {"Authorization": f"Bearer {token}"}
