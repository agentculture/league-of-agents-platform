"""Tests for the evals pages' stylesheet and the game tokens it consumes."""

from __future__ import annotations

import re

from league_site.evals.styles import EVALS_CSS
from league_site.web import theme

_TEAM = {
    "light": {"--team-blue": "#2b54c4", "--team-red": "#b4314f"},
    "dark": {"--team-blue": "#6f8be6", "--team-red": "#e0627f"},
}
_GAME_TOKENS = (
    "--team-blue",
    "--team-blue-wash",
    "--team-red",
    "--team-red-wash",
    "--gold",
    "--crown",
    "--tile",
    "--fog",
    "--bad",
)


def _scheme_blocks() -> dict[str, list[str]]:
    s = theme.STYLESHEET
    root = s[s.index(":root {") : s.index(':root[data-theme="dark"]')]
    attr = s[s.index(':root[data-theme="dark"] {') :]
    attr = attr[: attr.index("}")]
    media = s[s.index("@media (prefers-color-scheme: dark)") :]
    media = media[: media.index("* {")]
    return {"light": [root], "dark": [attr, media]}


def test_evals_css_has_no_raw_colour_literals() -> None:
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", EVALS_CSS)
    assert "rgb(" not in EVALS_CSS and "rgba(" not in EVALS_CSS


def test_evals_css_uses_only_defined_tokens() -> None:
    defined = set(re.findall(r"(--[\w-]+):", theme.STYLESHEET))
    local = set(re.findall(r"(--[\w-]+):", EVALS_CSS))
    used = set(re.findall(r"var\((--[\w-]+)", EVALS_CSS))
    assert used <= defined | local, used - defined - local


def test_theme_team_colours_exact_in_every_scheme_block() -> None:
    for scheme, blocks in _scheme_blocks().items():
        for block in blocks:
            for token, value in _TEAM[scheme].items():
                assert f"{token}: {value};" in block, (scheme, token)


def test_game_tokens_defined_in_every_scheme_block() -> None:
    for blocks in _scheme_blocks().values():
        for block in blocks:
            for token in _GAME_TOKENS:
                assert f"{token}:" in block, token
