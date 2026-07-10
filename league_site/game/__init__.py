"""League of Agents grid-lane game adapter — a subprocess ``GameEngine``.

Plugs the external ``league`` CLI (a separate, non-importable package —
``docs/game-integration.md``) in behind
:class:`~league_site.matches.engine.GameEngine` as
:class:`~league_site.game.adapter.GridLaneEngine`. Every call this package
makes to the game happens through :class:`~league_site.game.runner.LeagueRunner`
(a subprocess wrapper) against a per-match workdir hydrated/persisted via
:mod:`league_site.game.workdir` — nothing here ever imports ``league`` or
``from league`` (``tests/test_game_import_boundary.py`` enforces this over
the whole ``league_site`` tree).

Three bundled launch modes ship as data in :mod:`league_site.game.modes`:
:data:`~league_site.game.modes.SOLO_VS_BOT`,
:data:`~league_site.game.modes.TEAM_VS_TEAM`,
:data:`~league_site.game.modes.COOP_2`.
"""

from __future__ import annotations

from league_site.game.adapter import GAME_ID, GridLaneEngine
from league_site.game.modes import (
    COOP_2,
    DEFAULT_SCENARIO_ID,
    SOLO_VS_BOT,
    TEAM_VS_TEAM,
    LaunchMode,
    Rejection,
    TeamSpec,
    assign_participants,
    enforce_action_cap,
    get_mode,
    mode_names,
)
from league_site.game.modes import registry as mode_registry
from league_site.game.runner import (
    DEFAULT_BASE_COMMAND,
    LEAGUE_CLI_ENV_VAR,
    LeagueCliError,
    LeagueRunner,
    LeagueRunnerError,
    game_version,
)
from league_site.game.workdir import Snapshot, hydrate, persist

__all__ = [
    "GAME_ID",
    "GridLaneEngine",
    "COOP_2",
    "DEFAULT_SCENARIO_ID",
    "SOLO_VS_BOT",
    "TEAM_VS_TEAM",
    "LaunchMode",
    "Rejection",
    "TeamSpec",
    "assign_participants",
    "enforce_action_cap",
    "get_mode",
    "mode_names",
    "mode_registry",
    "DEFAULT_BASE_COMMAND",
    "LEAGUE_CLI_ENV_VAR",
    "LeagueCliError",
    "LeagueRunner",
    "LeagueRunnerError",
    "game_version",
    "Snapshot",
    "hydrate",
    "persist",
]
