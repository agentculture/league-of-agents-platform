"""Shared test-only fixtures for the ``league_site.matches`` test suite.

Not collected by pytest (module name doesn't match ``test_*``); imported
directly by the ``test_matches_*`` modules.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from league_site.matches import AgentIdentity, GameEngine, Participant, ParticipantKind


class CounterGameEngine(GameEngine):
    """Minimal toy engine used to exercise the ``Match``/``GameEngine`` contract.

    Two participants alternately submit ``{"delta": int}``; state tracks a
    running total. The game is over once the total reaches ``target``. State
    and actions are plain JSON-safe dicts/ints so archive round-trip tests
    can exercise real ``json.dumps``/``json.loads``.
    """

    def __init__(self, *, target: int = 10, game_id: str = "counter-demo") -> None:
        self._target = target
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Sequence[Participant]) -> dict[str, Any]:
        return {"total": 0, "turns_taken": 0, "last_participant_id": None}

    def apply_turn(
        self, state: dict[str, Any], participant_id: str, action: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "total": state["total"] + action["delta"],
            "turns_taken": state["turns_taken"] + 1,
            "last_participant_id": participant_id,
        }

    def is_over(self, state: dict[str, Any]) -> bool:
        return state["total"] >= self._target

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        winner = state.get("last_participant_id")
        if winner is None:
            return {}
        return {winner: float(state["total"])}


class FixedScoreEngine(GameEngine):
    """Toy engine whose :meth:`score` is fixed at construction time.

    Ends immediately (:meth:`is_over` is always ``True``) and never mutates
    state on a turn — used to drive :meth:`~league_site.matches.match.Match.
    complete` straight to a known, deliberately chosen score map (e.g. a
    tie), without needing a game whose rules can actually produce one.
    """

    def __init__(self, scores: dict[str, float], *, game_id: str = "fixed-score-demo") -> None:
        self._scores = dict(scores)
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Sequence[Participant]) -> dict[str, Any]:
        return {}

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        return state

    def is_over(self, state: dict[str, Any]) -> bool:
        return True

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        return dict(self._scores)


def make_participants() -> tuple[Participant, Participant]:
    """One human + one agent participant, with a full benchmark identity."""
    human = Participant(
        display_name="Ada",
        kind=ParticipantKind.HUMAN,
        participant_id="p-human",
    )
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-agent",
    )
    return human, agent
