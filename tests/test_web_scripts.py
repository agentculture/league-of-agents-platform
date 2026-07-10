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
  querySelector: () => null,
  querySelectorAll: () => [],
  addEventListener: () => {},
};
global.window = { innerHeight: 900 };

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
    assert 'meta[http-equiv="refresh"]' in js, "the refresh refusal must stay wired"
    assert '" i]' not in js, "the selector case-flag throws on legacy engines"


_STAGGER_HARNESS = """
"use strict";
const siteJs = process.argv[1];
global.localStorage = { getItem: () => null, setItem: () => {}, removeItem: () => {} };
// Elements are given a rect 100px tall: the first 8 non-hero children
// intersect the viewport (top < innerHeight && bottom > 0), the rest sit
// below the fold — the stagger delay belongs only to the intersecting ones
// (a delayed scroll-time reveal would hold content invisible AFTER it
// entered the viewport).
function makeEl(cls, top) {
  const classes = new Set(cls ? [cls] : []);
  return {
    classes,
    style: { props: {}, setProperty(k, v) { this.props[k] = v; } },
    classList: { contains: (c) => classes.has(c), add: (c) => classes.add(c) },
    getBoundingClientRect: () => ({ top, bottom: top + 100 }),
  };
}
const heroEl = makeEl("hero", 0);
const kids = [heroEl];
for (let i = 0; i < 15; i++) { kids.push(makeEl("", 100 + i * 120)); }
const main = { children: kids };
let refreshMeta = null;     // flipped by the refresh run below
let selectorThrows = false; // flipped by the legacy-engine run below
global.document = {
  documentElement: { dataset: {} },
  readyState: "complete",
  getElementById: (id) => (id === "main" ? main : null),
  // A legacy engine: the attribute-selector case-flag (` i]`) is a
  // SyntaxError, as in real pre-2016 querySelector implementations.
  querySelector: (sel) => {
    if (selectorThrows || sel.indexOf('" i]') !== -1 || sel.indexOf("' i]") !== -1) {
      throw new SyntaxError("unsupported selector: " + sel);
    }
    return sel === 'meta[http-equiv="refresh"]' ? refreshMeta : null;
  },
  querySelectorAll: (sel) =>
    (sel === ".reveal" ? kids.filter((k) => k.classes.has("reveal")) : []),
  addEventListener: () => {},
};
global.window = { innerHeight: 900 }; // no IntersectionObserver -> fallback reveals immediately
eval(siteJs);
const firstRun = {
  heroStamped: heroEl.classes.has("reveal"),
  heroRevealed: heroEl.classes.has("revealed"),
  stamped: kids.slice(1).map((k) => k.classes.has("reveal")),
  delays: kids.slice(1).map((k) => k.style.props["--reveal-delay"] || null),
  revealedCount: kids.filter((k) => k.classes.has("revealed")).length,
};
// An auto-refreshing page (live watch page) must get NO stagger at all.
const freshKids = [makeEl("hero", 0)];
for (let i = 0; i < 5; i++) { freshKids.push(makeEl("", 100 + i * 120)); }
main.children = freshKids;
kids.length = 0; Array.prototype.push.apply(kids, freshKids);
refreshMeta = { httpEquiv: "refresh" };
eval(siteJs);
const refreshRun = {
  stampedCount: freshKids.filter((k) => k.classes.has("reveal")).length,
};
// A page restored mid-scroll: an element whose rect sits entirely above
// the viewport (bottom <= 0) is NOT on screen — a cascade delay stamped
// now would replay when the visitor scrolls back up to it.
refreshMeta = null;
const aboveEl = makeEl("", -300);  // bottom -200: fully above the viewport
const visibleEl = makeEl("", 100); // intersects the viewport
const scrollKids = [aboveEl, visibleEl];
main.children = scrollKids;
kids.length = 0; Array.prototype.push.apply(kids, scrollKids);
eval(siteJs);
const aboveRun = {
  aboveStamped: aboveEl.classes.has("reveal"),
  aboveDelay: aboveEl.style.props["--reveal-delay"] || null,
  visibleDelay: visibleEl.style.props["--reveal-delay"] || null,
};
// A future selector edit must never abort init on an old engine: with
// querySelector throwing on EVERYTHING, the throw reads as "no refresh
// meta found" and the stagger still runs.
selectorThrows = true;
const guardKids = [makeEl("hero", 0)];
for (let i = 0; i < 5; i++) { guardKids.push(makeEl("", 100 + i * 120)); }
main.children = guardKids;
kids.length = 0; Array.prototype.push.apply(kids, guardKids);
eval(siteJs);
const guardRun = {
  stampedCount: guardKids.filter((k) => k.classes.has("reveal")).length,
  revealedCount: guardKids.filter((k) => k.classes.has("revealed")).length,
};
process.stdout.write(JSON.stringify({ firstRun, refreshRun, aboveRun, guardRun }));
"""


def _run_stagger_harness() -> dict[str, Any]:
    result = subprocess.run(
        [_NODE or "node", "-e", _STAGGER_HARNESS, "--", scripts.SITE_JS],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(_NODE is None, reason="node not available for the JS behavioral harness")
def test_stagger_stamps_up_to_twelve_children_and_never_the_hero() -> None:
    out = _run_stagger_harness()["firstRun"]
    assert out["heroStamped"] is False
    assert out["heroRevealed"] is False
    # 15 non-hero children: the first 12 are stamped, the rest left alone
    # (they simply render, never hidden).
    assert out["stamped"] == [True] * 12 + [False] * 3
    assert out["delays"][:3] == ["0ms", "60ms", "120ms"]
    # Children 0..6 sit on-screen (top 100..820 < 900) and carry the
    # cascade delay; below-fold children are stamped but get NO delay —
    # their scroll-time reveal must start the instant they intersect.
    assert out["delays"][6] == "360ms"
    assert out["delays"][7:12] == [None] * 5
    assert out["delays"][12:] == [None, None, None]
    # Without IntersectionObserver the fallback reveals every stamped
    # element immediately — motion is decoration, never a gate on content.
    assert out["revealedCount"] == 12


@pytest.mark.skipif(_NODE is None, reason="node not available for the JS behavioral harness")
def test_auto_refreshing_pages_get_no_stagger_at_all() -> None:
    """The live watch page reloads every 5s via meta refresh — replaying the
    entrance cascade each reload would blink the transcript a visitor is
    reading. With a refresh meta present, initStagger refuses entirely."""
    out = _run_stagger_harness()["refreshRun"]
    assert out["stampedCount"] == 0


@pytest.mark.skipif(_NODE is None, reason="node not available for the JS behavioral harness")
def test_stagger_gives_no_delay_to_elements_fully_above_the_viewport() -> None:
    """A page restored mid-scroll (anchor jump, back/forward) can have
    children entirely above the viewport (bottom <= 0). They are not on
    screen — a cascade delay stamped now would fire later, when the visitor
    scrolls back up. They get ``.reveal`` with no delay, exactly like
    below-fold elements."""
    out = _run_stagger_harness()["aboveRun"]
    assert out["aboveStamped"] is True
    assert out["aboveDelay"] is None
    assert out["visibleDelay"] == "60ms"


@pytest.mark.skipif(_NODE is None, reason="node not available for the JS behavioral harness")
def test_a_throwing_query_selector_never_aborts_the_stagger() -> None:
    """Legacy engines throw SyntaxError on selectors they cannot parse (the
    attribute case-flag ``i`` did exactly that). A throw from the
    refresh-meta lookup must read as "no refresh meta found" — never abort
    init and take the whole cascade down with it."""
    out = _run_stagger_harness()["guardRun"]
    assert out["stampedCount"] == 5
    assert out["revealedCount"] == 5
