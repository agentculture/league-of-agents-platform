"""Playwright (Chromium) checks of the /evals pages; see conftest.py for the server.

Covers 1440 and 390 px in light and dark, keyboard turn stepping, labelled controls,
sticky boards, reasoning on demand, live advance and streaming.
"""

from __future__ import annotations

import pytest

from tests.e2e.conftest import EVAL_ID, EVIDENCE, FINISHED_TAG, LIVE_TAG

VIEWPORTS = {"1440": (1440, 900), "390": (390, 844)}
THEMES = ["light", "dark"]
REPLAY = f"/evals/{EVAL_ID}/{FINISHED_TAG}/"
LIVE_REPLAY = f"/evals/{EVAL_ID}/{LIVE_TAG}/"

combos = pytest.mark.parametrize(
    "width,theme", [(w, t) for w in VIEWPORTS for t in THEMES], ids=lambda v: str(v)
)


def _prepare(page, width="1440", theme="light"):
    w, h = VIEWPORTS[width]
    page.set_viewport_size({"width": w, "height": h})
    page.emulate_media(color_scheme=theme)
    errors = []
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))

    def on_console(msg):
        if msg.type == "error" and "favicon" not in (msg.location.get("url") or "").lower():
            errors.append(f"console: {msg.text} {msg.location.get('url')}")

    page.on("console", on_console)
    return errors


def _open_replay(page, path=REPLAY):
    page.goto(path)
    page.wait_for_function(
        "(() => { const t = document.getElementById('turn'); return !!t && +t.max > 0; })()"
    )
    page.wait_for_selector("#board *")


def _open_index(page):
    page.goto("/evals/")
    page.wait_for_function("document.querySelector('#ledger').children.length > 0")
    page.wait_for_function("document.querySelector('#benchmarks').children.length > 0")


def _turn(page) -> int:
    return int(page.locator("#clockt").inner_text())


def _max(page) -> int:
    return int(page.locator("#turn").get_attribute("max"))


def _no_hscroll(page):
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def _shot(page, request, name, width, theme):
    if request.config.getoption("--update-evidence"):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        page.screenshot(
            path=str(EVIDENCE / f"2026-11-09-evals-{name}-{width}-{theme}.png"), full_page=True
        )


@combos
def test_index_loads_clean(page, base_url, request, width, theme):
    errors = _prepare(page, width, theme)
    _open_index(page)
    page.wait_for_timeout(300)
    assert errors == []
    assert page.locator("h1").inner_text() == "Evals"
    _shot(page, request, "index", width, theme)


@pytest.mark.parametrize(
    "width",
    [
        "1440",
        "390",
    ],
)
def test_index_has_no_horizontal_scroll(page, base_url, width):
    _prepare(page, width)
    _open_index(page)
    _no_hscroll(page)


@combos
def test_replay_loads_clean(page, base_url, request, width, theme):
    errors = _prepare(page, width, theme)
    _open_replay(page)
    page.wait_for_timeout(300)
    assert errors == []
    _no_hscroll(page)
    assert page.locator(".slice").count() > 0
    _shot(page, request, "replay", width, theme)


def test_controls_have_accessible_names(page, base_url):
    _prepare(page)
    _open_replay(page)
    unnamed = page.evaluate(
        """() => [...document.querySelectorAll('button, input, select, textarea, [role=button]')]
        .filter(el => !el.hidden && el.type !== 'hidden')
        .filter(el => {
          const labelled = el.getAttribute('aria-label') || el.getAttribute('title');
          const by = el.getAttribute('aria-labelledby');
          const byEl = by && document.getElementById(by.split(' ')[0]);
          const lab = el.labels && [...el.labels].some(l => l.textContent.trim());
          const text = el.tagName !== 'INPUT' && el.textContent.trim();
          const titled = el.querySelector('title') && el.querySelector('title').textContent.trim();
          return !(labelled || (byEl && byEl.textContent.trim()) || lab || text || titled);
        }).map(el => el.outerHTML.slice(0, 120))"""
    )
    assert unnamed == []
    for sel in ("#prev", "#play", "#next", "#turn"):
        assert page.locator(sel).get_attribute("aria-label")
    assert page.locator("[data-view]").count() == 2


def test_keyboard_turn_stepping(page, base_url):
    _prepare(page)
    _open_replay(page)
    page.evaluate("document.activeElement && document.activeElement.blur()")
    n = _max(page)
    assert _turn(page) == n
    page.keyboard.press("Home")
    assert _turn(page) == 0
    page.keyboard.press("ArrowRight")
    assert _turn(page) == 1
    page.keyboard.press("ArrowRight")
    assert _turn(page) == 2
    page.keyboard.press("ArrowLeft")
    assert _turn(page) == 1
    page.keyboard.press("End")
    assert _turn(page) == n
    page.keyboard.press("ArrowLeft")
    assert _turn(page) == n - 1
    assert page.locator("#turn").input_value() == str(n - 1)


def _open_slice(page, width):
    _prepare(page, width)
    _open_replay(page)
    page.keyboard.press("Home")
    ticks = page.locator(".slice .hl")
    assert ticks.count() >= 4
    ticks.nth(3).click()
    page.wait_for_selector(".slice.active .detail")
    page.wait_for_timeout(700)  # let smooth scrolling settle
    return page.evaluate("window.innerHeight")


@pytest.mark.parametrize("width", ["1440", "390"])
def test_slice_click_keeps_board_and_moment_in_view(page, base_url, width):
    height = _open_slice(page, width)
    board = page.locator("#board").bounding_box()
    assert board["y"] >= -1 and board["y"] + board["height"] <= height + 1, board
    detail = page.locator(".slice.active .detail").bounding_box()
    assert detail["y"] < height and detail["y"] + detail["height"] > board["y"] + board["height"]
    assert page.locator(".slice.active .seat").first.is_visible()


@pytest.mark.parametrize(
    "width",
    [
        "1440",
        "390",
    ],
)
def test_open_moment_is_not_covered_by_sticky_board(page, base_url, width):
    _open_slice(page, width)
    covered = page.evaluate(
        """() => { const r = document.querySelector('.slice.active .seat').getBoundingClientRect();
        const el = document.elementFromPoint(r.x + r.width / 2, r.y + r.height / 2);
        return !el || !el.closest('.seat'); }"""
    )
    assert not covered


def test_reasoning_loads_on_demand(page, base_url):
    _prepare(page)
    requests = []
    page.on("request", lambda r: requests.append(r.url) if "/thinking/" in r.url else None)
    _open_replay(page)
    n = _max(page)
    page.wait_for_timeout(300)
    assert [u for u in requests if f"{FINISHED_TAG}-t3.json" in u] == []
    page.keyboard.press("Home")
    assert not any(f"{FINISHED_TAG}-t1.json" in u for u in requests)
    for _ in range(3):
        page.keyboard.press("ArrowRight")
    assert _turn(page) == 3
    page.wait_for_selector("#think pre.reasoning")
    page.wait_for_function("document.querySelector('#think pre').textContent.includes('T3 plan 1')")
    assert any(u.endswith(f"/thinking/{FINISHED_TAG}-t3.json") for u in requests)
    assert not any(u.endswith(f"/thinking/{FINISHED_TAG}-t{n - 3}.json") for u in requests)
    # Claude seats: the claude -p note, no reasoning fetch
    seen = len(requests)
    page.locator('[data-side="red"]').first.click()
    note = page.locator("#think .asym")
    note.wait_for()
    assert "claude -p" in note.inner_text()
    assert len(requests) == seen


def _live_page(page):
    errors = _prepare(page)
    page.clock.install()
    _open_replay(page, LIVE_REPLAY)
    return errors


def test_live_advance_follows_latest_but_not_scrubbed(page, base_url, live):
    _live_page(page)
    n = _max(page)
    assert _turn(page) == n
    assert live.append_turn() == n + 1
    page.clock.run_for(60_000)
    page.wait_for_function(f"+document.getElementById('clockt').textContent === {n + 1}")
    assert _max(page) == n + 1
    # scrub back one turn; a newly exported turn must not move the viewer
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("ArrowLeft")
    assert _turn(page) == n
    assert live.append_turn() == n + 2
    page.clock.run_for(60_000)
    page.wait_for_function(f"+document.getElementById('turn').max === {n + 2}")
    assert _turn(page) == n


def test_streaming_panel_appears_and_disappears(page, base_url, live):
    _live_page(page)
    n = _max(page)
    stream = page.locator("#stream")
    live.stream(["blue-1"], n + 1)
    page.clock.run_for(10_000)
    stream.get_by_text(f"Answering turn {n + 1} now").wait_for()
    assert "probing the east gate" in stream.inner_text()
    assert "MOVE 3,4" in stream.inner_text()
    live.stream([], n + 1)
    page.clock.run_for(10_000)
    page.wait_for_function(
        "!document.getElementById('stream').textContent.includes('Answering turn')"
    )
    assert "Answering turn" not in stream.inner_text()
