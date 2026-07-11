"""Raw-surface byte-identity + no-external-origin proofs for :mod:`league_site.web.shell`.

Two properties :func:`~league_site.web.shell.with_shell`'s docstring
promises, held to directly here -- complementing (not duplicating) the
passthrough assertions already spread across ``tests/test_web_theme_shell.py``,
``tests/test_web_scripts.py``, and ``tests/test_web_hero.py``:

* **Byte identity** -- every raw-surface path (``*.md``, ``/llms.txt``,
  ``/front``) served through the *full* dazzle-shelled app is exactly the
  same bytes, with exactly the same ``Content-Type``, as the same path
  served by the unwrapped app. Those other modules already prove this for
  ``/index.md`` plus ``/llms.txt``/``/front`` (each folded into a test about
  something else -- scripts, hero); this module adds an explicit,
  parametrized sweep across all three plus one arbitrary doc's ``.md`` URL
  (``/architecture.md``), so a raw-surface regression on any of them fails
  on its own, named test rather than only incidentally.
* **No externally fetched resource, anywhere** -- every rendered page
  (the landing paths, ``/docs``, ``/leaderboard``, ``/about``),
  ``/theme.css``, and ``/site.js`` must never ask the browser to fetch a
  third party: every ``src="..."`` attribute, every ``<link href="...">``
  inside ``<head>``, and every ``url(...)`` inside an inline ``<style>``
  block must resolve to a same-origin path (starts with ``"/"``, never
  ``"//"``). Anchor (``<a href>``) links in body content are excluded on
  purpose -- a link *to* an external site is content the visitor chooses to
  follow, not a resource the page fetches on their behalf (the platform's
  own ``/about`` and ``index.md`` content links to AWS/GitHub/AgentCulture
  exactly this way). ``/theme.css`` gets its own narrower check (the only
  ``url(`` tokens allowed are the two same-origin ``@font-face`` sources
  for the vendored variable fonts — spec h1's USER DECISION, t5's
  ``@font-face`` rules — and ``@import`` stays banned outright) and
  ``/site.js`` gets the request-API denylist plus the protocol-relative
  case ``test_web_scripts.py``'s string-level check doesn't cover.
* A **guard test** on the two ``shell.py`` module constants
  (``_UNSHELLED_PATHS``, ``_MD_SUFFIX``) this whole file's raw-surface
  contract rests on -- a cheap drift alarm if either is ever edited.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from league_site.web import shell, theme
from league_site.web.http import WSGIApp, http_app, site_app
from league_site.web.shell import FooterSlotRegistry, asset_url, with_shell


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


# ---------------------------------------------------------------------------
# 1. Byte identity: raw surfaces through the FULL shell == the unwrapped app
# ---------------------------------------------------------------------------

_RAW_SURFACE_PATHS = ("/index.md", "/llms.txt", "/front", "/architecture.md")


@pytest.mark.parametrize("path", _RAW_SURFACE_PATHS)
def test_raw_surface_is_byte_identical_through_the_full_shell(path: str) -> None:
    """Each raw-surface path, run through :func:`with_shell` itself (not a
    comparison against a source constant), returns the exact same status,
    body bytes, and ``Content-Type`` as the unwrapped app -- the shell must
    never touch these responses at all, not even to re-encode them."""
    inner = http_app()
    shelled = with_shell(inner, footer_slots=FooterSlotRegistry())
    inner_status, inner_headers, inner_body = _get(inner, path)
    shelled_status, shelled_headers, shelled_body = _get(shelled, path)
    assert shelled_status == inner_status, path
    assert shelled_body == inner_body, path
    assert shelled_headers["Content-Type"] == inner_headers["Content-Type"], path
    assert shelled_headers["Content-Type"] == "text/markdown; charset=utf-8", path


def test_raw_surface_paths_are_actually_nonempty_markdown() -> None:
    """Guards the proof above against a false positive: an empty-body bug
    present in *both* the wrapped and unwrapped app would still satisfy
    byte identity, so pin every raw-surface path to real, non-trivial
    content too."""
    inner = http_app()
    for path in _RAW_SURFACE_PATHS:
        _, _, body = _get(inner, path)
        assert len(body) > 100, path


# ---------------------------------------------------------------------------
# 2. No externally fetched resource, anywhere on a rendered page
# ---------------------------------------------------------------------------

_SRC_RE = re.compile(r"""\bsrc=["']([^"']*)["']""")
_HEAD_RE = re.compile(r"<head\b[^>]*>(.*?)</head>", re.S | re.I)
_HEAD_LINK_HREF_RE = re.compile(r"""<link\b[^>]*\bhref=["']([^"']*)["']""", re.I)
_SCRIPT_TAG_RE = re.compile(r"<script\b[^>]*>", re.I)
_STYLE_BLOCK_RE = re.compile(r"<style\b[^>]*>(.*?)</style>", re.S | re.I)
_URL_FN_RE = re.compile(r'url\(\s*[\'"]?([^\'")]+)[\'"]?\s*\)')


def _is_same_origin(value: str) -> bool:
    """Same-origin per the task contract: starts with ``/``, never ``//``."""
    return value.startswith("/") and not value.startswith("//")


def _assert_no_externally_fetched_resources(html_text: str, *, context: str) -> None:
    """Scan *html_text* for every resource the browser would *fetch* on its
    own -- any ``src=`` attribute, any ``<head>`` ``<link href=`` , and any
    ``url(...)`` inside an inline ``<style>`` block -- and assert each one
    is same-origin. Anchor ``href=`` in body content is deliberately never
    scanned: a link a visitor may choose to follow is content, not a fetch
    the page performs unprompted."""
    for src in _SRC_RE.findall(html_text):
        assert _is_same_origin(src), f"{context}: externally-sourced src={src!r}"

    head_match = _HEAD_RE.search(html_text)
    if head_match is not None:
        for href in _HEAD_LINK_HREF_RE.findall(head_match.group(1)):
            assert _is_same_origin(href), f"{context}: externally-sourced <link href={href!r}>"

    for style_body in _STYLE_BLOCK_RE.findall(html_text):
        for url in _URL_FN_RE.findall(style_body):
            assert _is_same_origin(url), f"{context}: externally-sourced style url({url!r})"


def test_shelled_landing_page_fetches_nothing_off_origin() -> None:
    shelled = _shelled()
    for path in ("/", "/index"):
        text = _get(shelled, path)[2].decode("utf-8")
        _assert_no_externally_fetched_resources(text, context=path)


@pytest.mark.parametrize("path", ("/docs", "/leaderboard", "/about"))
def test_other_public_pages_fetch_nothing_off_origin(path: str) -> None:
    """Uses :func:`~league_site.web.http.site_app` -- the actual composed,
    production app -- because ``/leaderboard`` is served by
    :mod:`league_site.viewer.wsgi`, a self-contained page that never passes
    through :func:`with_shell` at all (see that module's docstring): under
    the bare ``with_shell(http_app())`` fixture used elsewhere in this file
    it isn't a registered doc and simply 404s, which would make the scan
    vacuous. ``site_app()`` gives all three their real rendered content."""
    status, headers, body = _get(site_app(), path)
    assert status == "200 OK", path
    assert headers["Content-Type"] == "text/html; charset=utf-8", path
    _assert_no_externally_fetched_resources(body.decode("utf-8"), context=path)


def test_theme_css_urls_are_exactly_the_two_vendored_font_sources() -> None:
    """The successor to the pre-font "no ``url(`` token at all" bar, which
    documented the system-stack baseline the font budget was renegotiated
    *from*. t5's ``@font-face`` rules now spend that allowance, so the bar
    evolves with the contract: the ONLY ``url(`` tokens permitted in the
    stylesheet are the two same-origin ``/fonts/*.woff2`` sources t3
    vendored — nothing else, never off-origin, and ``@import`` stays banned
    outright regardless of origin (it is always a second blocking
    request)."""
    urls = _URL_FN_RE.findall(theme.STYLESHEET)
    assert sorted(urls) == ["/fonts/albert-sans-var.woff2", "/fonts/fraunces-var.woff2"]
    for url in urls:
        assert _is_same_origin(url), url
    assert "@import" not in theme.STYLESHEET


def test_site_js_fetches_nothing_off_origin() -> None:
    """Complements ``test_web_scripts.py``'s
    ``test_site_js_makes_no_external_requests`` (which checks the plain
    ``SITE_JS`` string constant) with the same guarantee read off the
    *served* ``/site.js`` response, plus the protocol-relative case that
    check doesn't cover: a quoted ``"//host/..."`` literal would dodge a
    bare ``"http"``/``"https://"`` substring search but still ask the
    browser to fetch cross-origin."""
    _, headers, body = _get(_shelled(), "/site.js")
    assert headers["Content-Type"] == "application/javascript; charset=utf-8"
    js = body.decode("utf-8")
    for banned in ("fetch(", "XMLHttpRequest", "import(", "http://", "https://"):
        assert banned not in js, banned
    assert re.search(r"[\"']//\S", js) is None, "protocol-relative URL literal in /site.js"


# ---------------------------------------------------------------------------
# 3. Guard: the raw-surface contract's own constants haven't drifted
# ---------------------------------------------------------------------------


def test_unshelled_paths_and_md_suffix_constants_are_unchanged() -> None:
    """Cheap drift alarm: every proof above assumes these two constants. If
    either is ever edited, every test in this module should be revisited
    (and probably rewritten) rather than silently keep testing a stale
    contract."""
    assert shell._UNSHELLED_PATHS == frozenset({"/llms.txt", "/front"})
    assert shell._MD_SUFFIX == ".md"


# ---------------------------------------------------------------------------
# 3. The exact script inventory — quote-style-proof
# ---------------------------------------------------------------------------


def test_every_page_carries_exactly_the_two_known_scripts() -> None:
    """Pin the complete <script> inventory on every page family: ONE inline
    pre-paint snippet (no src) and ONE deferred first-party /site.js
    (t4: versioned, ``?v=<hash>`` -- see
    :func:`league_site.web.shell.asset_url`). A single-quoted src, a
    protocol-relative //host, or a smuggled inline beacon script all fail
    here — the earlier src-attribute scans only saw double-quoted
    attributes (review finding B2)."""
    for path, text in (
        ("/", _get(_shelled(), "/")[2].decode("utf-8")),
        ("/docs", _get(site_app(), "/docs")[2].decode("utf-8")),
        ("/leaderboard", _get(site_app(), "/leaderboard")[2].decode("utf-8")),
    ):
        tags = _SCRIPT_TAG_RE.findall(text)
        srcs = [m for tag in tags for m in _SRC_RE.findall(tag)]
        assert srcs == [asset_url("site.js")], f"{path}: unexpected script srcs {srcs!r}"
        inline = [tag for tag in tags if "src" not in tag]
        assert len(inline) == 1, f"{path}: expected exactly one inline script"
        assert "dataset.js" in text, f"{path}: pre-paint snippet missing"
