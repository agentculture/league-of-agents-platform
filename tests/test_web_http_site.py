"""Tests for :func:`league_site.web.http.site_app` — the composed site entry.

``site_app()`` registers its branding on the process-wide
:data:`~league_site.web.shell.FOOTER_SLOTS` registry (see
``league_site.web.branding.register_branding``'s default), which is by
design idempotent and shared across the whole test process — these tests
lean on that idempotency rather than isolating a registry per test.
"""

from __future__ import annotations

from typing import Any

from league_site.web.branding import FOOTER_HTML
from league_site.web.http import WSGIApp, http_app, site_app
from league_site.web.shell import FOOTER_SLOTS


def _get(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI test client: GET *path*, return (status, headers, body)."""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def test_site_app_serves_index_md_byte_identical_to_the_unwrapped_app() -> None:
    unwrapped = http_app()
    composed = site_app()
    _, unwrapped_headers, unwrapped_body = _get(unwrapped, "/index.md")
    _, composed_headers, composed_body = _get(composed, "/index.md")
    assert composed_body == unwrapped_body
    assert composed_headers["Content-Type"] == unwrapped_headers["Content-Type"]
    assert composed_headers["Content-Type"] == "text/markdown; charset=utf-8"


def test_site_app_leaves_llms_txt_and_front_unshelled() -> None:
    unwrapped = http_app()
    composed = site_app()
    for path in ("/llms.txt", "/front"):
        unwrapped_status, unwrapped_headers, unwrapped_body = _get(unwrapped, path)
        composed_status, composed_headers, composed_body = _get(composed, path)
        assert composed_status == unwrapped_status, path
        assert composed_body == unwrapped_body, path
        assert composed_headers["Content-Type"] == unwrapped_headers["Content-Type"], path


def test_site_app_serves_rendered_html_for_the_root_page() -> None:
    status, headers, body = _get(site_app(), "/")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8")
    assert "<!doctype html>" in text
    assert FOOTER_HTML in text


def test_site_app_registers_branding_without_duplicating_it_across_calls() -> None:
    """Calling :func:`site_app` more than once (as separate Lambda cold
    starts in the same test process would) must not duplicate the footer
    registration on the shared process-wide registry."""
    site_app()
    site_app()
    assert FOOTER_SLOTS.render().count(FOOTER_HTML) == 1
