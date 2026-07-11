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

from league_site.web import hero, scripts, theme
from league_site.web.http import WSGIApp, http_app
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
    link_at = head.find(f'<link rel="stylesheet" href="{asset_url("theme.css")}">')
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
    assert f'<script defer src="{asset_url("site.js")}"></script>' in head


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


# ---------------------------------------------------------------------------
# t7 — the first-party board sim that plays the hero scene turn by turn
# ---------------------------------------------------------------------------
#
# The sim lives inside SITE_JS. It stages per-team orders and resolves them
# against the hero's authored SVG scene (league_site/web/hero.py): units move
# one cell per turn, harvesters gather→deliver, scouts/defenders capture
# posts (flipping data-owner), the score ticks by the real formula
# (missions + control + resources), and the ticker posts terse role-flavored
# lines. It activates ONLY under html[data-js] AND
# prefers-reduced-motion: no-preference; otherwise the poster frame stands.

#: Every id / class / data-attribute the sim drives. Each must appear in
#: BOTH SITE_JS and hero.HERO_HTML — so a rename in the scene fails loudly
#: here instead of silently turning the sim into a no-op.
_SIM_INTERFACE = (
    "hero-svg",
    "hp-unit",
    "hp-post",
    "hp-res",
    "hero-gather",
    "hero-turn",
    "hero-ticker",
    "hero-score-",
    "data-owner",
    "data-term",
    "data-team",
    "data-role",
)


def test_site_js_carries_a_board_sim_wired_into_init() -> None:
    """The sim is its own init path, called from init() alongside the
    existing theme-toggle / stagger / reveal behaviors — one IIFE, no new
    globals."""
    js = scripts.SITE_JS
    assert "initSim" in js, "the sim must exist as its own init path"
    assert re.search(
        r"function init\(\)\s*\{[^}]*initSim\(\)", js, re.S
    ), "init() must call initSim()"
    # Still exactly one strict-mode IIFE, no console noise, no new globals.
    assert js.count('"use strict"') == 1
    assert "console." not in js


def test_sim_activates_only_under_data_js_and_no_preference() -> None:
    """The sim is a motion enhancement: it runs only when JS is on
    (html[data-js]) AND motion is welcome (prefers-reduced-motion:
    no-preference), checked via window.matchMedia at init. With either gate
    absent, the authored poster frame is left untouched."""
    js = scripts.SITE_JS
    assert "matchMedia" in js
    assert "prefers-reduced-motion: reduce" in js
    assert "dataset.js" in js
    assert ".matches" in js


def test_sim_stops_cleanly_when_reduced_motion_flips_on_mid_session() -> None:
    """If the media query flips to reduce mid-session, the sim tears down
    its turn loop and restores the poster — it must register a change
    listener and be able to clear its interval."""
    js = scripts.SITE_JS
    assert "addListener" in js or "addEventListener" in js
    assert "clearInterval" in js


def test_sim_only_touches_the_documented_hero_interface() -> None:
    """Cross-check: every hook the sim references is one hero.py actually
    emits. Drift in either file trips this."""
    js = scripts.SITE_JS
    for token in _SIM_INTERFACE:
        assert token in js, f"sim never references {token}"
        assert token in hero.HERO_HTML, f"hero.py no longer emits {token} — interface drift"


def test_sim_moves_by_rewriting_transform_on_the_documented_grid() -> None:
    """Movement is a transform-attribute rewrite on the documented cell
    geometry (hero.py: 40px cells, cell center (60 + 40*col, 80 + 40*row));
    the sim never sets the CSS transform *property*, which would
    permanently override the attribute."""
    js = scripts.SITE_JS
    assert "setAttribute" in js and "transform" in js and "translate(" in js
    assert "60 + 40" in js and "80 + 40" in js
    # The geometry the sim computes in is the one hero.py documents.
    assert "(60 + 40*col, 80 + 40*row)" in (hero.__doc__ or "")


def test_sim_flips_ownership_and_ticks_the_real_score_formula() -> None:
    """Capture flips data-owner (CSS recolors the ring); the score writes
    every term of the real formula plus the honest total."""
    js = scripts.SITE_JS
    assert 'setAttribute("data-owner"' in js
    for term in ("missions", "control", "resources", "total"):
        assert term in js, term


def test_sim_varies_between_loops_with_a_seeded_prng() -> None:
    """Consecutive loops differ — a tiny PRNG seeded from Date.now()."""
    assert "Date.now" in scripts.SITE_JS


def test_sim_snapshots_the_poster_and_restores_it_on_reset() -> None:
    """The authored poster is the truth at load: the sim snapshots every
    attribute it mutates and restores exactly that on each loop reset (via
    a paced setInterval turn loop), so the still is never corrupted."""
    js = scripts.SITE_JS
    assert "removeAttribute" in js
    assert "setInterval" in js


def test_the_grown_first_party_js_still_fits_the_budget() -> None:
    """The sim is real weight, but combined first-party JS still clears the
    16KB ceiling (the pre-paint snippet counts too)."""
    combined = len(scripts.SITE_JS.encode("utf-8")) + len(scripts.PRE_PAINT_JS.encode("utf-8"))
    assert combined <= theme.JS_BUDGET_BYTES


# --- behavioral harness: actually play the board and audit faithfulness ----
#
# Builds a stub SVG scene mirroring hero.HERO_HTML's authored cells, evals
# SITE_JS (which starts a turn loop via a stubbed setInterval we drive by
# hand), and audits the invariants the real engine guarantees: every move is
# to an adjacent cell (no teleports), pieces stay on the 8x5 field,
# data-owner only ever holds a legal value, the score always satisfies
# missions + control + resources = total with control equal to the live
# owned-post count, and a loop reset restores the authored poster exactly.

_SIM_HARNESS = r"""
"use strict";
var siteJs = process.argv[1];

var clock = 20260711;
global.Date = { now: function () { return clock; } };

var intervalCb = null, timeoutCb = null;
global.window = {
  innerHeight: 900,
  matchMedia: function (q) {
    return {
      media: q, matches: false,
      addEventListener: function () {}, addListener: function () {}
    };
  },
  setInterval: function (fn) { intervalCb = fn; return 1; },
  clearInterval: function () { intervalCb = null; },
  setTimeout: function (fn) { timeoutCb = fn; return 2; },
  clearTimeout: function () { timeoutCb = null; },
};
global.localStorage = {
  getItem: function () { return null; }, setItem: function () {}, removeItem: function () {}
};

function El(attrs) {
  var a = {};
  for (var k in attrs) {
    if (attrs.hasOwnProperty(k) && k !== "__text") { a[k] = String(attrs[k]); }
  }
  return {
    _a: a,
    style: {},
    _kids: [],
    textContent: attrs && attrs.__text != null ? String(attrs.__text) : "",
    innerHTML: "",
    getAttribute: function (n) { return this._a.hasOwnProperty(n) ? this._a[n] : null; },
    setAttribute: function (n, v) { this._a[n] = String(v); },
    removeAttribute: function (n) { delete this._a[n]; },
    querySelectorAll: function (sel) { return sel === "[data-term]" ? this._kids : []; },
  };
}
function at(col, row, extra) {
  extra = extra || {};
  extra.transform = "translate(" + (60 + 40 * col) + "," + (80 + 40 * row) + ")";
  return El(extra);
}

var units = [
  at(4, 1, { "data-unit": "blue-u1", "data-team": "blue", "data-role": "scout" }),
  at(2, 2, { "data-unit": "blue-u2", "data-team": "blue", "data-role": "harvester" }),
  at(1, 2, { "data-unit": "blue-u3", "data-team": "blue", "data-role": "defender" }),
  at(3, 3, { "data-unit": "red-u1", "data-team": "red", "data-role": "scout" }),
  at(6, 1, { "data-unit": "red-u2", "data-team": "red", "data-role": "harvester" }),
  at(5, 3, { "data-unit": "red-u3", "data-team": "red", "data-role": "defender" }),
];
var posts = [
  at(1, 1, { "data-post": "west", "data-owner": "blue" }),
  at(4, 2, { "data-post": "mid", "data-owner": "none" }),
  at(6, 3, { "data-post": "east", "data-owner": "red" }),
];
var resources = [
  at(2, 3, { "data-res": "r1" }), at(5, 1, { "data-res": "r2" }), at(3, 0, { "data-res": "r3" })
];
var beams = [
  El({ "data-team": "blue", x1: 0, y1: 0, x2: 0, y2: 0 }),
  El({ "data-team": "red", x1: 0, y1: 0, x2: 0, y2: 0 })
];

function term(t, v) { return El({ "data-term": t, __text: v }); }
var scoreBlue = El({});
scoreBlue._kids = [term("missions", 2), term("control", 1), term("resources", 3), term("total", 6)];
var scoreRed = El({});
scoreRed._kids = [term("missions", 1), term("control", 1), term("resources", 2), term("total", 4)];
var turnEl = El({ __text: 12 });
var tickerEl = El({}); tickerEl.innerHTML = "authored";

var svg = {
  style: {},
  querySelectorAll: function (sel) {
    if (sel === ".hp-unit") { return units; }
    if (sel === ".hp-post") { return posts; }
    if (sel === ".hp-res") { return resources; }
    if (sel === ".hero-gather") { return beams; }
    return [];
  },
};
global.document = {
  documentElement: { dataset: { js: "1" } },
  readyState: "complete",
  addEventListener: function () {},
  querySelector: function (sel) { return sel === ".hero-svg" ? svg : null; },
  querySelectorAll: function () { return []; },
  getElementById: function (id) {
    if (id === "hero-turn") { return turnEl; }
    if (id === "hero-ticker") { return tickerEl; }
    if (id === "hero-score-blue") { return scoreBlue; }
    if (id === "hero-score-red") { return scoreRed; }
    return null;
  },
};

function cellOf(el) {
  var re = /translate\(\s*(-?\d+(?:\.\d+)?)[ ,]+(-?\d+(?:\.\d+)?)/;
  var m = re.exec(el.getAttribute("transform"));
  return [Math.round((+m[1] - 60) / 40), Math.round((+m[2] - 80) / 40)];
}
var initial = units.map(function (u) { return u.getAttribute("transform"); });

eval(siteJs);

var facts = {
  started: !!intervalCb, teleport: false, outOfBounds: false, badOwner: false,
  scoreBroken: false, controlWrong: false, moved: false, ownerFlipped: false,
  tickerChanged: false, resetRestoredOk: false, turnAdvanced: false,
};
function owners() {
  return posts.map(function (p) { return p.getAttribute("data-owner"); }).join(",");
}
function auditScore() {
  [["blue", scoreBlue], ["red", scoreRed]].forEach(function (pair) {
    var cells = {};
    pair[1]._kids.forEach(function (k) { cells[k.getAttribute("data-term")] = +k.textContent; });
    if (cells.missions + cells.control + cells.resources !== cells.total) {
      facts.scoreBroken = true;
    }
    var ctrl = 0;
    posts.forEach(function (p) { if (p.getAttribute("data-owner") === pair[0]) { ctrl += 1; } });
    if (cells.control !== ctrl) { facts.controlWrong = true; }
  });
}

var prev = units.map(cellOf);
var prevOwners = owners();
var startTurn = +turnEl.textContent;
if (facts.started) {
  for (var t = 0; t < 20; t++) {
    clock += 2750;
    intervalCb();
    var now = units.map(cellOf);
    for (var i = 0; i < units.length; i++) {
      var dc = Math.abs(now[i][0] - prev[i][0]), dr = Math.abs(now[i][1] - prev[i][1]);
      if (dc + dr > 1) { facts.teleport = true; }
      if (now[i][0] < 0 || now[i][0] > 7 || now[i][1] < 0 || now[i][1] > 4) {
        facts.outOfBounds = true;
      }
      if (dc + dr > 0) { facts.moved = true; }
    }
    prev = now;
    posts.forEach(function (p) {
      if (["blue", "red", "none"].indexOf(p.getAttribute("data-owner")) === -1) {
        facts.badOwner = true;
      }
    });
    var no = owners();
    if (no !== prevOwners) { facts.ownerFlipped = true; }
    prevOwners = no;
    if (tickerEl.innerHTML !== "authored") { facts.tickerChanged = true; }
    if (+turnEl.textContent > startTurn) { facts.turnAdvanced = true; }
    auditScore();
    if (timeoutCb) {
      var cb = timeoutCb; timeoutCb = null; cb();
      var ok = true;
      for (var j = 0; j < units.length; j++) {
        if (units[j].getAttribute("transform") !== initial[j]) { ok = false; }
      }
      if (ok && posts[1].getAttribute("data-owner") === "none") { facts.resetRestoredOk = true; }
      prev = units.map(cellOf);
      prevOwners = owners();
    }
  }
}
process.stdout.write(JSON.stringify(facts));
"""


def _run_sim_harness() -> dict[str, Any]:
    result = subprocess.run(
        [_NODE or "node", "-e", _SIM_HARNESS, "--", scripts.SITE_JS],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout)


@pytest.mark.skipif(_NODE is None, reason="node not available for the JS behavioral harness")
def test_sim_plays_the_board_faithfully_turn_by_turn() -> None:
    """Drive the real sim over 20 turns against a stub scene and audit the
    engine-faithful invariants: it starts, it actually moves pieces, every
    move is to an adjacent cell (no teleports), pieces stay on the field,
    posts flip ownership only to legal values, the ticker posts lines, and
    the score always satisfies missions + control + resources = total with
    control tracking the live owned-post count."""
    f = _run_sim_harness()
    assert f["started"], "sim never started its turn loop"
    assert f["moved"], "no piece ever moved — the board is not playing"
    assert f["turnAdvanced"], "turn counter never advanced"
    assert not f["teleport"], "a piece jumped more than one cell in a turn"
    assert not f["outOfBounds"], "a piece left the 8x5 field"
    assert not f["badOwner"], "a post took an illegal data-owner value"
    assert not f["scoreBroken"], "score total drifted from missions + control + resources"
    assert not f["controlWrong"], "control term did not track the live owned-post count"
    assert f["ownerFlipped"], "no post was ever captured"
    assert f["tickerChanged"], "the ticker never posted a live line"


@pytest.mark.skipif(_NODE is None, reason="node not available for the JS behavioral harness")
def test_sim_reset_restores_the_authored_poster_exactly() -> None:
    """A full loop ends in a quiet reset that restores every mutated
    attribute to its authored value — the poster is idempotent, never
    corrupted by a prior loop."""
    assert _run_sim_harness()["resetRestoredOk"], "loop reset did not restore the poster"
