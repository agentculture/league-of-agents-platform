"""Shared test-only fixtures for the ``league_site.viewer`` test suite.

Not collected by pytest (module name doesn't match ``test_*``); imported
directly by the ``test_viewer_*`` modules.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from league_site.matches import (
    AgentIdentity,
    GameEngine,
    Match,
    Participant,
    ParticipantKind,
)


class TallyEngine(GameEngine):
    """A minimal, never-auto-ending engine for driving the viewer's tests.

    Each participant has a running per-participant tally; a turn's
    ``action`` is read for an optional ``{"delta": int}`` and is otherwise
    treated as fully opaque (per :class:`~league_site.matches.engine.
    GameEngine`'s contract) -- tests attach whatever extra keys (``"message"``,
    arbitrary "orders" payloads, hostile strings, ...) they need to exercise
    the viewer's rendering directly, since the whole ``action`` dict a caller
    passes to :meth:`~league_site.matches.match.Match.take_turn` is stored
    verbatim on the resulting :class:`~league_site.matches.models.TurnRecord`.

    :meth:`is_over` always returns ``False`` -- per
    :class:`~league_site.matches.match.Match`'s "no implicit/automatic
    advance" rule, this engine never ends a match on its own; a test calls
    ``match.complete(engine)`` explicitly when it wants a finished match.
    """

    def __init__(self, *, game_id: str = "viewer-demo") -> None:
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Sequence[Participant]) -> dict[str, Any]:
        return {"scores": {p.participant_id: 0 for p in participants}}

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        delta = action.get("delta", 0) if isinstance(action, dict) else 0
        scores = dict(state["scores"])
        scores[participant_id] = scores.get(participant_id, 0) + delta
        return {"scores": scores}

    def is_over(self, state: dict[str, Any]) -> bool:
        return False

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        return {pid: float(total) for pid, total in state["scores"].items()}


def make_participants() -> tuple[Participant, Participant]:
    """One human + one agent participant, with a full benchmark identity."""
    human = Participant(display_name="Ada", kind=ParticipantKind.HUMAN, participant_id="p-human")
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-agent",
    )
    return human, agent


def start_match(
    *,
    match_id: str = "m-viewer",
    game_id: str = "viewer-demo",
    participants: Sequence[Participant] | None = None,
) -> tuple[Match, TallyEngine]:
    """A freshly ``start``\\ ed :class:`Match` plus the :class:`TallyEngine` driving it."""
    engine = TallyEngine(game_id=game_id)
    match = Match.create(
        game_id=game_id, participants=participants or make_participants(), match_id=match_id
    )
    match.start(engine)
    return match, engine
