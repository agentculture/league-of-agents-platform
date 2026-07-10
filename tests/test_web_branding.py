"""Tests for :mod:`league_site.web.branding` — the footer acknowledgement.

Every test here builds its own isolated :class:`~league_site.web.shell.
FooterSlotRegistry` (the same pattern ``tests/test_web_theme_shell.py``
uses) rather than mutating the process-wide default registry, so these
tests don't depend on — or leak into — other test modules' footer state.
"""

from __future__ import annotations

from typing import Any

from league_site.web.branding import FOOTER_HTML, register_branding
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


def test_register_branding_registers_the_footer_fragment() -> None:
    slots = FooterSlotRegistry()
    register_branding(slots)
    assert FOOTER_HTML in slots.render()


def test_register_branding_is_idempotent_across_a_double_register_call() -> None:
    """Calling :func:`register_branding` twice must not duplicate the slot."""
    slots = FooterSlotRegistry()
    register_branding(slots)
    register_branding(slots)
    assert slots.render().count(FOOTER_HTML) == 1


def test_register_branding_defaults_to_the_process_wide_footer_slots_registry() -> None:
    from league_site.web.shell import FOOTER_SLOTS

    register_branding()
    register_branding()
    assert FOOTER_SLOTS.render().count(FOOTER_HTML) == 1


def test_footer_acknowledgement_appears_exactly_once_on_two_different_pages() -> None:
    slots = FooterSlotRegistry()
    register_branding(slots)
    app = with_shell(http_app(), footer_slots=slots)
    for path in ("/index", "/about"):
        _, _, body = _get(app, path)
        text = body.decode("utf-8")
        assert text.count(FOOTER_HTML) == 1, path
        assert "Powered by AWS" in text, path


def test_footer_carries_a_working_link_to_the_about_page() -> None:
    assert '<a href="/about">About</a>' in FOOTER_HTML
