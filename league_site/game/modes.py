"""The launch match modes, as data — never as per-mode code branches.

Three modes ship at launch (spec ``2026-07-10-the-league-of-agents-grid-lane-
game-is-live-as-the``, requirement "Launch match modes map to game
presets"): ``solo-vs-bot`` (one platform participant against the house),
``team-vs-team`` (two participants, competitive), and ``coop-2`` (two
participants sharing one team, cooperative). Each is a :class:`LaunchMode` —
a scenario, a game mode, a seed, and a tuple of :class:`TeamSpec` — resolved
generically by :mod:`league_site.game.adapter`, the same "mode is data, not
a code fork" discipline the game's own ``league/presets.py`` uses.

**Mode fairness is platform-enforced, never trusted from the game.** The
game's ``--driver <team>:<kind>`` flag is an audit label only — see
``docs/game-integration.md``'s "driver kinds are audit labels, not gates."
Nothing in the league CLI stops a team from submitting more actions than a
mode allows; :func:`enforce_action_cap` is what actually refuses the excess,
*before* any order ever reaches ``league match act``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from league_site.matches.models import Participant

#: The only scenario the three launch modes ship against (spec scope: "grid
#: lane only at launch"). Larger tables/other scenarios are future work.
DEFAULT_SCENARIO_ID = "skirmish-1"


@dataclass(frozen=True)
class TeamSpec:
    """One league team a :class:`LaunchMode` creates.

    ``driver_kind`` is recorded on ``league match new --driver`` purely as
    audit metadata (see the module docstring) — it never changes engine
    behavior. ``is_bot`` marks a team no platform participant ever controls
    (the house side): its orders are never staged by
    :meth:`~league_site.game.adapter.GridLaneEngine.apply_turn`, so the
    adapter must force resolution with ``league match tick --apply`` once
    every non-bot team has staged (see :attr:`LaunchMode.bot_team_ids`).
    ``action_cap`` is the platform-enforced per-turn order limit for this
    team (``None`` = unlimited); solo mode's fairness handicap is
    ``action_cap=1`` on the lone human/agent-controlled team.
    """

    team_id: str
    driver_kind: str
    is_bot: bool = False
    action_cap: int | None = None


@dataclass(frozen=True)
class Rejection:
    """A structured refusal — either the game's own (from
    ``last_turn_rejections``) or the platform's own (an order cut by
    :func:`enforce_action_cap` before ``match act`` was ever called).
    Mirrors the shape ``league match show --json`` already uses for its own
    ``last_turn_rejections`` entries, so a caller/UI can treat both
    uniformly."""

    team_id: str
    unit_id: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"team_id": self.team_id, "unit_id": self.unit_id, "reason": self.reason}


@dataclass(frozen=True)
class LaunchMode:
    """A named, data-only game-mode configuration.

    ``expected_participants`` is the exact platform-participant count this
    mode needs (checked by
    :meth:`~league_site.game.adapter.GridLaneEngine.initial_state`);
    :func:`assign_participants` derives the participant -> team mapping from
    it and ``teams`` alone, generically (see that function's docstring).
    """

    name: str
    game_mode: str  # "competitive" | "cooperative" — the league CLI's own vocabulary
    scenario_id: str
    seed: int
    expected_participants: int
    teams: tuple[TeamSpec, ...]
    description: str = ""

    @property
    def team_ids(self) -> tuple[str, ...]:
        return tuple(t.team_id for t in self.teams)

    @property
    def bot_team_ids(self) -> tuple[str, ...]:
        return tuple(t.team_id for t in self.teams if t.is_bot)

    def team(self, team_id: str) -> TeamSpec:
        for t in self.teams:
            if t.team_id == team_id:
                return t
        raise KeyError(f"launch mode {self.name!r} has no team {team_id!r}")

    def with_overrides(
        self, *, scenario_id: str | None = None, seed: int | None = None
    ) -> "LaunchMode":
        """A copy of this mode with ``scenario_id``/``seed`` overridden (both
        optional) — how :class:`~league_site.game.adapter.GridLaneEngine`'s
        constructor lets a caller pin a different scenario/seed without
        touching the bundled registry."""
        changes: dict[str, Any] = {}
        if scenario_id is not None:
            changes["scenario_id"] = scenario_id
        if seed is not None:
            changes["seed"] = seed
        return dataclasses.replace(self, **changes) if changes else self


SOLO_VS_BOT = LaunchMode(
    name="solo-vs-bot",
    game_mode="competitive",
    scenario_id=DEFAULT_SCENARIO_ID,
    seed=20260710,
    expected_participants=1,
    teams=(
        TeamSpec(team_id="solo", driver_kind="stateless", is_bot=False, action_cap=1),
        TeamSpec(team_id="house", driver_kind="bot", is_bot=True, action_cap=None),
    ),
    description=(
        "One participant commands the whole roster alone, handicapped to a single "
        "action per turn (platform-enforced), against the house side — which never "
        "stages orders, so the adapter force-resolves every turn with "
        "'league match tick --apply' once the solo side has staged."
    ),
)

TEAM_VS_TEAM = LaunchMode(
    name="team-vs-team",
    game_mode="competitive",
    scenario_id=DEFAULT_SCENARIO_ID,
    seed=20260711,
    expected_participants=2,
    teams=(
        TeamSpec(team_id="blue", driver_kind="stateless", is_bot=False, action_cap=None),
        TeamSpec(team_id="red", driver_kind="stateless", is_bot=False, action_cap=None),
    ),
    description=(
        "Two participants, one team each, competitive mode. No bot side — the "
        "match resolves each turn once both participants have staged, exactly "
        "the league CLI's own 'auto-resolve when every team has staged' rule."
    ),
)

COOP_2 = LaunchMode(
    name="coop-2",
    game_mode="cooperative",
    scenario_id=DEFAULT_SCENARIO_ID,
    seed=20260712,
    expected_participants=2,
    teams=(TeamSpec(team_id="coop", driver_kind="stateless", is_bot=False, action_cap=None),),
    description=(
        "Two participants share one team against the scenario itself (the league "
        "engine's cooperative mode needs exactly one team). Participants take "
        "turns commanding the shared roster; because there is only one team, "
        "every 'league match act --apply' call resolves that turn immediately."
    ),
)

_REGISTRY: tuple[LaunchMode, ...] = (SOLO_VS_BOT, TEAM_VS_TEAM, COOP_2)


def registry() -> tuple[LaunchMode, ...]:
    """The bundled launch-mode registry, in stable declaration order."""
    return _REGISTRY


def mode_names() -> tuple[str, ...]:
    return tuple(m.name for m in _REGISTRY)


def get_mode(name: str) -> LaunchMode:
    for mode in _REGISTRY:
        if mode.name == name:
            return mode
    raise ValueError(f"unknown launch mode {name!r}; known: {', '.join(mode_names())}")


def assign_participants(mode: LaunchMode, participants: Sequence[Participant]) -> dict[str, str]:
    """``participant_id -> team_id`` for every participant, generically.

    Two rules cover all three bundled modes with no per-mode branching:

    * exactly one non-bot team (``solo-vs-bot``'s ``solo``, ``coop-2``'s
      ``coop``) -> every participant is assigned to that one team, whether
      there is one of them (solo) or several sharing it (coop);
    * more than one non-bot team (``team-vs-team``'s ``blue``/``red``) ->
      participants are assigned 1:1, in the order both were given.

    Raises ``ValueError`` if ``len(participants) != mode.expected_participants``
    or (defensively, for a mode with N > 1 non-bot teams) the participant
    count does not divide evenly into a 1:1 assignment.
    """
    if len(participants) != mode.expected_participants:
        raise ValueError(
            f"launch mode {mode.name!r} needs exactly {mode.expected_participants} "
            f"participant(s), got {len(participants)}"
        )
    non_bot_team_ids = [t.team_id for t in mode.teams if not t.is_bot]
    if not non_bot_team_ids:
        raise ValueError(f"launch mode {mode.name!r} has no non-bot team to assign participants to")
    if len(non_bot_team_ids) == 1:
        return {p.participant_id: non_bot_team_ids[0] for p in participants}
    if len(participants) != len(non_bot_team_ids):
        raise ValueError(
            f"launch mode {mode.name!r} needs one participant per non-bot team "
            f"({len(non_bot_team_ids)}), got {len(participants)}"
        )
    return {p.participant_id: team_id for p, team_id in zip(participants, non_bot_team_ids)}


def enforce_action_cap(
    mode: LaunchMode, team_id: str, orders: Mapping[str, Any]
) -> tuple[dict[str, Any], list[Rejection]]:
    """Trim ``orders["actions"]`` to ``mode.team(team_id).action_cap``.

    Returns ``(orders_to_submit, rejections)``: ``orders_to_submit`` is a
    shallow copy of ``orders`` with ``actions`` truncated to the cap (or
    ``orders`` unchanged, as a plain dict copy, if under the cap); every
    action beyond the cap becomes a :class:`Rejection` — refused *before*
    ``league match act`` is ever invoked, per the mode-fairness requirement
    (spec h14: "the excess refused by the platform adapter before match act
    is called").
    """
    team = mode.team(team_id)
    actions = list(orders.get("actions") or [])
    result = dict(orders)
    result["actions"] = actions
    if team.action_cap is None or len(actions) <= team.action_cap:
        return result, []
    kept, excess = actions[: team.action_cap], actions[team.action_cap :]
    result["actions"] = kept
    rejections = [
        Rejection(
            team_id=team_id,
            unit_id=action.get("unit_id") if isinstance(action, Mapping) else None,
            reason=(
                f"platform mode cap: team {team_id!r} allows at most "
                f"{team.action_cap} action(s)/turn; refused before 'league match act'"
            ),
        )
        for action in excess
    ]
    return result, rejections


__all__ = [
    "DEFAULT_SCENARIO_ID",
    "TeamSpec",
    "Rejection",
    "LaunchMode",
    "SOLO_VS_BOT",
    "TEAM_VS_TEAM",
    "COOP_2",
    "registry",
    "mode_names",
    "get_mode",
    "assign_participants",
    "enforce_action_cap",
]
