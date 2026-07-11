"""Tests for :mod:`league_site.web.hero` — the strategy-game board scene.

The sibling-of-agentculture.org pass's t6 contract (see
``docs/plans/2026-07-11-league-of-agents-ai-now-moves-like-a-sibling-of-ag.md``):

* The hero — copy column plus the board — renders **exactly once**, as the
  first child of ``<main>``, on the landing paths (``/`` and ``/index``)
  and **nowhere else**. Raw passthrough surfaces (``*.md``, ``/llms.txt``,
  ``/front``) stay byte-identical to the unwrapped app.
* Every color in the fragment derives from the live design tokens —
  ``var(--…)`` only, **zero** hex/rgb/hsl literals — so flipping the theme
  toggle re-skins the whole scene without a reload.
* The SVG scene is a legible strategy game in miniature, and every mechanic
  it depicts exists in the real game (``docs/game-integration.md``,
  ``tests/fixtures/grid_match_score.json``): role-distinct unit glyphs for
  BOTH teams (scout / harvester / defender — three distinct geometric
  shapes), resource nodes, capturable control posts with team-ownership
  rings, a score readout carrying the real formula (missions + control +
  resources), and a one-line message ticker of agent commentary.
* All motion lives inside one ``@media (prefers-reduced-motion:
  no-preference)`` block; with JS off or reduced motion the *unmodified*
  markup composes the mid-game poster frame — nothing is pre-hidden.
* A stable DOM interface for t7's first-party sim (ids, classes, data
  attributes, and the documented grid geometry) — pinned here so the sim
  can be built against it without re-reading the markup.
* Exactly one ``<h1>`` on the landing page — the hero's mixed-case
  headline ("An arena for humans and agents", accent on "arena"); the
  landing markdown's own heading is stripped from the rendered body only.
* CTAs name the action and point where the direction says: **Play a
  match** → ``/docs``, **See the leaderboard** → ``/leaderboard``.
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

_MOTION_GUARD = "@media (prefers-reduced-motion: no-preference)"

#: The real game's vocabulary (docs/game-integration.md + the score
#: fixture): two teams, three roles, unit ids engine-generated as
#: ``<team_id>-u<N>``, score = missions + control + resources.
_TEAMS = ("blue", "red")
_ROLES = ("scout", "harvester", "defender")
_SCORE_TERMS = ("missions", "control", "resources")

#: The documented board geometry (hero.py docstring, the t7 interface
#: contract): 40px cells on an 8x5 field whose top-left corner sits at
#: user-unit (40, 60), so cell [col, row] centers at (60+40*col, 80+40*row).
_CELL = 40
_ORIGIN_X, _ORIGIN_Y = 40, 60
_COLS, _ROWS = 8, 5


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


def _unguarded_css() -> str:
    """The fragment's scoped CSS up to the reduced-motion guard — the rules
    that ARE the poster frame when motion (or CSS animation) is off."""
    return hero.HERO_HTML.split(_MOTION_GUARD, 1)[0]


def _guarded_css() -> str:
    return hero.HERO_HTML.split(_MOTION_GUARD, 1)[1]


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
    """The scene's color roles ride the dawn tokens (hero.py documents the
    map): board base --surface, pedestals --surface-2, grid --border,
    neutral rings --border-strong, blue team --accent (+ --accent-glow
    halos), red team --text (+ --border halos), commentary --text-muted,
    resource motes --mesh-halo, score/ticker type --font-mono, and motion
    on the family easing tokens."""
    for token in (
        "var(--surface)",
        "var(--surface-2)",
        "var(--border)",
        "var(--border-strong)",
        "var(--text)",
        "var(--text-muted)",
        "var(--accent)",
        "var(--accent-glow)",
        "var(--mesh-halo)",
        "var(--font-mono)",
        "var(--ease-out)",
        "var(--ease-gentle)",
    ):
        assert token in hero.HERO_HTML, token


# ---------------------------------------------------------------------------
# The scene: role glyphs for both teams, three distinct geometric shapes
# ---------------------------------------------------------------------------

_UNIT_RE = re.compile(
    r'<g class="hp-unit hp-(?P<teamclass>blue|red)"'
    r' data-unit="(?P<unit>[\w-]+)"'
    r' data-team="(?P<team>blue|red)"'
    r' data-role="(?P<role>\w+)"'
    r' transform="translate\((?P<x>\d+),(?P<y>\d+)\)">'
    r"(?P<glyph>.*?)</g>",
    re.S,
)


def _units() -> list[dict[str, str]]:
    return [m.groupdict() for m in _UNIT_RE.finditer(hero.HERO_HTML)]


def test_both_teams_field_all_three_roles() -> None:
    units = _units()
    assert len(units) == 6, "expected 3 units per team"
    for team in _TEAMS:
        roles = {u["role"] for u in units if u["team"] == team}
        assert roles == set(_ROLES), (team, roles)


def test_unit_ids_follow_the_engine_scheme_and_teams_match_classes() -> None:
    """Unit ids are engine-generated ``<team_id>-u<N>`` (docs/game-
    integration.md) — the scene uses the real scheme, and the styling class
    never disagrees with the data attribute."""
    for unit in _units():
        assert re.fullmatch(rf"{unit['team']}-u\d+", unit["unit"]), unit["unit"]
        assert unit["teamclass"] == unit["team"], unit["unit"]


def test_roles_are_three_distinct_geometric_shapes_never_anthropomorphized() -> None:
    """scout = polygon (triangle), harvester = circle, defender = rect —
    one shape per role, identical across teams (teams differ by fill/stroke
    treatment, not silhouette)."""
    shape_by_role = {"scout": "<polygon", "harvester": "<circle", "defender": "<rect"}
    for unit in _units():
        expected = shape_by_role[unit["role"]]
        assert expected in unit["glyph"], (unit["unit"], unit["glyph"])
        others = set(shape_by_role.values()) - {expected}
        for other in others:
            assert other not in unit["glyph"], (unit["unit"], other)


def test_teams_are_told_apart_by_fill_and_stroke_treatment() -> None:
    """Blue reads as the solid accent team; red reads as the ink-outline
    team — a fill-vs-stroke distinction, not a hue-only one."""
    css = _unguarded_css()
    blue_rule = re.search(r"\.hp-blue\s*\{([^}]*)\}", css)
    red_rule = re.search(r"\.hp-red\s*\{([^}]*)\}", css)
    assert blue_rule is not None and red_rule is not None
    assert "fill: var(--accent)" in blue_rule.group(1)
    assert "stroke: var(--text)" in red_rule.group(1)


# ---------------------------------------------------------------------------
# The scene: resource nodes, control posts with ownership rings, a gather
# ---------------------------------------------------------------------------

_POST_RE = re.compile(
    r'<g class="hp-post" data-post="(?P<post>[\w-]+)" data-owner="(?P<owner>\w+)"'
    r' transform="translate\((?P<x>\d+),(?P<y>\d+)\)">(?P<body>.*?)</g>',
    re.S,
)


def _posts() -> list[dict[str, str]]:
    return [m.groupdict() for m in _POST_RE.finditer(hero.HERO_HTML)]


def test_at_least_two_control_posts_with_team_ownership_rings() -> None:
    posts = _posts()
    assert len(posts) >= 2
    owners = {p["owner"] for p in posts}
    assert owners <= {"blue", "red", "none"}, owners
    # The poster frame shows held posts — both ownership treatments live.
    assert "blue" in owners
    assert "red" in owners
    for post in posts:
        assert "hero-post-ring" in post["body"], post["post"]


def test_ownership_ring_color_derives_from_the_data_owner_attribute() -> None:
    """t7 flips ``data-owner`` and CSS does the re-skin: every ownership
    state has an attribute-selector rule, so capture needs no class
    surgery."""
    css = hero.HERO_HTML
    for owner in ("blue", "red", "none"):
        assert f'.hp-post[data-owner="{owner}"]' in css, owner


def test_resource_nodes_are_on_the_field() -> None:
    nodes = re.findall(r'<g class="hp-res" data-res="(\w+)"', hero.HERO_HTML)
    assert len(nodes) >= 2
    assert len(set(nodes)) == len(nodes), "resource ids must be unique"


def test_a_gather_is_in_progress() -> None:
    """The poster frame catches the economy mid-verb: at least one gather
    beam ties a harvester to a resource node (gather/deliver are real game
    actions — docs/game-integration.md)."""
    beams = re.findall(r'class="hero-gather" data-team="(blue|red)"', hero.HERO_HTML)
    assert len(beams) >= 1


# ---------------------------------------------------------------------------
# The scene: score readout (the real formula) and the message ticker
# ---------------------------------------------------------------------------


def _score_terms(team: str) -> dict[str, int]:
    text = re.search(rf'<text id="hero-score-{team}"[^>]*>(.*?)</text>', hero.HERO_HTML, re.S)
    assert text is not None, team
    terms = dict(re.findall(r'<tspan data-term="(\w+)">(\d+)</tspan>', text.group(1)))
    return {name: int(value) for name, value in terms.items()}


def test_score_readout_carries_the_real_three_term_formula() -> None:
    """Score = missions + control + resources (the platform's hard rating
    score — docs/game-integration.md; outcome shape in
    tests/fixtures/grid_match_score.json). The authored numbers must
    actually sum: the scene never fakes its own scoring."""
    for team in _TEAMS:
        terms = _score_terms(team)
        assert set(terms) == set(_SCORE_TERMS) | {"total"}, (team, terms)
        assert sum(terms[name] for name in _SCORE_TERMS) == terms["total"], team
    # The formula itself is spelled out on the board for the naive viewer.
    assert "missions + control + resources" in hero.HERO_HTML


def test_ticker_carries_one_line_of_agent_commentary() -> None:
    """One terse line in the real voice: ``<unit> · <role> — “…”`` with an
    engine-scheme unit id and a real role."""
    ticker = re.search(
        r'<text id="hero-ticker" class="hero-ticker"[^>]*>(.*?)</text>',
        hero.HERO_HTML,
        re.S,
    )
    assert ticker is not None
    flattened = re.sub(r"<[^>]+>", "", ticker.group(1)).strip()
    assert re.fullmatch(
        r"(blue|red)-u\d+ · (scout|harvester|defender) — “[^”]+”", flattened
    ), flattened


# ---------------------------------------------------------------------------
# Motion: everything animated inside the one reduced-motion guard; the
# unmodified markup IS the mid-game poster frame
# ---------------------------------------------------------------------------


def test_all_hero_motion_lives_inside_the_reduced_motion_guard() -> None:
    assert hero.HERO_HTML.count(_MOTION_GUARD) == 1
    before = _unguarded_css()
    for motion_token in ("@keyframes", "animation", "transition"):
        assert motion_token not in before, motion_token


def test_poster_frame_hides_nothing_without_js_or_motion() -> None:
    """With every guarded rule inert, the authored markup must read as a
    complete mid-game still: no unguarded rule may pre-hide anything (no
    zero-opacity, no display:none, no visibility:hidden on scene parts),
    and no element ships a ``hidden`` attribute."""
    before = _unguarded_css()
    # No zero-opacity rule (0.35 etc. are fine — only a flat 0 hides).
    assert re.search(r"opacity:\s*0(?![.\d])", before) is None
    assert "visibility: hidden" not in before
    assert "display: none" not in before
    assert " hidden" not in re.sub(r"<style>.*?</style>", "", hero.HERO_HTML, flags=re.S)


def test_entrance_choreography_staggers_on_the_family_easing_tokens() -> None:
    """The hero orchestrates its own entrance (scripts.py skips it): a
    staggered rise for eyebrow → headline → CTAs → board, all inside the
    guard, eased by the family tokens."""
    guarded = _guarded_css()
    for selector in (".hero-eyebrow", ".hero-headline", ".hero-ctas", ".hero-board"):
        at = guarded.find(selector)
        assert at != -1, selector
        assert "animation" in guarded[at : at + 220], selector
    assert "var(--ease-out)" in guarded
    assert "var(--ease-gentle)" in guarded


def test_held_post_halos_breathe_only_inside_the_guard() -> None:
    """The one ambient motion on the still scene: owned-post halos breathe
    (opacity only — never transform, which t7 owns for movement)."""
    guarded = _guarded_css()
    breathe = re.search(r"@keyframes hero-breathe\s*\{([^@]*?)\}\s*\}", guarded)
    assert breathe is not None
    assert "opacity" in breathe.group(1)
    assert "transform" not in breathe.group(1)


# ---------------------------------------------------------------------------
# The t7 interface: stable ids, data attributes, and grid geometry
# ---------------------------------------------------------------------------


def test_the_documented_sim_interface_ids_exist() -> None:
    for element_id in ("hero-turn", "hero-ticker", "hero-score-blue", "hero-score-red"):
        assert hero.HERO_HTML.count(f'id="{element_id}"') == 1, element_id


def test_every_piece_sits_on_a_documented_cell_center() -> None:
    """Units, posts, and resources are positioned via
    ``transform="translate(x,y)"`` where (x, y) is a cell center of the
    documented grid — the coordinate system t7 computes moves in."""
    pieces = [(u["unit"], int(u["x"]), int(u["y"])) for u in _units()]
    pieces += [(p["post"], int(p["x"]), int(p["y"])) for p in _posts()]
    pieces += [
        (m.group(1), int(m.group(2)), int(m.group(3)))
        for m in re.finditer(
            r'data-res="(\w+)" transform="translate\((\d+),(\d+)\)"', hero.HERO_HTML
        )
    ]
    assert len(pieces) >= 11  # 6 units + >=2 posts + >=2 resources
    half = _CELL // 2
    for name, x, y in pieces:
        col, col_rem = divmod(x - _ORIGIN_X - half, _CELL)
        row, row_rem = divmod(y - _ORIGIN_Y - half, _CELL)
        assert col_rem == 0 and row_rem == 0, (name, x, y)
        assert 0 <= col < _COLS and 0 <= row < _ROWS, (name, col, row)


def test_no_two_pieces_share_a_cell() -> None:
    occupied = [
        (int(m.group(1)), int(m.group(2)))
        for m in re.finditer(r'transform="translate\((\d+),(\d+)\)"', hero.HERO_HTML)
    ]
    assert len(occupied) == len(set(occupied)), "two pieces authored onto one cell"


def test_the_module_docstring_documents_the_t7_contract() -> None:
    """t7 builds against hero.py's docstring, not against a re-read of the
    markup: the interface vocabulary and the grid geometry must be written
    down there."""
    doc = hero.__doc__ or ""
    for token in (
        "data-unit",
        "data-role",
        "data-team",
        "data-post",
        "data-owner",
        "data-res",
        "hero-score-blue",
        "hero-score-red",
        "hero-ticker",
        "hero-turn",
        "data-term",
        "40",
        "(40, 60)",
    ):
        assert token in doc, token


# ---------------------------------------------------------------------------
# Semantics and copy — the dawn voice
# ---------------------------------------------------------------------------


def test_exactly_one_h1_on_the_landing_page_and_it_is_the_hero_headline() -> None:
    for path in _LANDING_PATHS:
        text = _page(path)
        h1s = re.findall(r"<h1[\s>]", text)
        assert len(h1s) == 1, (path, h1s)
        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.S)
        assert h1 is not None, path
        assert "arena" in h1.group(1), path


def test_non_landing_pages_keep_their_own_single_h1() -> None:
    for path in _NON_LANDING_PAGES:
        text = _page(path)
        assert len(re.findall(r"<h1[\s>]", text)) == 1, path


def test_headline_is_mixed_case_with_the_accent_word_treatment() -> None:
    """The dawn re-voicing: mixed-case Fraunces headline (the theme's h1
    styles carry the family voice — the hero no longer forces mono
    uppercase), with "arena" carrying the accent treatment."""
    assert '<span class="hero-accent">arena</span>' in hero.HERO_HTML
    headline = re.search(r"<h1[^>]*>(.*?)</h1>", hero.HERO_HTML, re.S)
    assert headline is not None
    flattened = re.sub(r"<[^>]+>", "", headline.group(1)).strip()
    assert flattened == "An arena for humans and agents"
    css = _unguarded_css()
    headline_rule = re.search(r"\.hero \.hero-headline\s*\{([^}]*)\}", css)
    assert headline_rule is not None
    assert "text-transform" not in headline_rule.group(1)
    assert "font-family" not in headline_rule.group(1), "theme.py owns the headline face"


def test_eyebrow_carries_a_sim_updatable_turn_counter() -> None:
    eyebrow = re.search(r'<p class="hero-eyebrow">(.*?)</p>', hero.HERO_HTML, re.S)
    assert eyebrow is not None
    assert re.search(
        r'Turn <span class="hero-turn" id="hero-turn">\d+</span> — your move',
        eyebrow.group(1),
    ), eyebrow.group(1)


def test_ctas_name_the_action_and_point_at_play_and_leaderboard() -> None:
    play = re.search(r'<a class="button"[^>]*href="([^"]+)"[^>]*>Play a match</a>', hero.HERO_HTML)
    assert play is not None
    assert play.group(1) == "/play"
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


def test_hero_makes_zero_network_requests() -> None:
    """The scene is self-contained inline markup: no images, no external
    fetches of any kind ride the fragment."""
    for needle in ("http://", "https://", "url(", "<img", "<image", "xlink:href", "@import"):
        assert needle not in hero.HERO_HTML, needle


# ---------------------------------------------------------------------------
# Size: inline HTML, but page weight still matters for the Lighthouse gate
# ---------------------------------------------------------------------------


def test_hero_fragment_stays_within_its_12kb_allowance() -> None:
    """Renegotiated for the t6 strategy-game scene (was 8KB for the
    four-piece decorative loop): the board now carries six role-distinct
    units, three posts with ownership rings, resource nodes, gather beams,
    a three-term score readout, and a ticker line — authored geometry the
    old cap has no room for. The plan (t6 brief) allows up to 16KB; 12KB is
    deliberately tighter, and the scene should land well under it — every
    landing page ships these bytes inline."""
    assert len(hero.HERO_HTML.encode("utf-8")) <= 12 * 1024
