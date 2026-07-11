"""Tests for :mod:`league_site.web.theme` — the design tokens + stylesheet.

Updated for the sibling-of-agentculture.org pass (spec h1, task t5): the
palette swaps wholesale to agentculture.org's dawn palette — "first light
over the mesh" — in BOTH schemes, the type voice becomes Fraunces Variable
(display) + Albert Sans Variable (body) via first-party ``@font-face``, and
the decorative sky-wash/mesh tokens land. League's token NAMES survive
(``--bg``, ``--surface``, ``--accent``, …) so every consumer (hero, viewer,
profiles) re-skins automatically; only the VALUES change. The
``data-theme`` toggle mechanics (t2) are unchanged and stay pinned below.
"""

from __future__ import annotations

import re

from league_site.web import theme

_CORE_TOKENS = (
    "--bg",
    "--surface",
    "--surface-2",
    "--text",
    "--text-muted",
    "--border",
    "--border-soft",
    "--border-strong",
    "--accent",
    "--accent-strong",
    "--accent-ink",
)

#: The dawn palette, light scheme — agentculture.org's ten-minutes-after-
#: sunrise values mapped onto league's token names.
_LIGHT_VALUES = {
    "--bg": "#f4f5fb",
    "--surface": "#ffffff",
    "--surface-2": "#e9ebf5",
    "--text": "#232a4d",
    "--text-muted": "#4d546f",
    "--border": "rgba(35, 42, 77, .14)",
    "--border-soft": "rgba(35, 42, 77, .08)",
    "--border-strong": "#767e9d",
    "--accent": "#0b655c",
    "--accent-strong": "#0c5a53",
    "--accent-ink": "#ffffff",
}

#: The dawn palette, dark scheme — the hour before dawn.
_DARK_VALUES = {
    "--bg": "#0b0f20",
    "--surface": "#161b36",
    "--surface-2": "#1b2140",
    "--text": "#e9ecf8",
    "--text-muted": "#a9b0cf",
    "--border": "rgba(233, 236, 248, .14)",
    "--border-soft": "rgba(233, 236, 248, .07)",
    "--border-strong": "#5d6689",
    "--accent": "#7fdcc9",
    "--accent-strong": "#7fdcc9",
    "--accent-ink": "#0b0f20",
}

#: Decorative sky washes (never carry text alone), light / dark.
_SKY_LIGHT = {
    "--sky-upper": "rgba(198, 210, 248, .55)",
    "--sky-horizon": "rgba(255, 205, 166, .5)",
    "--sky-mist": "rgba(167, 216, 205, .35)",
    "--sky-glow": "rgba(255, 178, 125, .5)",
}
_SKY_DARK = {
    "--sky-upper": "rgba(64, 92, 181, .25)",
    "--sky-horizon": "rgba(255, 159, 102, .1)",
    "--sky-mist": "rgba(88, 214, 181, .14)",
    "--sky-glow": "rgba(96, 226, 189, .14)",
}


def _root_block() -> str:
    return theme.STYLESHEET.split(":root {", 1)[1].split("}", 1)[0]


def _dark_attr_block() -> str:
    block = theme.STYLESHEET.split(':root[data-theme="dark"]', 1)[1]
    return block.split("}", 1)[0]


def _media_dark_block() -> str:
    block = theme.STYLESHEET.split("@media (prefers-color-scheme: dark)", 1)[1]
    block = block.split(':root:not([data-theme="light"])', 1)[1]
    return block.split("}", 1)[0]


def test_stylesheet_defines_a_light_root_scope() -> None:
    assert ":root {" in theme.STYLESHEET


def test_stylesheet_defines_a_dark_scheme_via_prefers_color_scheme() -> None:
    assert "@media (prefers-color-scheme: dark)" in theme.STYLESHEET


def test_stylesheet_declares_every_documented_palette_variable() -> None:
    for token in _CORE_TOKENS:
        assert f"{token}:" in theme.STYLESHEET, token


def test_dark_scheme_overrides_the_core_palette_tokens() -> None:
    _, dark_block = theme.STYLESHEET.split("@media (prefers-color-scheme: dark)", 1)
    for token in ("--bg", "--surface", "--text", "--accent"):
        assert f"{token}:" in dark_block, token


# --- the dawn palette (t5): agentculture.org's values on league's names ---


def test_light_root_carries_the_dawn_palette_values() -> None:
    root = _root_block()
    for token, value in _LIGHT_VALUES.items():
        assert f"{token}: {value}" in root, (token, value)


def test_explicit_dark_theme_block_uses_the_documented_dark_values() -> None:
    block = _dark_attr_block()
    for token, value in _DARK_VALUES.items():
        assert f"{token}: {value}" in block, (token, value)


def test_sky_wash_tokens_are_defined_in_both_schemes() -> None:
    root = _root_block()
    for token, value in _SKY_LIGHT.items():
        assert f"{token}: {value}" in root, (token, value)
    for block in (_dark_attr_block(), _media_dark_block()):
        for token, value in _SKY_DARK.items():
            assert f"{token}: {value}" in block, (token, value)


def test_mesh_tokens_are_defined_and_the_node_rides_the_accent() -> None:
    root = _root_block()
    # --mesh-node references the accent token, so it re-skins with the
    # scheme automatically and never needs a dark override.
    assert "--mesh-node: var(--accent)" in root
    assert "--mesh-thread: rgba(35, 42, 77, .32)" in root
    assert "--mesh-halo: rgba(255, 170, 110, .55)" in root
    assert "--mesh-halo-alt: rgba(122, 158, 245, .5)" in root
    dark = _dark_attr_block()
    assert "--mesh-thread: rgba(233, 236, 248, .28)" in dark
    assert "--mesh-halo: rgba(96, 226, 189, .4)" in dark
    assert "--mesh-halo-alt: rgba(255, 170, 120, .28)" in dark


def test_shadow_tokens_are_defined_in_both_schemes() -> None:
    root = _root_block()
    assert "--shadow: 0 4px 24px rgba(35, 42, 77, .07)" in root
    assert "--shadow-lift: 0 14px 40px rgba(35, 42, 77, .12)" in root
    dark = _dark_attr_block()
    assert "--shadow: 0 4px 24px rgba(2, 4, 12, .5)" in dark
    assert "--shadow-lift: 0 14px 40px rgba(2, 4, 12, .6)" in dark


def test_rhythm_tokens_radius_and_section_pad() -> None:
    root = _root_block()
    assert "--radius: 1.25rem" in root
    assert "--section-pad: clamp(4.5rem, 10vh, 8rem)" in root
    # A smaller derived radius keeps inline code / pre from going pill.
    assert "--radius-sm:" in root
    # ...and the rhythm is actually consumed: the selector must be the
    # compound main.wrap — a bare `main` rule loses the cascade to .wrap's
    # `padding` shorthand (higher specificity) and would silently never
    # apply on the real shells, which all render <main id="main"
    # class="wrap">.
    main_block = theme.STYLESHEET.split("main.wrap {", 1)[1].split("}", 1)[0]
    assert "padding-block: var(--section-pad)" in main_block


def test_body_before_paints_the_dawn_sky_wash() -> None:
    """The single strongest family signal: every page opens under the same
    dawn — two soft radial gradients from the sky tokens, fixed behind
    content, decorative only."""
    css = theme.STYLESHEET
    idx = css.index("body::before")
    block = css[idx:].split("}", 1)[0]
    assert "radial-gradient" in block
    assert "var(--sky-upper)" in block
    assert "var(--sky-mist)" in block
    assert "pointer-events: none" in block
    assert "z-index: -1" in block
    # For the negative z-index to sit behind content but above the page
    # background, the background must live on html, not body.
    html_block = css.split("html {", 1)[1].split("}", 1)[0]
    assert "background: var(--bg)" in html_block


# --- the family type voice (t5): Fraunces display, Albert Sans body ---


def test_stylesheet_declares_both_vendored_variable_fonts() -> None:
    css = theme.STYLESHEET
    faces = re.findall(r"@font-face\s*\{([^}]*)\}", css)
    assert len(faces) == 2, "expected exactly two @font-face rules"
    by_family = {}
    for body in faces:
        match = re.search(r'font-family:\s*"([^"]+)"', body)
        assert match is not None
        by_family[match.group(1)] = body

    fraunces = by_family["Fraunces Variable"]
    assert "url(/fonts/fraunces-var.woff2)" in fraunces
    albert = by_family["Albert Sans Variable"]
    assert "url(/fonts/albert-sans-var.woff2)" in albert

    for body in faces:
        assert "font-display: swap" in body
        assert "font-style: normal" in body
        assert re.search(r"font-weight:\s*100 900", body), "variable weight range"
        # First-party only — never a third-party font URL.
        assert "url(http" not in body


def test_font_tokens_mirror_the_agentculture_type_voice() -> None:
    root = _root_block()
    assert '--font-display: "Fraunces Variable", "Iowan Old Style", Georgia, serif' in root
    assert '--font-body: "Albert Sans Variable", -apple-system' in root
    # The body stack keeps robust system fallbacks; mono stays for code.
    assert "-apple-system" in theme.STYLESHEET
    assert "--font-mono: ui-monospace" in root


def test_headings_speak_fraunces_with_the_family_variation_settings() -> None:
    css = theme.STYLESHEET
    idx = css.index("h1, h2, h3")
    block = css[idx:].split("}", 1)[0]
    assert "font-family: var(--font-display)" in block
    assert 'font-variation-settings: "SOFT" 75, "WONK" 0' in block
    assert "line-height: 1.14" in block
    assert "letter-spacing: -0.012em" in block
    assert "text-wrap: balance" in block
    # h1 is the lightest weight; fluid clamp() sizes.
    h1_block = re.search(r"\nh1 \{([^}]*)\}", css)
    assert h1_block is not None
    assert "clamp(" in h1_block.group(1)
    assert "font-weight: 400" in h1_block.group(1)


def test_body_reads_albert_sans_at_the_family_measure() -> None:
    css = theme.STYLESHEET
    body_block = css.split("\nbody {", 1)[1].split("}", 1)[0]
    assert "font-family: var(--font-body)" in body_block
    assert "font-size: 1.0625rem" in body_block
    assert "line-height: 1.7" in body_block
    assert "text-wrap: pretty" in css


def test_wordmark_restyles_to_the_display_serif() -> None:
    css = theme.STYLESHEET
    idx = css.index(".wordmark {")
    block = css[idx:].split("}", 1)[0]
    assert "font-family: var(--font-display)" in block
    assert "font-weight: 520" in block
    # The mono/uppercase scoreboard styling is gone from the wordmark.
    assert "text-transform" not in block
    assert "0.08em" not in block


def test_stylesheet_makes_no_external_requests() -> None:
    assert "url(http" not in theme.STYLESHEET
    assert "@import" not in theme.STYLESHEET
    assert "<script" not in theme.STYLESHEET


def test_stylesheet_payload_is_within_the_documented_budget() -> None:
    payload = theme.STYLESHEET.encode("utf-8")
    assert len(payload) <= theme.CSS_BUDGET_BYTES


def test_css_budget_constant_matches_the_documented_budget() -> None:
    # Renegotiated twice: 10KB -> 24KB ahead of the dazzle pass (spec c11),
    # then 24KB -> 32KB ahead of the sibling-of-agentculture.org pass
    # (spec h1, task t2); see tests/test_web_theme_budget.py for the full
    # budget contract (CSS + first-party JS + fonts + total asset weight)
    # this constant is part of.
    assert theme.CSS_BUDGET_BYTES == 32 * 1024


# --- data-theme manual toggle (t2): explicit choice beats the OS default ---
#
# t3 adds a header control that sets data-theme="dark" or data-theme="light"
# on <html> (no attribute at all means "follow the OS", i.e. the pre-existing
# prefers-color-scheme behavior is unchanged). These tests pin the CSS
# contract that toggle depends on.


def test_stylesheet_defines_an_explicit_dark_theme_attribute_block() -> None:
    assert ':root[data-theme="dark"]' in theme.STYLESHEET


def test_explicit_dark_theme_block_carries_every_dark_token_unconditionally() -> None:
    block = _dark_attr_block()
    for token in _CORE_TOKENS:
        assert f"{token}:" in block, token


def test_explicit_light_theme_attribute_pins_light_color_scheme() -> None:
    assert ':root[data-theme="light"]' in theme.STYLESHEET
    _, block = theme.STYLESHEET.split(':root[data-theme="light"]', 1)
    block = block.split("}", 1)[0]
    assert "color-scheme: light" in block


def test_prefers_color_scheme_dark_block_is_scoped_to_not_override_an_explicit_light_choice() -> (
    None
):
    """The OS-driven dark override must not win once a visitor has explicitly
    picked light — it should only apply when there is no explicit light
    choice, i.e. :root:not([data-theme="light"])."""
    _, media_block = theme.STYLESHEET.split("@media (prefers-color-scheme: dark)", 1)
    assert ':root:not([data-theme="light"])' in media_block


def test_prefers_color_scheme_dark_block_still_carries_every_dark_token() -> None:
    scoped_block = _media_dark_block()
    for token in _CORE_TOKENS:
        assert f"{token}:" in scoped_block, token


def test_color_scheme_is_kept_in_sync_across_every_theming_path() -> None:
    # Explicit dark choice -> dark form controls / scrollbars.
    assert "color-scheme: dark" in _dark_attr_block()

    # OS says dark, no explicit override -> also dark form controls / scrollbars.
    assert "color-scheme: dark" in _media_dark_block()

    # Explicit light choice -> light form controls / scrollbars, regardless of OS.
    _, light_attr_block = theme.STYLESHEET.split(':root[data-theme="light"]', 1)
    light_attr_block = light_attr_block.split("}", 1)[0]
    assert "color-scheme: light" in light_attr_block


def test_first_visit_default_root_still_declares_both_color_schemes() -> None:
    """No data-theme attribute at all (first visit) must still let the OS
    decide via the plain `color-scheme: light dark` on the bare :root."""
    assert "color-scheme: light dark" in _root_block()
