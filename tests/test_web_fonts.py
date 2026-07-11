"""First-party variable fonts: vendored, served, preloaded, Lambda-safe.

This is the t3 contract (sibling-of-agentculture.org pass, spec h1) held to
directly. t2 pinned the 320KB font budget in :mod:`league_site.web.theme`
without vendoring anything; this task vendors the two variable ``woff2``
files agentculture.org itself serves — Fraunces Variable (display, the FULL
file that carries the SOFT/WONK axes the design needs) and Albert Sans
Variable (body) — beside their OFL ``LICENSE`` texts under
``league_site/web/assets/fonts/``, serves them first-party at
``/fonts/<name>.woff2`` through :mod:`league_site.web.shell`, and preloads
both in the shell ``<head>`` before the stylesheet link.

What each section proves:

* **Vendoring / packaging** — the two ``woff2`` files resolve *via the
  installed package* (``importlib.resources``), not a repo-relative path, so
  they load wherever the package is installed (``/var/task`` in Lambda), and
  a ``uv build`` wheel actually carries them (the ship-in-artifact guard,
  the lesson of platform#20's docs-tree packaging gap — except fonts live
  *inside* the package, so hatchling ships them with no Makefile ``cp``).
  Their combined weight fits :data:`theme.FONT_BUDGET_BYTES`.
* **Serving** — ``GET /fonts/*.woff2`` returns 200 ``font/woff2``,
  byte-identical to the vendored file, long-lived-immutable cached; HEAD
  returns the same headers with an empty body (the prior static-asset review
  fix, extended to fonts); an unknown ``/fonts/*`` path 404s.
* **Lambda binary path** — the same font path through the API Gateway v2
  handler comes back ``isBase64Encoded: True`` and base64-round-trips
  byte-identically (woff2 is not valid UTF-8, so the adapter's binary branch
  is what carries it).
* **Preload + no external fetch** — both fonts are preloaded with
  ``crossorigin`` before the stylesheet ``<link>``, every fetched resource
  stays same-origin, and no font ever comes from a third-party CDN.
"""

from __future__ import annotations

import base64
from importlib import resources
from typing import Any

import pytest

from league_site.web import theme
from league_site.web.http import WSGIApp, http_app, site_app
from league_site.web.shell import FooterSlotRegistry, with_shell

# The two vendored files, by served name, with the exact byte sizes of the
# agentculture.org-served originals. The Fraunces size (121_016) pins that
# the FULL variable file was vendored — the wght-only Fraunces file is far
# smaller and lacks the SOFT/WONK axes, so this size is a real correctness
# guard, not just a checksum.
_FRAUNCES_NAME = "fraunces-var.woff2"
_ALBERT_NAME = "albert-sans-var.woff2"
_EXPECTED_SIZES = {_FRAUNCES_NAME: 121_016, _ALBERT_NAME: 32_020}
_WOFF2_MAGIC = b"wOF2"
_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


def _vendored_bytes(name: str) -> bytes:
    """The vendored font bytes resolved *through the installed package*.

    Uses :func:`importlib.resources.files` on the package, never a
    repo-checkout-relative path, so this reads the copy that actually ships
    with ``league_site`` wherever it is installed.
    """
    return (resources.files("league_site.web") / "assets" / "fonts" / name).read_bytes()


def _get(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "GET", "PATH_INFO": path}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _head(app: WSGIApp, path: str) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": "HEAD", "PATH_INFO": path}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _shelled() -> WSGIApp:
    return with_shell(http_app(), footer_slots=FooterSlotRegistry())


# ---------------------------------------------------------------------------
# 1. Vendoring / packaging
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", (_FRAUNCES_NAME, _ALBERT_NAME))
def test_font_module_exposes_bytes_identical_to_the_vendored_package_file(name: str) -> None:
    """The fonts module's bytes are exactly the vendored, package-shipped
    file — resolved through ``importlib.resources``, not the repo checkout."""
    from league_site.web import fonts

    on_disk = _vendored_bytes(name)
    assert on_disk[:4] == _WOFF2_MAGIC, f"{name} is not a woff2 file"
    assert len(on_disk) == _EXPECTED_SIZES[name], name
    assert fonts.FONTS[name] == on_disk


def test_both_ofl_license_texts_are_vendored_beside_the_fonts() -> None:
    """Each font ships its OFL ``LICENSE`` beside it (attribution is a term
    of the license): resolved via the package, non-empty, and OFL text."""
    for name in ("fraunces-var.LICENSE.txt", "albert-sans-var.LICENSE.txt"):
        text = (resources.files("league_site.web") / "assets" / "fonts" / name).read_text(
            encoding="utf-8"
        )
        assert "SIL OPEN FONT LICENSE" in text, name


def test_combined_vendored_font_bytes_fit_the_font_budget() -> None:
    """The combined vendored payload fits the ceiling t2 pinned — measured
    against REAL bytes, not the arithmetic of the two sizes."""
    from league_site.web import fonts

    combined = sum(len(b) for b in fonts.FONTS.values())
    assert combined == sum(_EXPECTED_SIZES.values())
    assert combined <= theme.FONT_BUDGET_BYTES


def test_vendored_woff2_files_are_not_valid_utf8() -> None:
    """Documents *why* the Lambda adapter's binary branch carries fonts: a
    woff2 file is not decodable as UTF-8, so ``call_wsgi_app`` base64-encodes
    it. If a future file ever decoded as text this alarm fires before the
    ``isBase64Encoded`` guarantee below could silently regress."""
    for name in (_FRAUNCES_NAME, _ALBERT_NAME):
        with pytest.raises(UnicodeDecodeError):
            _vendored_bytes(name).decode("utf-8")


def test_built_wheel_ships_the_fonts_and_licenses_inside_the_artifact(tmp_path: Any) -> None:
    """Ship-in-artifact guard: the wheel ``pip install --target`` builds into
    the Lambda artifact (see the repo Makefile) must actually contain the
    two ``woff2`` files and both ``LICENSE`` texts under
    ``league_site/web/assets/fonts/``. Fonts live *inside* the package, so
    hatchling includes them with no Makefile ``cp`` (unlike the top-level
    ``docs/`` tree, platform#20) — this proves that default holds.

    Skipped where ``uv`` isn't available or a wheel can't be built offline
    (e.g. a minimal CI image); the ``importlib.resources`` tests above still
    prove package-path resolution unconditionally.
    """
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
        "league_site/web/assets/fonts/fraunces-var.woff2",
        "league_site/web/assets/fonts/albert-sans-var.woff2",
        "league_site/web/assets/fonts/fraunces-var.LICENSE.txt",
        "league_site/web/assets/fonts/albert-sans-var.LICENSE.txt",
    ):
        assert member in names, f"{member} missing from wheel — would 404 in Lambda"


# ---------------------------------------------------------------------------
# 2. Serving through the shell / site_app
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", (_FRAUNCES_NAME, _ALBERT_NAME))
def test_get_font_returns_woff2_byte_identical_and_immutably_cached(name: str) -> None:
    status, headers, body = _get(site_app(), f"/fonts/{name}")
    assert status == "200 OK", name
    assert headers["Content-Type"] == "font/woff2", name
    assert headers["Cache-Control"] == _IMMUTABLE_CACHE, name
    assert headers["Content-Length"] == str(len(body)), name
    assert body == _vendored_bytes(name), name


@pytest.mark.parametrize("name", (_FRAUNCES_NAME, _ALBERT_NAME))
def test_head_font_returns_get_headers_with_empty_body(name: str) -> None:
    """HEAD on a font mirrors the theme.css/site.js HEAD contract: same
    headers (including the would-be ``Content-Length`` and the cache header)
    but an empty body."""
    app = _shelled()
    get_status, get_headers, get_body = _get(app, f"/fonts/{name}")
    head_status, head_headers, head_body = _head(app, f"/fonts/{name}")
    assert head_status == get_status == "200 OK", name
    assert head_body == b"", name
    assert head_headers["Content-Type"] == get_headers["Content-Type"] == "font/woff2", name
    assert head_headers["Content-Length"] == get_headers["Content-Length"] == str(len(get_body))
    assert head_headers["Cache-Control"] == _IMMUTABLE_CACHE, name


def test_unknown_font_path_is_not_served_by_the_shell() -> None:
    """A ``/fonts/*`` path that isn't one of the two vendored files must not
    be served — it falls through to the app and 404s."""
    status, _, _ = _get(site_app(), "/fonts/does-not-exist.woff2")
    assert status.startswith("404")


# ---------------------------------------------------------------------------
# 3. Lambda binary path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", (_FRAUNCES_NAME, _ALBERT_NAME))
def test_lambda_handler_serves_font_as_base64_binary_round_tripping(name: str) -> None:
    """Through the real API Gateway v2 handler a font comes back
    ``isBase64Encoded: True`` and its base64 body decodes byte-identically to
    the vendored file — the binary path API Gateway HTTP API requires."""
    from league_site.aws_lambda.handler import handler

    event = {
        "version": "2.0",
        "routeKey": "$default",
        "rawPath": f"/fonts/{name}",
        "rawQueryString": "",
        "headers": {"accept": "*/*", "host": "id.execute-api.us-east-1.amazonaws.com"},
        "requestContext": {
            "http": {
                "method": "GET",
                "path": f"/fonts/{name}",
                "protocol": "HTTP/1.1",
                "sourceIp": "192.0.2.1",
            },
            "stage": "$default",
        },
        "isBase64Encoded": False,
    }
    response = handler(event, context=None)
    assert response["statusCode"] == 200, name
    assert response["headers"]["Content-Type"] == "font/woff2", name
    assert response["headers"]["Cache-Control"] == _IMMUTABLE_CACHE, name
    assert response["isBase64Encoded"] is True, name
    assert base64.b64decode(response["body"]) == _vendored_bytes(name), name


# ---------------------------------------------------------------------------
# 4. Preload + no external fetch
# ---------------------------------------------------------------------------


def _head_html(text: str) -> str:
    import re

    match = re.search(r"<head\b[^>]*>(.*?)</head>", text, re.S | re.I)
    assert match is not None, "no <head> in rendered page"
    return match.group(1)


@pytest.mark.parametrize("path", ("/", "/index"))
def test_shell_head_preloads_both_fonts_before_the_stylesheet(path: str) -> None:
    head = _head_html(_get(_shelled(), path)[2].decode("utf-8"))
    stylesheet_at = head.index('<link rel="stylesheet" href="/theme.css">')
    for name in (_FRAUNCES_NAME, _ALBERT_NAME):
        preload = (
            f'<link rel="preload" as="font" type="font/woff2" ' f'href="/fonts/{name}" crossorigin>'
        )
        assert preload in head, f"{path}: missing preload for {name}"
        assert head.index(preload) < stylesheet_at, f"{path}: preload for {name} after stylesheet"


def test_font_preloads_are_same_origin_and_no_external_font_is_fetched() -> None:
    """Every ``<head>`` ``<link href=...>`` (preloads included) is
    same-origin, and there's no third-party font CDN anywhere in the page."""
    import re

    text = _get(_shelled(), "/index")[2].decode("utf-8")
    head = _head_html(text)
    for href in re.findall(r"""<link\b[^>]*\bhref=["']([^"']*)["']""", head):
        assert href.startswith("/") and not href.startswith("//"), href
    for banned in (
        "fonts.googleapis.com",
        "fonts.gstatic.com",
        "use.typekit",
        "https://",
        "http://",
    ):
        assert banned not in head, banned
