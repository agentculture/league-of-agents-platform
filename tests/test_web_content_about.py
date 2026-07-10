"""Tests for the About page (``league_site/web/content/about.md``) end to end.

Served through an isolated shell (own :class:`~league_site.web.shell.
FooterSlotRegistry`, the same pattern as ``tests/test_web_theme_shell.py``)
rather than the process-wide default, so these tests don't depend on other
test modules having already registered the footer branding.
"""

from __future__ import annotations

from typing import Any

from league_site.web.branding import register_branding
from league_site.web.http import WSGIApp, http_app
from league_site.web.shell import FooterSlotRegistry, with_shell


def _get(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI test client: GET *path*, return (status, headers, body)."""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _shelled() -> WSGIApp:
    slots = FooterSlotRegistry()
    register_branding(slots)
    return with_shell(http_app(), footer_slots=slots)


def test_about_page_serves_as_a_rendered_html_page() -> None:
    status, headers, _ = _get(_shelled(), "/about")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"


def test_about_page_names_the_author_and_credits_the_agents() -> None:
    _, _, body = _get(_shelled(), "/about")
    text = body.decode("utf-8")
    assert "Ori Nachum" in text
    assert "Claude Code" in text
    assert "Colleague" in text


def test_about_page_links_to_the_agentculture_home() -> None:
    _, _, body = _get(_shelled(), "/about")
    text = body.decode("utf-8")
    assert 'href="https://culture.dev"' in text


def test_about_page_links_to_the_github_repo() -> None:
    _, _, body = _get(_shelled(), "/about")
    text = body.decode("utf-8")
    assert 'href="https://github.com/agentculture/league-of-agents-platform"' in text


def test_about_page_is_reachable_from_the_footer_link() -> None:
    app = _shelled()
    _, _, index_body = _get(app, "/index")
    assert '<a href="/about">About</a>' in index_body.decode("utf-8")
    status, _, _ = _get(app, "/about")
    assert status == "200 OK"


def test_about_page_also_available_as_raw_markdown() -> None:
    """``/about.md`` still resolves through the raw-passthrough, unshelled."""
    _, headers, body = _get(_shelled(), "/about.md")
    assert headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert "Ori Nachum" in body.decode("utf-8")
    assert "<html" not in body.decode("utf-8").lower()
