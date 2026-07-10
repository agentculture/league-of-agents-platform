"""Tests for :mod:`league_site.web.scripts` and the shell's JS wiring.

The dazzle pass's t3 contract, in one place:

* ``/site.js`` is served first-party by :func:`league_site.web.shell.
  with_shell`, exactly the way ``/theme.css`` already is — correct content
  type, correct ``Content-Length``, body byte-identical to
  :data:`league_site.web.scripts.SITE_JS`.
* Every shelled page's ``<head>`` carries a tiny inline *pre-paint* snippet
  **before** the stylesheet link — it applies any stored explicit theme
  choice before first paint (no flash of the wrong theme) and stamps
  ``html[data-js]`` so t4's reveal styles never hide content when JS is
  off — plus a ``<script defer src="/site.js">`` tag.
* The header carries the theme-toggle button; with JS disabled it is inert
  but harmless (real ``aria-label``, real text, ``type="button"``).
* The raw passthrough surfaces (``*.md``, ``/llms.txt``, ``/front``) stay
  byte-identical to the unwrapped app and carry no script tags at all.
* All of it fits the renegotiated first-party JS budget
  (:data:`league_site.web.theme.JS_BUDGET_BYTES`) — the inline snippet
  counts toward that budget too, even though it never travels via
  ``/site.js``.

Where node is available, a small behavioral harness actually *executes*
the pre-paint snippet and ``SITE_JS`` against stub DOM/localStorage objects
and drives the toggle through a full light → dark → system cycle; where it
is not, those tests skip and the string-contract tests still gate the
merge.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any

import pytest

from league_site.web import scripts, theme
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
    return with_shell(http_app(), footer_slots=FooterSlotRegistry())


def _page(path: str = "/index") -> str:
    _, _, body = _get(_shelled(), path)
    return body.decode("utf-8")


# ---------------------------------------------------------------------------
# /site.js — served first-party, exactly like /theme.css
# ---------------------------------------------------------------------------


def test_site_js_is_served_and_matches_the_scripts_module() -> None:
    status, headers, body = _get(_shelled(), "/site.js")
    assert status == "200 OK"
    assert headers["Content-Type"] == "application/javascript; charset=utf-8"
    assert body.decode("utf-8") == scripts.SITE_JS


def test_site_js_content_length_matches_the_served_bytes() -> None:
    _, headers, body = _get(_shelled(), "/site.js")
    assert headers["Content-Length"] == str(len(body))


# ---------------------------------------------------------------------------
# The shelled page: pre-paint snippet, defer tag, toggle button
# ---------------------------------------------------------------------------


def test_head_carries_the_pre_paint_snippet_before_the_stylesheet_link() -> None:
    """The inline snippet must run before the stylesheet is even requested,
    so an explicit stored choice is on ``<html>`` before first paint — no
    flash of the wrong theme."""
    text = _page()
    head = text.split("</head>")[0]
    snippet_at = head.find(f"<script>{scripts.PRE_PAINT_JS}</script>")
    link_at = head.find('<link rel="stylesheet" href="/theme.css">')
    assert snippet_at != -1, "pre-paint snippet missing from <head>"
    assert link_at != -1, "stylesheet link missing from <head>"
    assert snippet_at < link_at, "pre-paint snippet must come before the stylesheet link"


def test_pre_paint_snippet_reads_localstorage_and_stamps_data_js() -> None:
    snippet = scripts.PRE_PAINT_JS
    assert 'localStorage.getItem("theme")' in snippet
    assert "try" in snippet and "catch" in snippet
    assert 'dataset.js="1"' in snippet.replace(" ", "")


def test_head_carries_the_deferred_first_party_script_tag() -> None:
    text = _page()
    head = text.split("</head>")[0]
    assert '<script defer src="/site.js"></script>' in head


def test_the_only_script_srcs_anywhere_are_first_party() -> None:
    text = _page()
    for src in re.findall(r'<script[^>]*\bsrc="([^"]+)"', text):
        assert src.startswith("/"), f"external script src: {src}"


def test_header_wrap_carries_the_toggle_button_after_the_nav() -> None:
    text = _page()
    header = text.split("<header", 1)[1].split("</header>")[0]
    button_match = re.search(r"<button[^>]*>", header)
    assert button_match is not None, "no <button> in the header"
    button = button_match.group(0)
    assert 'type="button"' in button
    assert 'id="theme-toggle"' in button
    assert 'class="theme-toggle"' in button
    assert 'aria-label="' in button
    assert header.find("</nav>") < header.find("<button"), "toggle must follow the nav"


def test_toggle_button_is_harmless_without_js() -> None:
    """No JS, no problem: the button already has an accessible name and a
    visible glyph, and ``type="button"`` means it never submits anything."""
    text = _page()
    match = re.search(r'<button[^>]*id="theme-toggle"[^>]*>(.*?)</button>', text, re.S)
    assert match is not None
    assert match.group(1).strip(), "button must have visible text content"
    label = re.search(r'aria-label="([^"]+)"', match.group(0))
    assert label is not None and "theme" in label.group(1).lower()


# ---------------------------------------------------------------------------
# Raw passthrough surfaces stay script-free and byte-identical
# ---------------------------------------------------------------------------


def test_raw_passthrough_pages_carry_no_script_tags_and_stay_byte_identical() -> None:
    inner = http_app()
    shelled = with_shell(inner, footer_slots=FooterSlotRegistry())
    for path in ("/index.md", "/llms.txt", "/front"):
        _, inner_headers, inner_body = _get(inner, path)
        _, shelled_headers, shelled_body = _get(shelled, path)
        assert shelled_body == inner_body, path
        assert shelled_headers["Content-Type"] == inner_headers["Content-Type"], path
        assert b"<script" not in shelled_body.lower(), path


# ---------------------------------------------------------------------------
# SITE_JS content contract — string-level
# ---------------------------------------------------------------------------


def test_site_js_is_strict_and_iife_wrapped_with_no_console_noise() -> None:
    assert '"use strict"' in scripts.SITE_JS
    assert scripts.SITE_JS.lstrip().startswith(("/*", "(function"))
    assert "console." not in scripts.SITE_JS


def test_site_js_makes_no_external_requests() -> None:
    for banned in ("http://", "https://", "fetch(", "XMLHttpRequest", "import(", "WebSocket"):
        assert banned not in scripts.SITE_JS, banned
    assert "http" not in scripts.PRE_PAINT_JS


def test_site_js_persists_explicit_choices_and_clears_them_on_system() -> None:
    js = scripts.SITE_JS
    assert '"theme"' in js, "localStorage key must be the literal 'theme'"
    assert "localStorage.setItem" in js
    assert "localStorage.removeItem" in js
    assert "delete" in js and "dataset.theme" in js


def test_site_js_wires_the_reveal_observer_with_a_no_observer_fallback() -> None:
    js = scripts.SITE_JS
    assert "IntersectionObserver" in js
    assert '".reveal"' in js or "'.reveal'" in js
    assert '"revealed"' in js or "'revealed'" in js
    assert "unobserve" in js
    assert "threshold" in js and "0.1" in js


# ---------------------------------------------------------------------------
# Budget — the inline snippet counts too
# ---------------------------------------------------------------------------


def test_pre_paint_snippet_stays_tiny() -> None:
    """It blocks parsing, so it has to stay trivially small (~300 bytes
    including its wrapping tags)."""
    inline = f"<script>{scripts.PRE_PAINT_JS}</script>"
    assert len(inline.encode("utf-8")) <= 300


def test_combined_first_party_js_fits_the_renegotiated_budget() -> None:
    """The 8KB allowance covers the inline pre-paint snippet *plus*
    ``SITE_JS`` — the snippet doesn't ride ``/site.js`` but it is still
    first-party JS the visitor pays for on every page."""
    combined = len(scripts.SITE_JS.encode("utf-8")) + len(scripts.PRE_PAINT_JS.encode("utf-8"))
    assert combined <= theme.JS_BUDGET_BYTES


# ---------------------------------------------------------------------------
# Behavioral harness — executes the real JS when node is available
# ---------------------------------------------------------------------------

_NODE = shutil.which("node")

_HARNESS = """
"use strict";
const [prePaint, siteJs] = [process.argv[1], process.argv[2]];
const storage = new Map();
global.localStorage = {
  getItem: (k) => (storage.has(k) ? storage.get(k) : null),
  setItem: (k, v) => storage.set(k, String(v)),
  removeItem: (k) => storage.delete(k),
};
const root = { dataset: {} };
const button = {
  textContent: "\\u25d0",
  title: "",
  attrs: {},
  handlers: {},
  setAttribute(name, value) { this.attrs[name] = value; },
  addEventListener(type, fn) { this.handlers[type] = fn; },
  click() { this.handlers.click(); },
};
global.document = {
  documentElement: root,
  readyState: "complete",
  getElementById: (id) => (id === "theme-toggle" ? button : null),
  querySelectorAll: () => [],
  addEventListener: () => {},
};
global.window = {};

const states = [];
const snapshot = () => states.push({
  theme: "theme" in root.dataset ? root.dataset.theme : null,
  stored: storage.has("theme") ? storage.get("theme") : null,
  label: button.attrs["aria-label"] || null,
  glyph: button.textContent,
});

eval(prePaint);
const dataJs = root.dataset.js;
eval(siteJs);
snapshot();                 // initial: system
button.click(); snapshot(); // -> light
button.click(); snapshot(); // -> dark
button.click(); snapshot(); // -> system

// A fresh page load with a stored choice: pre-paint applies it alone.
storage.set("theme", "dark");
delete root.dataset.theme;
eval(prePaint);
const rehydrated = root.dataset.theme;

process.stdout.write(JSON.stringify({ dataJs, states, rehydrated }));
"""


def _run_harness() -> dict[str, Any]:
    result = subprocess.run(
        [_NODE or "node", "-e", _HARNESS, "--", scripts.PRE_PAINT_JS, scripts.SITE_JS],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(_NODE is None, reason="node not available for the JS behavioral harness")
def test_toggle_cycles_light_dark_system_and_persists() -> None:
    out = _run_harness()
    assert out["dataJs"] == "1"
    initial, first, second, third = out["states"]
    # Initial paint reflects "system" (no attribute, nothing stored).
    assert initial["theme"] is None and initial["stored"] is None
    assert "system" in initial["label"]
    # light -> dark -> system, persisted at each explicit step.
    assert (first["theme"], first["stored"]) == ("light", "light")
    assert "light" in first["label"]
    assert (second["theme"], second["stored"]) == ("dark", "dark")
    assert "dark" in second["label"]
    # Back to system: attribute AND stored key both removed.
    assert third["theme"] is None and third["stored"] is None
    assert "system" in third["label"]
    # Each state paints a distinct glyph on the button.
    glyphs = {state["glyph"] for state in out["states"]}
    assert len(glyphs) == 3


@pytest.mark.skipif(_NODE is None, reason="node not available for the JS behavioral harness")
def test_pre_paint_applies_a_stored_choice_before_first_paint() -> None:
    out = _run_harness()
    assert out["rehydrated"] == "dark"


# ---------------------------------------------------------------------------
# Reveal stagger (t5) — main#main children get .reveal + --reveal-delay,
# the hero excepted (it orchestrates itself)
# ---------------------------------------------------------------------------


def test_site_js_stamps_the_reveal_stagger_and_skips_the_hero() -> None:
    js = scripts.SITE_JS
    assert '"main"' in js
    assert "--reveal-delay" in js
    assert '"hero"' in js, "the hero must be excluded — it orchestrates itself"
    assert "60" in js, "stagger increment is 60ms per element"
    assert "12" in js, "stamping is capped at 12 elements"


_STAGGER_HARNESS = """
"use strict";
const siteJs = process.argv[1];
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
function makeEl(cls) {
  const classes = new Set(cls ? [cls] : []);
  return {
    classes,
    style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
    classList: { contains: (c) => classes.has(c), add: (c) => classes.add(c) },
  };
}
const heroEl = makeEl("hero");
const kids = [heroEl];
for (let i = 0; i < 15; i++) { kids.push(makeEl("")); }
const main = { children: kids };
global.document = {
  documentElement: { dataset: {} },
  readyState: "complete",
  getElementById: (id) => (id === "main" ? main : null),
  querySelectorAll: (sel) =>
    (sel === ".reveal" ? kids.filter((k) => k.classes.has("reveal")) : []),
  addEventListener: () => {},
};
global.window = {}; // no IntersectionObserver -> the fallback reveals immediately
eval(siteJs);
process.stdout.write(JSON.stringify({
  heroStamped: heroEl.classes.has("reveal"),
  heroRevealed: heroEl.classes.has("revealed"),
  stamped: kids.slice(1).map((k) => k.classes.has("reveal")),
  delays: kids.slice(1).map((k) => k.style.props["--reveal-delay"] || null),
  revealedCount: kids.filter((k) => k.classes.has("revealed")).length,
}));
"""


@pytest.mark.skipif(_NODE is None, reason="node not available for the JS behavioral harness")
def test_stagger_stamps_up_to_twelve_children_and_never_the_hero() -> None:
    result = subprocess.run(
        [_NODE or "node", "-e", _STAGGER_HARNESS, "--", scripts.SITE_JS],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    out = json.loads(result.stdout)
    assert out["heroStamped"] is False
    assert out["heroRevealed"] is False
    # 15 non-hero children: the first 12 are stamped, the rest left alone
    # (they simply render, never hidden).
    assert out["stamped"] == [True] * 12 + [False] * 3
    assert out["delays"][:3] == ["0ms", "60ms", "120ms"]
    assert out["delays"][11] == "660ms"
    assert out["delays"][12:] == [None, None, None]
    # Without IntersectionObserver the fallback reveals every stamped
    # element immediately — motion is decoration, never a gate on content.
    assert out["revealedCount"] == 12
