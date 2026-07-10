"""Tests for :mod:`league_site.web.theme` — the design tokens + stylesheet."""

from __future__ import annotations

from league_site.web import theme

_CORE_TOKENS = (
    "--bg",
    "--surface",
    "--surface-2",
    "--text",
    "--text-muted",
    "--border",
    "--border-strong",
    "--accent",
    "--accent-ink",
)


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


def test_stylesheet_uses_system_font_stacks_only() -> None:
    assert "-apple-system" in theme.STYLESHEET
    assert "ui-monospace" in theme.STYLESHEET
    assert "@font-face" not in theme.STYLESHEET


def test_stylesheet_makes_no_external_requests() -> None:
    assert "url(http" not in theme.STYLESHEET
    assert "@import" not in theme.STYLESHEET
    assert "<script" not in theme.STYLESHEET


def test_stylesheet_payload_is_within_the_documented_budget() -> None:
    payload = theme.STYLESHEET.encode("utf-8")
    assert len(payload) <= theme.CSS_BUDGET_BYTES


def test_css_budget_constant_matches_the_documented_budget() -> None:
    # Renegotiated from 10KB to 24KB ahead of the dazzle pass (spec c11);
    # see tests/test_web_theme_budget.py for the full budget contract
    # (CSS + first-party JS + total asset weight) this constant is part of.
    assert theme.CSS_BUDGET_BYTES == 24 * 1024


# --- data-theme manual toggle (t2): explicit choice beats the OS default ---
#
# t3 adds a header control that sets data-theme="dark" or data-theme="light"
# on <html> (no attribute at all means "follow the OS", i.e. the pre-existing
# prefers-color-scheme behavior is unchanged). These tests pin the CSS
# contract that toggle depends on.


def test_stylesheet_defines_an_explicit_dark_theme_attribute_block() -> None:
    assert ':root[data-theme="dark"]' in theme.STYLESHEET


def test_explicit_dark_theme_block_carries_every_dark_token_unconditionally() -> None:
    _, block = theme.STYLESHEET.split(':root[data-theme="dark"]', 1)
    # Only look at the block's own braces, not everything after it.
    block = block.split("}", 1)[0]
    for token in _CORE_TOKENS:
        assert f"{token}:" in block, token


def test_explicit_dark_theme_block_uses_the_documented_dark_values() -> None:
    _, block = theme.STYLESHEET.split(':root[data-theme="dark"]', 1)
    block = block.split("}", 1)[0]
    dark_values = {
        "--bg": "#12151a",
        "--surface": "#1b1f27",
        "--surface-2": "#232833",
        "--text": "#e6e9ef",
        "--text-muted": "#a3adc2",
        "--border": "#2b313d",
        "--border-strong": "#5b6478",
        "--accent": "#ff8a3d",
        "--accent-ink": "#14171c",
    }
    for token, value in dark_values.items():
        assert f"{token}: {value}" in block, token


def test_explicit_light_theme_attribute_pins_light_color_scheme() -> None:
    assert ':root[data-theme="light"]' in theme.STYLESHEET
    _, block = theme.STYLESHEET.split(':root[data-theme="light"]', 1)
    block = block.split("}", 1)[0]
    assert "color-scheme: light" in block


def test_prefers_color_scheme_dark_block_is_scoped_to_not_override_an_explicit_light_choice() -> None:
    """The OS-driven dark override must not win once a visitor has explicitly
    picked light — it should only apply when there is no explicit light
    choice, i.e. :root:not([data-theme="light"])."""
    _, media_block = theme.STYLESHEET.split("@media (prefers-color-scheme: dark)", 1)
    assert ':root:not([data-theme="light"])' in media_block


def test_prefers_color_scheme_dark_block_still_carries_every_dark_token() -> None:
    _, media_block = theme.STYLESHEET.split("@media (prefers-color-scheme: dark)", 1)
    scoped_block = media_block.split(':root:not([data-theme="light"])', 1)[1]
    scoped_block = scoped_block.split("}", 1)[0]
    for token in _CORE_TOKENS:
        assert f"{token}:" in scoped_block, token


def test_color_scheme_is_kept_in_sync_across_every_theming_path() -> None:
    # Explicit dark choice -> dark form controls / scrollbars.
    _, dark_attr_block = theme.STYLESHEET.split(':root[data-theme="dark"]', 1)
    dark_attr_block = dark_attr_block.split("}", 1)[0]
    assert "color-scheme: dark" in dark_attr_block

    # OS says dark, no explicit override -> also dark form controls / scrollbars.
    _, media_block = theme.STYLESHEET.split("@media (prefers-color-scheme: dark)", 1)
    scoped_block = media_block.split(':root:not([data-theme="light"])', 1)[1]
    scoped_block = scoped_block.split("}", 1)[0]
    assert "color-scheme: dark" in scoped_block

    # Explicit light choice -> light form controls / scrollbars, regardless of OS.
    _, light_attr_block = theme.STYLESHEET.split(':root[data-theme="light"]', 1)
    light_attr_block = light_attr_block.split("}", 1)[0]
    assert "color-scheme: light" in light_attr_block


def test_first_visit_default_root_still_declares_both_color_schemes() -> None:
    """No data-theme attribute at all (first visit) must still let the OS
    decide via the plain `color-scheme: light dark` on the bare :root."""
    root_block = theme.STYLESHEET.split(":root {", 1)[1].split("}", 1)[0]
    assert "color-scheme: light dark" in root_block
