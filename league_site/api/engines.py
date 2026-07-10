"""The default, built-in stub :class:`~league_site.matches.engine.GameEngine`.

This is a placeholder registry entry, not a real game: a minimal,
deterministic turn-taking engine good enough to exercise the whole match
API surface end to end (create -> turns -> auto-complete -> score ->
rating) before the real grid-lane game exists. The real adapter
(``league_site.game``, being built in parallel by another task) registers
into a :mod:`league_site.api.wsgi` ``engine_registry`` post-merge — this
module intentionally never imports ``league_site.game``, so that wiring
stays a later, purely additive change.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from league_site.matches import GameEngine, Participant

#: The registry key (and default ``game_id``) the built-in stub engine
#: registers under, and the ``mode`` a create-match request falls back to
#: when its body omits ``mode`` entirely.
DEFAULT_MODE = "stub-duel"

#: Point values a turn's ``{"points": <value>}`` action may legally carry.
LEGAL_POINTS: tuple[int, ...] = (1, 2, 3)


class StubDuelEngine(GameEngine):
    """Deterministic placeholder engine: participants add points to their own score.

    Participants take turns in the fixed order :meth:`initial_state` was
    given; a solo-practice match (a single participant) simply keeps
    taking its own turn every time, so the same engine serves both a
    one-participant "practice" match and a real multi-participant duel with
    no special-casing. A turn's action must be ``{"points": n}`` with ``n``
    one of :data:`LEGAL_POINTS`; anything else raises :class:`ValueError`
    (translated to a ``400`` by :mod:`league_site.api.wsgi`), as does a
    participant acting out of turn.

    The match ends once any participant's running score reaches ``target``
    or ``max_turns`` total turns have been taken across all participants,
    whichever comes first — both are constructor knobs so tests can force
    a short game. :meth:`score` returns every participant's raw point
    total, not just the winner's, so a two-participant match always has
    the two-or-more scored entries
    :class:`~league_site.ratings.system.IntegerEloRatingSystem` requires
    in order to rate it.
    """

    def __init__(
        self, *, target: int = 10, max_turns: int = 40, game_id: str = DEFAULT_MODE
    ) -> None:
        self._target = target
        self._max_turns = max_turns
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Sequence[Participant]) -> dict[str, Any]:
        order = [participant.participant_id for participant in participants]
        return {
            "participant_order": order,
            "scores": {participant_id: 0 for participant_id in order},
            "turn_index": 0,
            "turns_taken": 0,
            "target": self._target,
            "legal_actions": list(LEGAL_POINTS),
        }

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        order = state["participant_order"]
        if not order:
            raise ValueError("match has no participants to take a turn")
        expected = order[state["turn_index"] % len(order)]
        if participant_id != expected:
            raise ValueError(f"it is not participant {participant_id!r}'s turn")
        points = action.get("points") if isinstance(action, Mapping) else None
        if points not in LEGAL_POINTS:
            raise ValueError(f"illegal action {action!r}: 'points' must be one of {LEGAL_POINTS}")
        scores = dict(state["scores"])
        scores[participant_id] = scores.get(participant_id, 0) + points
        return {
            **state,
            "scores": scores,
            "turn_index": state["turn_index"] + 1,
            "turns_taken": state["turns_taken"] + 1,
        }

    def is_over(self, state: dict[str, Any]) -> bool:
        if state["turns_taken"] >= self._max_turns:
            return True
        return any(score >= state["target"] for score in state["scores"].values())

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        return {participant_id: float(total) for participant_id, total in state["scores"].items()}


#: ``game_id -> GameEngine factory``, the shape
#: :func:`league_site.api.wsgi.with_api`'s ``engine_registry`` argument
#: takes. Keyed by :data:`DEFAULT_MODE` today; the real grid adapter
#: registers under its own key post-merge.
DEFAULT_ENGINE_REGISTRY: Mapping[str, Callable[[], GameEngine]] = {
    DEFAULT_MODE: lambda: StubDuelEngine(game_id=DEFAULT_MODE),
}
