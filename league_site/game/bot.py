"""Drive a mode's house/bot teams with the game's *own* bot policies.

Closes platform#9 ("solo-vs-bot house team never acts"): before this module,
the adapter never staged house orders — it force-resolved every turn with
``league match tick --apply``, which treats an unstaged team as all-holds,
so the solo mode's opponent was a stationary puppet.

The fix stays strictly on the subprocess side of the platform's
never-``import league`` boundary (``tests/test_game_import_boundary.py``):
the ``league`` CLI's own ``harness run`` verb is **resumable** — given a
config whose ``match.id`` already exists on disk, it skips team
registration/match creation and simply plays the configured teams from the
current turn. So the adapter, after staging the participant side's orders,
writes the config built here into the match workdir and runs::

    league harness run --config <file> --apply --json

with only the mode's policy-carrying bot teams configured and
``max_rounds: 1``. The harness computes each bot team's orders with the
game's own policy and stages them through ``league match act`` — and since
every other team has already staged, that final act auto-resolves the turn
exactly per the game's "resolve once all teams have staged" rule. No
platform-side reimplementation of the game's strategy, and mode fairness
stays intact: the solo action cap applies to the player side only, mirroring
the game's own solo preset (``solo`` declares ``max_actions: 1``, ``house``
is uncapped).

Policy labels (:class:`~league_site.game.modes.TeamSpec.bot_policy`) use the
game's own vocabulary — the ``model`` labels its bundled presets give bot
seats:

* ``"bot:greedy"`` — the in-package deterministic greedy baseline
  (``league.harness.make_bot_driver``; harness driver ``{"type": "bot"}``).
  This is the launch default: it ships inside the installed
  ``league-of-agents`` wheel, needs no extra files, and is fully
  deterministic given the match state.
* ``"bot-file:<name>"`` — a committed coded strategy (``bots/<name>.py``;
  harness driver ``{"type": "bot-file", "strategy": <name>}``). Supported by
  the mapping here, but **not** usable against a plain wheel install today:
  the published package does not ship the game repo's ``bots/`` directory,
  so the harness would fail to load the strategy file. Prefer
  ``"bot:greedy"`` until the game ships its strategies in the wheel.
"""

from __future__ import annotations

from typing import Any

from league_site.game.modes import LaunchMode, TeamSpec

#: The game's deterministic greedy bot baseline — the one bot policy the
#: installed wheel always carries (see the module docstring).
BOT_GREEDY = "bot:greedy"

#: Prefix for committed coded-strategy policies (``bots/<name>.py``).
BOT_FILE_PREFIX = "bot-file:"

#: Filename the adapter writes the harness config under, at the match
#: workdir's *root* — deliberately outside ``.league/``, so
#: :func:`league_site.game.workdir.persist` never folds it into a snapshot.
HARNESS_CONFIG_FILENAME = "harness-bot-teams.json"


def driver_spec(bot_policy: str) -> dict[str, Any]:
    """The ``league harness`` driver spec for a ``bot_policy`` label.

    Raises ``ValueError`` for anything that is not ``"bot:greedy"`` or
    ``"bot-file:<name>"`` — a mode declaring a policy the game cannot run
    must fail loudly at config-build time, never as a silent all-holds turn.
    """
    if bot_policy == BOT_GREEDY:
        return {"type": "bot"}
    if bot_policy.startswith(BOT_FILE_PREFIX):
        strategy = bot_policy[len(BOT_FILE_PREFIX) :]
        if strategy:
            return {"type": "bot-file", "strategy": strategy}
    raise ValueError(
        f"unknown bot policy {bot_policy!r}; expected {BOT_GREEDY!r} or "
        f"'{BOT_FILE_PREFIX}<name>'"
    )


def driven_bot_teams(mode: LaunchMode) -> tuple[TeamSpec, ...]:
    """The bot teams of ``mode`` that carry a :attr:`TeamSpec.bot_policy`.

    A bot team without a policy is a deliberately passive house — the
    adapter leaves it unstaged and force-resolves with ``match tick``
    instead (see :class:`~league_site.game.modes.TeamSpec`).
    """
    return tuple(t for t in mode.teams if t.is_bot and t.bot_policy)


def harness_config(mode: LaunchMode, *, match_id: str, scenario_id: str) -> dict[str, Any]:
    """The ``league harness run`` resume config driving ``mode``'s bot side.

    ``match_id`` must name an already-created match (the harness then skips
    registration/creation and plays from the current turn) and
    ``scenario_id`` the scenario it was created with — taken from the match
    state, not from ``mode``, so a per-engine scenario override is honored.
    ``max_rounds: 1`` bounds the harness to the current turn: it acts each
    configured bot team once, and the resulting auto-resolution (every team
    staged) is exactly the turn the platform is processing. Only bot teams
    are configured; the harness never touches a participant-controlled team.
    """
    teams = [
        {"id": team.team_id, "driver": driver_spec(team.bot_policy or "")}
        for team in driven_bot_teams(mode)
    ]
    if not teams:
        raise ValueError(f"launch mode {mode.name!r} has no bot team with a bot_policy to drive")
    return {
        "match": {"scenario": scenario_id, "id": match_id},
        "teams": teams,
        "max_rounds": 1,
    }


__all__ = [
    "BOT_GREEDY",
    "BOT_FILE_PREFIX",
    "HARNESS_CONFIG_FILENAME",
    "driver_spec",
    "driven_bot_teams",
    "harness_config",
]
