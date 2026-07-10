"""Tests for :mod:`league_site.web.hero` — the landing page's signature scene.

The dazzle pass's t5 contract (see ``docs/design/dazzle-direction.md``):

* The hero — copy column plus the living arena board — renders **exactly
  once**, as the first child of ``<main>``, on the landing paths (``/`` and
  ``/index``) and **nowhere else**. Raw passthrough surfaces (``*.md``,
  ``/llms.txt``, ``/front``) stay byte-identical to the unwrapped app.
* Every color in the fragment derives from the live design tokens —
  ``var(--…)`` only, **zero** hex/rgb/hsl literals — so flipping the theme
  toggle re-skins the whole scene without a reload.
* All motion lives inside one ``@media (prefers-reduced-motion:
  no-preference)`` block; with reduced motion requested the authored markup
  *is* the still composition (pieces mid-clash, flare at half-bloom, score
  visible) — a poster frame, not an empty box.
* The board moves in **discrete turn-steps**: percentage-hold keyframes
  (hold, step, settle) on ``transform: translate``, a clash flare, a score
  tick, a ~12s seamless loop, and a ~1.2s load orchestration before the loop
  starts.
* Exactly one ``<h1>`` on the landing page — the hero's headline; the
  landing markdown's own ``# League of Agents`` heading is stripped from the
  rendered (HTML) body only, never from the raw ``.md`` bytes.
* CTAs name the action and point where the direction says: **Play a match**
  → ``/docs``, **See the leaderboard** → ``/leaderboard``.
"""

from __future__ import annotations

import re
from typing import Any

from league_site.web import hero
from league_site.web.http import WSGIApp, http_app
from league_site.web.shell import FooterSlotRegistry, with_shell

_HERO_MARKER = '<section class="hero"'
_LANDING_PATHS = ("/", "/index")
_NON_LANDING_PAGES = ("/docs", "/about", "/architecture")


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


def _page(path: str) -> str:
    status, headers, body = _get(_shelled(), path)
    assert status == "200 OK", path
    assert headers["Content-Type"] == "text/html; charset=utf-8", path
    return body.decode("utf-8")


# ---------------------------------------------------------------------------
# Placement: landing paths only, exactly once, first child of <main>
# ---------------------------------------------------------------------------


def test_hero_renders_exactly_once_on_each_landing_path() -> None:
    for path in _LANDING_PATHS:
        text = _page(path)
        assert text.count(_HERO_MARKER) == 1, path
        assert hero.HERO_HTML in text, path


def test_hero_is_the_first_child_of_main() -> None:
    for path in _LANDING_PATHS:
        text = _page(path)
        main_body = text.split('<main id="main" class="wrap">', 1)[1]
        assert main_body.lstrip().startswith(_HERO_MARKER), path


def test_hero_is_absent_from_every_non_landing_page() -> None:
    for path in _NON_LANDING_PAGES:
        text = _page(path)
        assert _HERO_MARKER not in text, path
        assert "hero-board" not in text, path


def test_hero_is_absent_from_unknown_and_non_markdown_paths() -> None:
    shelled = _shelled()
    for path in ("/leaderboard", "/sitemap.xml", "/nope-not-a-real-page"):
        _, _, body = _get(shelled, path)
        assert _HERO_MARKER.encode("utf-8") not in body, path


def test_raw_passthrough_surfaces_stay_byte_identical_and_hero_free() -> None:
    """The landing's raw ``.md`` (and the other agent-facing raw surfaces)
    must not change by a single byte when the shell — hero included — wraps
    the app. Reuses the byte-identity contract from test_web_theme_shell."""
    inner = http_app()
    shelled = with_shell(inner, footer_slots=FooterSlotRegistry())
    for path in ("/index.md", "/llms.txt", "/front"):
        _, inner_headers, inner_body = _get(inner, path)
        _, shelled_headers, shelled_body = _get(shelled, path)
        assert shelled_body == inner_body, path
        assert shelled_headers["Content-Type"] == inner_headers["Content-Type"], path
        assert _HERO_MARKER.encode("utf-8") not in shelled_body, path
        assert b"hero-board" not in shelled_body, path


# ---------------------------------------------------------------------------
# Theme-nativeness: var(...) only — zero color literals of any kind
# ---------------------------------------------------------------------------


def test_hero_contains_no_color_literals_anywhere() -> None:
    """Every color must ride the live tokens so the theme toggle re-skins
    the scene without a reload. No hex (no ``#`` at all — also keeps the
    fragment free of fragile id selectors), no rgb()/hsl()/hwb(), and no
    var() fallbacks smuggling a literal in."""
    assert "#" not in hero.HERO_HTML
    assert re.search(r"\brgba?\(", hero.HERO_HTML) is None
    assert re.search(r"\bhsla?\(", hero.HERO_HTML) is None
    assert re.search(r"\bhwb\(", hero.HERO_HTML) is None
    # No fallback values inside var() — the tokens are always defined.
    assert re.search(r"var\(--[\w-]+\s*,", hero.HERO_HTML) is None


def test_hero_scene_derives_every_role_from_the_documented_tokens() -> None:
    """The direction assigns tokens to roles: grid --border, active lane
    --border-strong, pieces --text-muted with the active piece --accent,
    clash bloom --accent-glow, board base --surface."""
    for token in (
        "var(--border)",
        "var(--border-strong)",
        "var(--text-muted)",
        "var(--accent)",
        "var(--accent-glow)",
        "var(--surface)",
        "var(--surface-2)",
        "var(--font-mono)",
    ):
        assert token in hero.HERO_HTML, token


# ---------------------------------------------------------------------------
# Motion: everything animated inside the reduced-motion guard; the still
# composition is the authored state
# ---------------------------------------------------------------------------


def test_all_hero_motion_lives_inside_the_reduced_motion_guard() -> None:
    guard = "@media (prefers-reduced-motion: no-preference)"
    assert hero.HERO_HTML.count(guard) == 1
    before = hero.HERO_HTML.split(guard, 1)[0]
    for motion_token in ("@keyframes", "animation", "transition"):
        assert motion_token not in before, motion_token


def test_reduced_motion_still_is_a_composed_poster_frame() -> None:
    """With every guarded rule inert, the *unguarded* styles must already
    hold the still: flare frozen at partial bloom (static opacity +
    scale), the incremented score visible (the pre-clash score hidden)."""
    before = hero.HERO_HTML.split("@media (prefers-reduced-motion: no-preference)", 1)[0]
    ring_rule = re.search(r"\.hero-flare-ring\s*\{([^}]*)\}", before)
    assert ring_rule is not None
    assert "opacity" in ring_rule.group(1)
    assert "scale" in ring_rule.group(1)
    pre_rule = re.search(r"\.hero-score-pre\s*\{([^}]*)\}", before)
    assert pre_rule is not None
    assert "opacity: 0" in pre_rule.group(1)


def test_board_moves_in_discrete_turn_steps_on_a_12s_loop() -> None:
    """Turn-step rhythm: percentage-hold keyframes (``N%, M% { transform:
    translate(...) }`` — hold, then snap to the next cell) rather than a
    continuous glide, looping seamlessly every 12s after a ~1.2s
    orchestrated entry."""
    holds = re.findall(r"[\d.]+%,\s*[\d.]+%\s*\{\s*transform:\s*translate\(", hero.HERO_HTML)
    assert len(holds) >= 8, "expected hold-then-step keyframes on the pieces"
    assert re.search(r"12s\s+[\w-]+\([^)]*\)\s+1\.2s\s+infinite", hero.HERO_HTML) or (
        "12s" in hero.HERO_HTML and "1.2s" in hero.HERO_HTML and "infinite" in hero.HERO_HTML
    )
    for piece_keyframes in ("hero-step-a", "hero-step-b", "hero-step-c", "hero-step-d"):
        assert f"@keyframes {piece_keyframes}" in hero.HERO_HTML, piece_keyframes


def test_clash_flare_and_score_tick_are_wired() -> None:
    assert "@keyframes hero-flare" in hero.HERO_HTML
    assert "hero-flare-glow" in hero.HERO_HTML
    assert "1 — 1" in hero.HERO_HTML
    # The incremented digit rides an accent tspan, so the post-clash score
    # reads "2 — 1" with the "2" lit.
    assert ">2</tspan> — 1<" in hero.HERO_HTML
    assert "hero-score-pre" in hero.HERO_HTML
    assert "hero-score-post" in hero.HERO_HTML


def test_load_orchestration_sequences_eyebrow_headline_grid_pieces() -> None:
    """One orchestrated entry (~1.2s): eyebrow → headline → grid draws
    itself (stroke-dashoffset) → pieces enter, loop begins."""
    guarded = hero.HERO_HTML.split("@media (prefers-reduced-motion: no-preference)", 1)[1]
    assert "@keyframes hero-draw" in guarded
    assert "stroke-dashoffset" in guarded
    eyebrow_at = guarded.find(".hero-eyebrow")
    headline_at = guarded.find(".hero-headline")
    assert eyebrow_at != -1 and headline_at != -1
    assert "animation" in guarded[eyebrow_at : eyebrow_at + 200]
    assert "animation" in guarded[headline_at : headline_at + 200]


# ---------------------------------------------------------------------------
# Semantics and copy
# ---------------------------------------------------------------------------


def test_exactly_one_h1_on_the_landing_page_and_it_is_the_hero_headline() -> None:
    for path in _LANDING_PATHS:
        text = _page(path)
        h1s = re.findall(r"<h1[\s>]", text)
        assert len(h1s) == 1, (path, h1s)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
        assert h1 is not None, path
        assert "ARENA" in h1.group(1), path


def test_non_landing_pages_keep_their_own_single_h1() -> None:
    for path in _NON_LANDING_PAGES:
        text = _page(path)
        assert len(re.findall(r"<h1[\s>]", text)) == 1, path


def test_headline_and_eyebrow_carry_the_direction_copy_verbatim() -> None:
    assert "TURN 1 — YOUR MOVE" in hero.HERO_HTML
    assert '<span class="hero-accent">ARENA</span>' in hero.HERO_HTML
    headline = re.search(r"<h1[^>]*>(.*?)</h1>", hero.HERO_HTML, re.S)
    assert headline is not None
    flattened = re.sub(r"<[^>]+>", "", headline.group(1)).strip()
    assert flattened == "AN ARENA FOR HUMANS AND AGENTS"


def test_ctas_name_the_action_and_point_at_docs_and_leaderboard() -> None:
    play = re.search(r'<a class="button"[^>]*href="([^"]+)"[^>]*>Play a match</a>', hero.HERO_HTML)
    assert play is not None
    assert play.group(1) == "/docs"
    board = re.search(
        r'<a class="button[^"]*"[^>]*href="([^"]+)"[^>]*>See the leaderboard</a>',
        hero.HERO_HTML,
    )
    assert board is not None
    assert board.group(1) == "/leaderboard"


def test_hero_board_is_decorative_and_the_section_is_labelled() -> None:
    assert re.search(r'<section class="hero" aria-label="[^"]+">', hero.HERO_HTML)
    board = re.search(r'<div class="hero-board"[^>]*>', hero.HERO_HTML)
    assert board is not None
    assert 'aria-hidden="true"' in board.group(0)


# ---------------------------------------------------------------------------
# Size: inline HTML, but page weight still matters for the Lighthouse gate
# ---------------------------------------------------------------------------


def test_hero_fragment_stays_within_its_8kb_allowance() -> None:
    assert len(hero.HERO_HTML.encode("utf-8")) <= 8 * 1024
