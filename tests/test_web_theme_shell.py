"""Tests for :mod:`league_site.web.shell` — the HTML shell WSGI middleware."""

from __future__ import annotations

from typing import Any

from league_site.web import theme
from league_site.web.app import build_app
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
    """A shelled app with its own footer registry, isolated from the process-wide default."""
    return with_shell(http_app(), footer_slots=FooterSlotRegistry())


def test_index_page_carries_the_full_shell() -> None:
    status, headers, body = _get(_shelled(), "/index")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8")
    assert text.startswith("<!doctype html>")
    assert '<html lang="en">' in text
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in text
    assert '<meta name="description" content="' in text
    assert '<link rel="stylesheet" href="/theme.css">' in text
    assert "<header" in text and "</header>" in text
    assert "<main" in text and "</main>" in text
    assert "<footer" in text and "</footer>" in text
    assert "<script" not in text.lower()


def test_header_carries_wordmark_and_nav_placeholders() -> None:
    _, _, body = _get(_shelled(), "/index")
    text = body.decode("utf-8")
    assert "LEAGUE" in text
    assert "OF AGENTS" in text
    for label, href in (
        ("Home", "/index"),
        ("Docs", "/"),
        ("Leaderboard", "/leaderboard"),
        ("About", "/about"),
    ):
        assert f'<a href="{href}">{label}</a>' in text


def test_footer_slot_defaults_to_empty_but_the_slot_is_present() -> None:
    _, _, body = _get(_shelled(), "/index")
    text = body.decode("utf-8")
    assert '<footer class="site-footer">' in text
    assert '<div class="wrap"></div>' in text


def test_footer_slot_registry_renders_registered_fragments_in_order() -> None:
    slots = FooterSlotRegistry()
    slots.register("<p>first</p>")
    slots.register("<p>second</p>")
    app = with_shell(http_app(), footer_slots=slots)
    _, _, body = _get(app, "/index")
    text = body.decode("utf-8")
    assert '<div class="wrap"><p>first</p><p>second</p></div>' in text


def test_with_shell_defaults_to_the_module_level_footer_registry() -> None:
    from league_site.web import shell as shell_module

    assert isinstance(shell_module.FOOTER_SLOTS, FooterSlotRegistry)


def test_raw_markdown_passthrough_stays_byte_identical_to_the_unwrapped_app() -> None:
    inner = http_app()
    shelled = with_shell(inner, footer_slots=FooterSlotRegistry())
    _, inner_headers, inner_body = _get(inner, "/index.md")
    _, shelled_headers, shelled_body = _get(shelled, "/index.md")
    assert shelled_body == inner_body
    assert shelled_headers["Content-Type"] == inner_headers["Content-Type"]
    assert shelled_headers["Content-Type"] == "text/markdown; charset=utf-8"


def test_llms_txt_and_front_stay_unshelled() -> None:
    inner = http_app()
    shelled = with_shell(inner, footer_slots=FooterSlotRegistry())
    for path in ("/llms.txt", "/front"):
        inner_status, inner_headers, inner_body = _get(inner, path)
        shelled_status, shelled_headers, shelled_body = _get(shelled, path)
        assert shelled_status == inner_status, path
        assert shelled_body == inner_body, path
        assert shelled_headers["Content-Type"] == inner_headers["Content-Type"], path


def test_sitemap_and_unknown_slugs_stay_unshelled() -> None:
    inner = http_app()
    shelled = with_shell(inner, footer_slots=FooterSlotRegistry())
    for path in ("/sitemap.xml", "/nope-not-a-real-page", "/nope-not-a-real-page.md"):
        inner_status, inner_headers, inner_body = _get(inner, path)
        shelled_status, shelled_headers, shelled_body = _get(shelled, path)
        assert shelled_status == inner_status, path
        assert shelled_body == inner_body, path
        assert shelled_headers["Content-Type"] == inner_headers["Content-Type"], path


def test_theme_css_is_served_and_matches_the_stylesheet_module() -> None:
    status, headers, body = _get(_shelled(), "/theme.css")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/css; charset=utf-8"
    assert body.decode("utf-8") == theme.STYLESHEET


def test_theme_css_served_bytes_are_within_the_documented_budget() -> None:
    _, headers, body = _get(_shelled(), "/theme.css")
    assert len(body) <= theme.CSS_BUDGET_BYTES
    assert headers["Content-Length"] == str(len(body))


def test_with_shell_closes_a_closeable_inner_response_iterable() -> None:
    closed = []

    class _ClosingIterable(list):
        def close(self) -> None:
            closed.append(True)

    def inner(environ: dict[str, Any], start_response: Any) -> "_ClosingIterable":
        start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
        return _ClosingIterable([b"hi"])

    app = with_shell(inner, footer_slots=FooterSlotRegistry())
    _get(app, "/whatever")
    assert closed == [True]


def test_missing_content_type_header_is_treated_as_raw_passthrough() -> None:
    def inner(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        start_response("200 OK", [])
        return [b"no content-type header"]

    app = with_shell(inner, footer_slots=FooterSlotRegistry())
    status, headers, body = _get(app, "/whatever")
    assert status == "200 OK"
    assert body == b"no content-type header"
    assert "Content-Type" not in headers


def test_every_registered_doc_page_renders_through_the_shell_without_crashing() -> None:
    app = _shelled()
    registry = build_app()
    for entry in registry.list_docs():
        status, headers, body = _get(app, f"/{entry.slug}")
        assert status == "200 OK", entry.slug
        assert headers["Content-Type"] == "text/html; charset=utf-8", entry.slug
        text = body.decode("utf-8")
        assert text.startswith("<!doctype html>"), entry.slug
        assert '<html lang="en">' in text, entry.slug
        assert 'name="viewport"' in text, entry.slug
        assert 'name="description"' in text, entry.slug
