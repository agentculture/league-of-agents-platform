"""Dawn-identity link/tab assets — favicon, og:image, link-preview meta.

t11 gives the site the family's "dawn" identity in the two places a sibling
is recognized at a glance: the browser tab (a dawn-palette ``/favicon.svg``
with a ``prefers-color-scheme`` dark variant) and link previews (a refreshed
raster ``/og.png`` plus the Open Graph / Twitter Card ``<meta>`` on every
shelled page). This module proves:

* ``/favicon.svg`` serves as ``image/svg+xml``, carries the dark-scheme
  variant and the dawn accent values (never the retired flare-orange), is
  linked from the shell ``<head>`` through the versioned :func:`asset_url`
  helper, and honors HEAD + immutable caching like the other static assets.
* ``/og.png`` serves as ``image/png`` at the standard 1200x630 og size
  (dimensions read straight from the PNG IHDR — no image library needed).
* The link-preview meta (``og:type``/``og:title``/``og:description``/
  ``og:url``/``og:image``/``twitter:card``) appears on shelled pages and
  never on the raw agent surfaces (``.md``/``/front``/``/llms.txt``), whose
  byte identity is guarded separately in ``tests/test_web_raw_surface.py``.

The matching dawn re-map of ``league_site.profiles.svg``'s self-contained
share-card palette is pinned in its own home, ``tests/test_profiles_svg.py``.
"""

from __future__ import annotations

from typing import Any

import pytest

from league_site.web.http import WSGIApp, http_app, site_app
from league_site.web.shell import FooterSlotRegistry, asset_url, with_shell

# --- dawn tokens the family shares (mirror league_site/web/theme.py) --------
_ACCENT_LIGHT = "#0b655c"
_ACCENT_DARK = "#7fdcc9"
_OLD_ORANGE = "#ff8a3d"  # the retired flare-orange identity — must be gone
_CANONICAL_ORIGIN = "https://league-of-agents.ai"


def _get(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI GET client; splits ``?query`` off ``PATH_INFO`` like a real server."""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    path_info, _, query = path.partition("?")
    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path_info, "QUERY_STRING": query}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _head(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    path_info, _, query = path.partition("?")
    environ = {"REQUEST_METHOD": "HEAD", "PATH_INFO": path_info, "QUERY_STRING": query}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _shelled() -> WSGIApp:
    return with_shell(http_app(), footer_slots=FooterSlotRegistry())


def _head_html(text: str) -> str:
    import re

    match = re.search(r"<head\b[^>]*>(.*?)</head>", text, re.S | re.I)
    assert match is not None, "no <head> in rendered page"
    return match.group(1)


# ---------------------------------------------------------------------------
# favicon: route, content type, dark variant, dawn palette
# ---------------------------------------------------------------------------


def test_favicon_route_serves_svg() -> None:
    status, headers, body = _get(_shelled(), "/favicon.svg")
    assert status == "200 OK"
    assert headers["Content-Type"] == "image/svg+xml"
    assert body.lstrip().startswith(b"<svg")


def test_favicon_has_prefers_color_scheme_dark_variant() -> None:
    _, _, body = _get(_shelled(), "/favicon.svg")
    svg = body.decode("utf-8")
    assert "<style>" in svg
    assert "@media (prefers-color-scheme: dark)" in svg


def test_favicon_wears_the_dawn_palette_not_the_old_orange() -> None:
    _, _, body = _get(_shelled(), "/favicon.svg")
    svg = body.decode("utf-8")
    assert _ACCENT_LIGHT in svg  # light-scheme accent
    assert _ACCENT_DARK in svg  # dark-scheme accent
    assert _OLD_ORANGE not in svg


def test_favicon_keeps_the_league_crossed_swords_glyph() -> None:
    _, _, body = _get(_shelled(), "/favicon.svg")
    assert "⚔" in body.decode("utf-8")  # ⚔ CROSSED SWORDS


def test_favicon_carries_immutable_cache_control() -> None:
    _, headers, _ = _get(_shelled(), "/favicon.svg")
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_head_favicon_returns_get_headers_with_empty_body() -> None:
    app = _shelled()
    get_status, get_headers, get_body = _get(app, "/favicon.svg")
    head_status, head_headers, head_body = _head(app, "/favicon.svg")
    assert head_status == get_status == "200 OK"
    assert head_body == b""
    assert head_headers["Content-Type"] == "image/svg+xml"
    assert head_headers["Content-Length"] == get_headers["Content-Length"] == str(len(get_body))


def test_favicon_is_versioned_through_asset_url() -> None:
    assert asset_url("favicon.svg").startswith("/favicon.svg?v=")


def test_shell_head_links_the_favicon() -> None:
    head = _head_html(_get(_shelled(), "/index")[2].decode("utf-8"))
    assert f'<link rel="icon" type="image/svg+xml" href="{asset_url("favicon.svg")}">' in head


# ---------------------------------------------------------------------------
# og:image (the raster card)
# ---------------------------------------------------------------------------


def _png_dimensions(data: bytes) -> tuple[int, int]:
    """Read width/height from a PNG's IHDR chunk (bytes 16..24), no image lib."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert data[12:16] == b"IHDR", "first chunk is not IHDR"
    width = int.from_bytes(data[16:20], "big")
    height = int.from_bytes(data[20:24], "big")
    return width, height


def test_og_image_route_serves_png() -> None:
    status, headers, body = _get(_shelled(), "/og.png")
    assert status == "200 OK"
    assert headers["Content-Type"] == "image/png"
    assert body[:8] == b"\x89PNG\r\n\x1a\n"


def test_og_image_is_the_standard_open_graph_size() -> None:
    _, _, body = _get(_shelled(), "/og.png")
    assert _png_dimensions(body) == (1200, 630)


def test_og_image_carries_immutable_cache_control() -> None:
    _, headers, _ = _get(_shelled(), "/og.png")
    assert headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_head_og_image_returns_get_headers_with_empty_body() -> None:
    app = _shelled()
    get_status, get_headers, get_body = _get(app, "/og.png")
    head_status, head_headers, head_body = _head(app, "/og.png")
    assert head_status == get_status == "200 OK"
    assert head_body == b""
    assert head_headers["Content-Length"] == get_headers["Content-Length"] == str(len(get_body))


def test_og_image_is_versioned_through_asset_url() -> None:
    assert asset_url("og.png").startswith("/og.png?v=")


# ---------------------------------------------------------------------------
# link-preview meta on shelled pages, and NOT on raw surfaces
# ---------------------------------------------------------------------------

_OG_META_KEYS = (
    'property="og:type"',
    'property="og:title"',
    'property="og:description"',
    'property="og:url"',
    'property="og:image"',
    'name="twitter:card"',
)


@pytest.mark.parametrize("path", ("/", "/index", "/about"))
def test_shelled_pages_carry_the_full_link_preview_meta(path: str) -> None:
    head = _head_html(_get(site_app(), path)[2].decode("utf-8"))
    for key in _OG_META_KEYS:
        assert key in head, f"{path}: missing meta {key}"


def test_og_type_and_twitter_card_have_the_expected_values() -> None:
    head = _head_html(_get(_shelled(), "/index")[2].decode("utf-8"))
    assert '<meta property="og:type" content="website">' in head
    assert '<meta name="twitter:card" content="summary_large_image">' in head


def test_og_image_meta_is_the_absolute_versioned_canonical_url() -> None:
    head = _head_html(_get(_shelled(), "/index")[2].decode("utf-8"))
    expected = f'<meta property="og:image" content="{_CANONICAL_ORIGIN}{asset_url("og.png")}">'
    assert expected in head


def test_og_url_meta_is_the_absolute_canonical_page_url() -> None:
    head = _head_html(_get(_shelled(), "/index")[2].decode("utf-8"))
    assert f'<meta property="og:url" content="{_CANONICAL_ORIGIN}/index">' in head


def test_og_title_on_landing_is_the_site_title() -> None:
    head = _head_html(_get(_shelled(), "/index")[2].decode("utf-8"))
    assert '<meta property="og:title" content="League of Agents">' in head


@pytest.mark.parametrize("path", ("/index.md", "/architecture.md", "/front", "/llms.txt"))
def test_raw_surfaces_carry_no_link_preview_meta(path: str) -> None:
    """The raw agent surfaces stay byte-identical to the unwrapped app —
    none of the og/twitter meta may leak into them."""
    body = _get(_shelled(), path)[2].decode("utf-8", "replace")
    for key in _OG_META_KEYS:
        assert key not in body, f"{path}: leaked meta {key}"
    assert "twitter:card" not in body
    assert "og:image" not in body


# ---------------------------------------------------------------------------
# ship-in-artifact guard: both assets are read at import via read_bytes(), so
# a wheel missing them would crash the Lambda at import (not merely 404)
# ---------------------------------------------------------------------------


def test_built_wheel_ships_the_favicon_and_og_image_inside_the_artifact(tmp_path: Any) -> None:
    """The favicon and og-image live *inside* the package
    (``league_site/web/assets/``) exactly like the vendored fonts, so
    hatchling ships them in the wheel with no Makefile ``cp``. Prove it: they
    are read at import time by :mod:`league_site.web.shell`, so a wheel that
    dropped them would fail the Lambda cold-start, not just serve a 404.

    Skipped where ``uv`` isn't available or a wheel can't be built offline —
    the route tests above still prove serving unconditionally."""
    import shutil
    import subprocess  # nosec B404 - builds this repo's own wheel, fixed argv
    import zipfile
    from pathlib import Path

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv not on PATH; wheel-contents ship guard skipped")

    repo_root = Path(__file__).resolve().parent.parent
    out_dir = tmp_path / "wheel"
    result = subprocess.run(  # nosec B603 - fixed argv, offline, no shell
        [uv, "build", "--wheel", "--offline", "--out-dir", str(out_dir), str(repo_root)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        pytest.skip(f"uv build unavailable offline: {result.stderr[-300:]}")

    wheels = list(out_dir.glob("*.whl"))
    assert wheels, "uv build produced no wheel"
    with zipfile.ZipFile(wheels[0]) as zf:
        names = set(zf.namelist())
    for member in (
        "league_site/web/assets/favicon.svg",
        "league_site/web/assets/og.png",
    ):
        assert member in names, f"{member} missing from wheel — would crash Lambda at import"
