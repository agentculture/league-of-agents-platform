"""Tests for :mod:`league_site.web.shell` — the HTML shell WSGI middleware."""

from __future__ import annotations

import re
from typing import Any

import pytest

from league_site.auth import sessions
from league_site.auth.wsgi import SESSION_COOKIE_NAME, with_auth
from league_site.web import theme
from league_site.web.app import build_app
from league_site.web.http import WSGIApp, http_app
from league_site.web.shell import FooterSlotRegistry, asset_url, header_html, with_shell

_TITLE_RE = re.compile(r"<title>(.*?)</title>")


def _title(body: bytes) -> str:
    match = _TITLE_RE.search(body.decode("utf-8"))
    assert match is not None, "no <title> element in rendered page"
    return match.group(1)


def _get(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI test client: GET *path*, return (status, headers, body)."""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _head(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI test client: HEAD *path*, return (status, headers, body)."""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "HEAD", "PATH_INFO": path}
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
    assert f'<link rel="stylesheet" href="{asset_url("theme.css")}">' in text
    assert "<header" in text and "</header>" in text
    assert "<main" in text and "</main>" in text
    assert "<footer" in text and "</footer>" in text
    # The dazzle pass spent (part of) the renegotiated JS allowance: the
    # only scripts on a page are the inline pre-paint snippet and the
    # first-party /site.js (t4: versioned, ?v=<hash>) — never anything
    # external. The zero-script baseline this evolved from is documented
    # in test_web_theme_budget.py.
    assert f'<script defer src="{asset_url("site.js")}"></script>' in text
    for src in re.findall(r'<script[^>]*\bsrc="([^"]+)"', text):
        assert src.startswith("/"), f"external script src: {src}"


def test_header_carries_wordmark_and_nav_placeholders() -> None:
    _, _, body = _get(_shelled(), "/index")
    text = body.decode("utf-8")
    assert "League" in text
    assert "of Agents" in text
    for label, href in (
        ("Home", "/index"),
        ("Docs", "/docs"),
        ("Leaderboard", "/leaderboard"),
        ("About", "/about"),
    ):
        assert f'<a href="{href}">{label}</a>' in text


def test_landing_page_title_is_the_site_name_at_both_its_urls() -> None:
    """``/`` and ``/index`` both serve the authored landing (platform#14 --
    see ``league_site.web.http._with_root_landing``) and both must present
    the plain site name as their title, exactly like a page with no H1 at
    all falls back to it -- not whatever their body's own ``# `` heading
    happens to read, so the title stays correct even if that heading is
    ever edited to something more conversational than the site name."""
    for path in ("/", "/index"):
        _, _, body = _get(_shelled(), path)
        title = _title(body)
        assert title == "League of Agents", path


def test_docs_catalog_title_is_its_own_heading_not_the_bare_site_name() -> None:
    """``/docs`` is agentfront's own generated doc catalog (formerly served
    at ``/`` -- see ``agentfront.http_surface._index``); it is not a
    landing path, so it gets a sensible title derived from its own ``# ``
    heading, like any other doc page (:func:`test_docs_page_title_is_its_own_h1`)."""
    _, _, body = _get(_shelled(), "/docs")
    assert _title(body) == "Documentation — League of Agents"


def test_docs_page_title_is_its_own_h1() -> None:
    _, _, body = _get(_shelled(), "/architecture")
    assert _title(body) == "Architecture — League of Agents"


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


def test_head_on_static_assets_returns_the_get_headers_with_an_empty_body() -> None:
    """Per HTTP semantics a HEAD response carries the same headers as the
    GET -- including the ``Content-Length`` of the would-be body -- but an
    EMPTY body; many WSGI servers do not strip it for you, so the shell
    must not send it."""
    app = _shelled()
    for path in ("/theme.css", "/site.js"):
        get_status, get_headers, get_body = _get(app, path)
        head_status, head_headers, head_body = _head(app, path)
        assert head_status == get_status == "200 OK", path
        assert head_body == b"", path
        assert head_headers["Content-Type"] == get_headers["Content-Type"], path
        assert head_headers["Content-Length"] == get_headers["Content-Length"], path
        assert head_headers["Content-Length"] == str(len(get_body)), path


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


# --- Session-aware header: sign-in entry + signed-in state (t8) --------------


def _session(display: str = "Ada Lovelace") -> sessions.Session:
    """A verified :class:`~league_site.auth.sessions.Session` for header tests.

    Constructed directly (never minted), so these unit tests need no
    ``LEAGUE_SESSION_SECRET`` — they exercise rendering, not verification.
    ``expiry`` is far in the future so ``is_expired`` is never a factor.
    """
    return sessions.Session(
        subject="42", provider="github", display=display, issued_at=0, expiry=4102444800
    )


def _get_cookie(app: WSGIApp, path: str, cookie: str) -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI test client that presents an ``HTTP_COOKIE`` header."""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path, "HTTP_COOKIE": cookie}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def test_header_html_anonymous_shows_a_github_sign_in_entry() -> None:
    markup = header_html()
    assert 'href="/auth/login/github"' in markup
    assert "Sign in" in markup
    assert "Sign out" not in markup
    # GitHub only — no Google login link on any header state (site-wide invariant).
    assert "/auth/login/google" not in markup


def test_header_html_default_argument_is_the_anonymous_header() -> None:
    assert header_html(None) == header_html()


def test_header_html_signed_in_shows_display_name_and_a_sign_out_link() -> None:
    markup = header_html(_session(display="Ada Lovelace"))
    assert "Ada Lovelace" in markup
    assert 'href="/auth/logout"' in markup
    assert "Sign out" in markup
    # No sign-in affordance once a session exists — neither link nor label.
    assert 'href="/auth/login/github"' not in markup
    assert "/auth/login/google" not in markup


def test_header_html_escapes_a_hostile_display_name() -> None:
    markup = header_html(_session(display='<script>alert("x")</script>'))
    assert "<script>alert" not in markup
    assert "&lt;script&gt;" in markup


def test_shelled_anonymous_request_carries_the_github_sign_in_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The session reaches the shell through ``with_auth``: it stashes the
    verified session (``None`` here — no cookie) into the shared ``environ``
    the shell reads after delegating."""
    monkeypatch.setenv("LEAGUE_SESSION_SECRET", "test-session-secret")
    app = with_shell(with_auth(http_app()), footer_slots=FooterSlotRegistry())
    _, _, body = _get(app, "/index")
    text = body.decode("utf-8")
    assert 'href="/auth/login/github"' in text
    assert 'href="/auth/logout"' not in text


def test_shelled_request_with_a_valid_session_cookie_shows_the_signed_in_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LEAGUE_SESSION_SECRET", "test-session-secret")
    token = sessions.issue({"subject": "42", "provider": "github", "display_name": "Ada Lovelace"})
    app = with_shell(with_auth(http_app()), footer_slots=FooterSlotRegistry())
    _, _, body = _get_cookie(app, "/index", f"{SESSION_COOKIE_NAME}={token}")
    text = body.decode("utf-8")
    assert "Ada Lovelace" in text
    assert 'href="/auth/logout"' in text
    assert "Sign out" in text
    # The anonymous sign-in link is gone once a session verifies.
    assert 'href="/auth/login/github"' not in text


def test_no_google_login_link_anywhere_in_the_rendered_site() -> None:
    """Site-wide GitHub-only invariant: no rendered page *links* /auth/login/google.

    Scans the anchor form (``href="/auth/login/google"``) rather than the
    bare path substring, since authored docs (this plan itself) legitimately
    *mention* the path in prose while linking it nowhere.
    """
    google_link = 'href="/auth/login/google"'
    app = _shelled()
    registry = build_app()
    for entry in registry.list_docs():
        _, _, body = _get(app, f"/{entry.slug}")
        assert google_link not in body.decode("utf-8"), entry.slug
    # Both header states, directly — headers link Google nowhere, ever.
    assert "/auth/login/google" not in header_html()
    assert "/auth/login/google" not in header_html(_session())


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
