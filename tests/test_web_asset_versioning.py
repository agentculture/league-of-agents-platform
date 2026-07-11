"""Versioned asset URLs — the shell's ``?v=<hash>`` cache-busting contract.

Motivating incident (spec c14-c16, ``docs/runbooks/cloudflare-league-of-agents-ai.md``
"Cache purge (emergency, /theme.css and /site.js)"): after the 0.7.x deploy,
Cloudflare's edge kept serving the pre-dazzle ``/theme.css`` under
``max-age=14400`` against new HTML/JS — the theme toggle went dead in
production. The fix ships here: every stylesheet/script/font URL the shell
(and the two standalone page shells, :mod:`league_site.viewer.wsgi` and
:mod:`league_site.profiles.wsgi`) emit carries a query-string content hash
(:func:`league_site.web.shell.asset_url`), so a byte change can only ever
be reached at a *new* URL — there is nothing stale left to purge.

Scheme: the first 8 hex digits of the served bytes' sha256, e.g.
``/theme.css?v=1a2b3c4d``. ``PATH_INFO`` never includes the query string
(confirmed for the real deploy path by
:mod:`league_site.aws_lambda.wsgi`, which builds it from ``rawPath`` /
``rawQueryString`` separately), so route matching is untouched by this —
the query is decorative to the server, load-bearing for caches.

What each section proves:

* Every reference the shell emits (``<link rel="stylesheet">``,
  ``<script defer src>``, both font ``<link rel="preload">`` tags) carries
  ``?v=<hash>``, and that hash equals the sha256 (first 8 hex) of the bytes
  *actually served* at that path today — not a hardcoded literal, so this
  can't drift from the served bytes.
* Fetching the referenced URL, query string and all, on the very FIRST
  fetch, returns exactly those bytes — the "honesty" the incident's fix
  demands: a browser (or Cloudflare) that has never seen this URL before
  gets the current build, not a stale cached one, because the URL never
  existed before this build.
* The hash is a genuine function of content: two different byte strings
  hash to two different (and correctly shaped) values, which is what makes
  "bumping the version changes every reference" true by construction
  rather than by convention.
* ``/theme.css`` and ``/site.js`` now carry the same long-lived immutable
  ``Cache-Control`` the fonts got in t3 — safe now that the URL changes
  with the bytes.
* :func:`league_site.web.shell.asset_url` is the one shared helper —
  :mod:`league_site.viewer.wsgi` and :mod:`league_site.profiles.wsgi`
  route their own ``/site.js`` reference through it rather than
  hardcoding the path, so there is exactly one hash computation for the
  whole site.
* The raw agent surfaces and static-asset serving behavior this task must
  not disturb (HEAD-headers-only, unknown-name 404, byte identity) still
  hold with the versioned scheme in place.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest

from league_site.web import fonts, scripts, theme
from league_site.web.http import WSGIApp, http_app, site_app
from league_site.web.shell import FooterSlotRegistry, asset_url, with_shell

_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"
_VERSION_RE = re.compile(r"\?v=([0-9a-f]+)$")


def _content_hash(data: bytes) -> str:
    """Reference implementation of the hash scheme, independent of shell.py."""
    return hashlib.sha256(data).hexdigest()[:8]


def _get(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI test client: GET *path*, return (status, headers, body).

    Splits *path* into ``PATH_INFO``/``QUERY_STRING`` exactly like a real
    server does (confirmed against :mod:`league_site.aws_lambda.wsgi` for
    the actual deploy path) — a query string must never leak into
    ``PATH_INFO``, so a versioned reference like ``/theme.css?v=1a2b3c4d``
    routes to exactly the same handler as the bare path.
    """
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    path_info, _, query = path.partition("?")
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path_info, "QUERY_STRING": query}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _shelled() -> WSGIApp:
    return with_shell(http_app(), footer_slots=FooterSlotRegistry())


def _head_html(text: str) -> str:
    match = re.search(r"<head\b[^>]*>(.*?)</head>", text, re.S | re.I)
    assert match is not None, "no <head> in rendered page"
    return match.group(1)


# ---------------------------------------------------------------------------
# 1. Every shell-emitted reference carries ?v=<hash of the served bytes>
# ---------------------------------------------------------------------------


def test_stylesheet_link_carries_the_hash_of_the_served_theme_css() -> None:
    head = _head_html(_get(_shelled(), "/index")[2].decode("utf-8"))
    match = re.search(r'<link rel="stylesheet" href="(/theme\.css\?v=[0-9a-f]{8})">', head)
    assert match is not None, head
    href = match.group(1)
    _, _, served = _get(_shelled(), "/theme.css")
    assert href == f"/theme.css?v={_content_hash(served)}"


def test_script_tag_carries_the_hash_of_the_served_site_js() -> None:
    head = _head_html(_get(_shelled(), "/index")[2].decode("utf-8"))
    match = re.search(r'<script defer src="(/site\.js\?v=[0-9a-f]{8})"></script>', head)
    assert match is not None, head
    src = match.group(1)
    _, _, served = _get(_shelled(), "/site.js")
    assert src == f"/site.js?v={_content_hash(served)}"


@pytest.mark.parametrize("name", tuple(fonts.FONTS))
def test_font_preload_carries_the_hash_of_the_served_font(name: str) -> None:
    head = _head_html(_get(_shelled(), "/index")[2].decode("utf-8"))
    pattern = (
        r'<link rel="preload" as="font" type="font/woff2" '
        rf'href="(/fonts/{re.escape(name)}\?v=[0-9a-f]{{8}})" crossorigin>'
    )
    match = re.search(pattern, head)
    assert match is not None, head
    href = match.group(1)
    _, _, served = _get(_shelled(), f"/fonts/{name}")
    assert href == f"/fonts/{name}?v={_content_hash(served)}"


def test_every_asset_reference_on_the_landing_page_carries_a_version_query() -> None:
    """No asset URL the shell emits is unversioned — a blanket sweep on top
    of the per-asset checks above."""
    head = _head_html(_get(_shelled(), "/index")[2].decode("utf-8"))
    for href in re.findall(r'href="(/(?:theme\.css|fonts/[^"]+))"', head):
        assert _VERSION_RE.search(href), f"unversioned reference: {href}"
    for src in re.findall(r'src="(/site\.js)"', head):
        pytest.fail(f"unversioned script src: {src}")


# ---------------------------------------------------------------------------
# 2. The referenced URL returns current-build bytes on the FIRST fetch
# ---------------------------------------------------------------------------


def test_fetching_the_versioned_theme_css_url_returns_current_bytes() -> None:
    app = _shelled()
    url = asset_url("theme.css")
    assert "?v=" in url
    status, headers, body = _get(app, url)
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/css; charset=utf-8"
    assert body.decode("utf-8") == theme.STYLESHEET


def test_fetching_the_versioned_site_js_url_returns_current_bytes() -> None:
    app = _shelled()
    url = asset_url("site.js")
    assert "?v=" in url
    status, headers, body = _get(app, url)
    assert status == "200 OK"
    assert headers["Content-Type"] == "application/javascript; charset=utf-8"
    assert body.decode("utf-8") == scripts.SITE_JS


@pytest.mark.parametrize("name", tuple(fonts.FONTS))
def test_fetching_a_versioned_font_url_returns_current_bytes(name: str) -> None:
    app = _shelled()
    url = asset_url(name)
    assert "?v=" in url
    status, headers, body = _get(app, url)
    assert status == "200 OK"
    assert headers["Content-Type"] == fonts.MEDIA_TYPE
    assert body == fonts.FONTS[name]


def test_bare_unversioned_paths_still_serve_the_same_bytes() -> None:
    """The query is decorative to the server: the same route answers with
    or without it, so an old cached HTML page referencing a bare path (or
    a hand-typed URL) still works — only caching honesty depends on the
    query, never routing."""
    app = _shelled()
    for bare, versioned in (
        ("/theme.css", asset_url("theme.css")),
        ("/site.js", asset_url("site.js")),
    ):
        _, _, bare_body = _get(app, bare)
        _, _, versioned_body = _get(app, versioned)
        assert bare_body == versioned_body


# ---------------------------------------------------------------------------
# 3. The hash is a genuine function of content
# ---------------------------------------------------------------------------


def test_content_hash_differs_for_different_bytes_and_is_eight_hex_digits() -> None:
    """Structural proof that "bumping the version changes every reference"
    holds by construction: since every emitted URL is
    ``path + "?v=" + sha256(served_bytes)[:8]`` (proved in section 1 above),
    and different bytes hash to different values (proved here), any byte
    change to a served asset necessarily changes its URL."""
    a = _content_hash(b"the quick brown fox")
    b = _content_hash(b"the quick brown fo!")  # one byte different
    assert a != b
    assert len(a) == 8
    assert re.fullmatch("[0-9a-f]{8}", a)
    assert re.fullmatch("[0-9a-f]{8}", b)
    # deterministic — the same bytes always hash to the same value
    assert a == _content_hash(b"the quick brown fox")


def test_bumping_the_underlying_bytes_in_a_subprocess_changes_the_reference() -> None:
    """End-to-end version of the structural proof above, run in a *throwaway
    subprocess* rather than an in-process ``importlib.reload`` of
    :mod:`league_site.web.shell`: that module's ``FooterSlotRegistry`` class
    and process-wide ``FOOTER_SLOTS`` singleton are imported by name all
    over the codebase (:mod:`league_site.viewer.wsgi`,
    :mod:`league_site.profiles.wsgi`, other test modules), and reloading it
    in THIS process mints a *new* ``FooterSlotRegistry`` class object,
    silently breaking every ``isinstance`` check against the one other
    modules already imported — real contamination observed while
    developing this test. A subprocess is discarded after the one
    ``print``, so the same reload there is safe: mutate
    :data:`league_site.web.scripts.SITE_JS` after the normal import chain
    has already run once (proving the baseline), then
    ``importlib.reload`` :mod:`league_site.web.shell` so it recomputes
    ``asset_url`` from the new bytes, exactly like a fresh deploy would."""
    import subprocess
    import sys

    script = (
        "from league_site.web import scripts\n"
        "import league_site.web.shell as shell\n"
        "baseline = shell.asset_url('site.js')\n"
        "scripts.SITE_JS = scripts.SITE_JS + '\\n// bumped for test\\n'\n"
        "import importlib\n"
        "importlib.reload(shell)\n"
        "print(baseline)\n"
        "print(shell.asset_url('site.js'))\n"
    )
    result = subprocess.run(  # nosec B603 - fixed argv, same interpreter as pytest
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
    )
    baseline, bumped = result.stdout.strip().splitlines()

    assert baseline.startswith("/site.js?v=")
    assert bumped.startswith("/site.js?v=")
    assert baseline != bumped


# ---------------------------------------------------------------------------
# 4. Long-lived immutable Cache-Control on theme.css and site.js
# ---------------------------------------------------------------------------


def test_theme_css_and_site_js_carry_the_immutable_cache_header() -> None:
    app = _shelled()
    for path in ("/theme.css", "/site.js"):
        _, headers, _ = _get(app, path)
        assert headers["Cache-Control"] == _IMMUTABLE_CACHE, path


def test_theme_css_and_site_js_carry_the_immutable_cache_header_via_the_versioned_url() -> None:
    app = _shelled()
    for path in (asset_url("theme.css"), asset_url("site.js")):
        _, headers, _ = _get(app, path)
        assert headers["Cache-Control"] == _IMMUTABLE_CACHE, path


def test_head_on_theme_css_and_site_js_still_returns_get_headers_with_empty_body() -> None:
    """The pre-existing HEAD contract (same headers, empty body) must
    survive both the versioning change and the new Cache-Control header."""
    from tests.test_web_theme_shell import _head as _head_request

    app = _shelled()
    for path in ("/theme.css", "/site.js"):
        get_status, get_headers, get_body = _get(app, path)
        head_status, head_headers, head_body = _head_request(app, path)
        assert head_status == get_status == "200 OK", path
        assert head_body == b"", path
        assert head_headers["Cache-Control"] == get_headers["Cache-Control"] == _IMMUTABLE_CACHE
        assert head_headers["Content-Length"] == get_headers["Content-Length"]


# ---------------------------------------------------------------------------
# 5. asset_url is the one shared helper — no duplicated hash logic
# ---------------------------------------------------------------------------


def test_asset_url_covers_theme_css_site_js_and_every_font() -> None:
    assert asset_url("theme.css").startswith("/theme.css?v=")
    assert asset_url("site.js").startswith("/site.js?v=")
    for name in fonts.FONTS:
        assert asset_url(name).startswith(f"/fonts/{name}?v=")


def test_asset_url_rejects_an_unknown_name() -> None:
    with pytest.raises(ValueError):
        asset_url("does-not-exist.css")


def test_viewer_leaderboard_page_references_site_js_through_asset_url() -> None:
    """:mod:`league_site.viewer.wsgi` renders its own standalone page shell
    (ahead of :func:`with_shell`, per that module's docstring) but must not
    hand-roll a second, unversioned ``/site.js`` reference — it routes
    through the same :func:`~league_site.web.shell.asset_url` helper shell.py
    uses, so there is exactly one hash computation for the whole site."""
    status, headers, body = _get(site_app(), "/leaderboard")
    assert status == "200 OK"
    text = body.decode("utf-8")
    assert f'<script defer src="{asset_url("site.js")}"></script>' in text
    assert '<script defer src="/site.js"></script>' not in text


def test_profile_page_references_site_js_through_asset_url() -> None:
    """Same contract as the leaderboard page, for
    :mod:`league_site.profiles.wsgi`'s standalone profile page shell."""
    from league_site.profiles.data import identity_slug
    from league_site.profiles.wsgi import profile_app
    from tests._profiles_support import ADA_IDENTITY, build_scenario

    scenario = build_scenario()
    app = profile_app(scenario.ledger_store, scenario.match_store)
    slug = identity_slug(ADA_IDENTITY)
    status, _, body = _get(app, f"/profiles/{slug}")
    assert status == "200 OK"
    text = body.decode("utf-8")
    assert f'<script defer src="{asset_url("site.js")}"></script>' in text
    assert '<script defer src="/site.js"></script>' not in text


# ---------------------------------------------------------------------------
# 6. Nothing else about static-asset serving regressed
# ---------------------------------------------------------------------------


def test_unknown_font_path_still_404s_with_a_version_query_too() -> None:
    status, _, _ = _get(site_app(), "/fonts/does-not-exist.woff2?v=deadbeef")
    assert status.startswith("404")


def test_content_length_header_matches_the_actual_versioned_response_body() -> None:
    app = _shelled()
    for path in (asset_url("theme.css"), asset_url("site.js")):
        _, headers, body = _get(app, path)
        assert headers["Content-Length"] == str(len(body))
