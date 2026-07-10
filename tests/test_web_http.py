"""WSGI-level tests for :mod:`league_site.web.http` — the platform's HTTP surface."""

from __future__ import annotations

from typing import Any

from league_site.web.http import WSGIApp, http_app, serve


def _get(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI test client: GET *path*, return (status, headers, body)."""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def test_index_page_serves_authored_markdown_no_hand_written_html() -> None:
    app = http_app()
    status, headers, body = _get(app, "/index")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/markdown; charset=utf-8"
    text = body.decode("utf-8")
    assert "# League of Agents" in text
    assert "<html" not in text.lower()
    assert "<body" not in text.lower()


def test_same_content_fetchable_as_raw_markdown_at_stable_url() -> None:
    """``/index`` (the page) and ``/index.md`` (the raw passthrough) agree byte-for-byte."""
    app = http_app()
    _, _, page_body = _get(app, "/index")
    _, headers, raw_body = _get(app, "/index.md")
    assert headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert raw_body == page_body
    assert b"League of Agents" in raw_body


def test_root_index_links_to_the_page() -> None:
    app = http_app()
    _, _, body = _get(app, "/")
    assert "/index" in body.decode("utf-8")


def test_unknown_slug_404s() -> None:
    app = http_app()
    status, _, _ = _get(app, "/nope-not-a-real-page")
    assert status == "404 Not Found"


def test_unknown_slug_with_md_suffix_also_404s() -> None:
    app = http_app()
    status, _, _ = _get(app, "/nope-not-a-real-page.md")
    assert status == "404 Not Found"


def test_serve_binds_and_shuts_down_cleanly() -> None:
    server = serve(port=0)
    try:
        assert server.server_port != 0
    finally:
        server.server_close()
