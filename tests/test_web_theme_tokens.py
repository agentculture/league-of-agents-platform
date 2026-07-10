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


def test_css_budget_constant_matches_the_documented_ten_kilobyte_budget() -> None:
    assert theme.CSS_BUDGET_BYTES == 10 * 1024
