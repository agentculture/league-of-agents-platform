"""Match state machine.

State diagram::

    created --start--> active --take_turn--> active
                        active --pause--> paused --resume--> active
                        active --complete--> completed

Every transition is an explicit method (``start``, ``take_turn``, ``pause``,
``resume``, ``complete``); calling one from a status that doesn't allow it
raises :class:`~league_site.matches.errors.InvalidTransitionError`. There is
no implicit/automatic advance — e.g. ``take_turn`` never auto-completes the
match even if the engine reports ``is_over``; the caller decides when to
call ``complete``.

``Match`` itself never imports a concrete game — it only depends on the
:class:`~league_site.matches.engine.GameEngine` interface, so it stays
game-agnostic. It also never imports ``boto3``; persistence lives in
:mod:`league_site.matches.store` and :mod:`league_site.matches.serialization`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from league_site.matches._util import utcnow
from league_site.matches.engine import GameEngine
from league_site.matches.errors import InvalidTransitionError
from league_site.matches.models import MatchResult, Participant, TurnRecord


class MatchStatus(str, Enum):
    """Lifecycle states. See the module docstring for the transition diagram."""

    CREATED = "created"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


@dataclass
class Match:
    """A single continuable match between ``participants`` playing ``game_id``.

    Carries the benchmark-grade schema: ``game_id``, ``participants`` (each
    with display name, kind, and — for agents — model/provider identity via
    :class:`~league_site.matches.models.AgentIdentity`), the full ``turns``
    history, and a terminal ``result`` once completed.

    No ``GameEngine`` reference is stored on the instance — engines are
    passed in per-call — so a ``Match`` stays a plain, serializable value
    even though playing it requires an engine.
    """

    match_id: str
    game_id: str
    participants: tuple[Participant, ...]
    status: MatchStatus = MatchStatus.CREATED
    game_state: Any = None
    turns: list[TurnRecord] = field(default_factory=list)
    result: MatchResult | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    @classmethod
    def create(
        cls,
        game_id: str,
        participants: Sequence[Participant],
        *,
        match_id: str | None = None,
    ) -> Match:
        """Construct a fresh ``CREATED`` match with a generated id if none is given."""
        return cls(
            match_id=match_id or uuid.uuid4().hex,
            game_id=game_id,
            participants=tuple(participants),
        )

    def start(self, engine: GameEngine) -> None:
        """``created -> active``: ask the engine for the initial game state."""
        self._expect(MatchStatus.CREATED, "start")
        self.game_state = engine.initial_state(self.participants)
        self.status = MatchStatus.ACTIVE
        self._touch()

    def take_turn(self, engine: GameEngine, participant_id: str, action: Any) -> None:
        """``active -> active``: apply one participant's action and record it."""
        self._expect(MatchStatus.ACTIVE, "take_turn")
        self.game_state = engine.apply_turn(self.game_state, participant_id, action)
        self.turns.append(
            TurnRecord(
                turn_number=len(self.turns) + 1,
                participant_id=participant_id,
                action=action,
                timestamp=utcnow(),
            )
        )
        self._touch()

    def pause(self) -> None:
        """``active -> paused``."""
        self._expect(MatchStatus.ACTIVE, "pause")
        self.status = MatchStatus.PAUSED
        self._touch()

    def resume(self) -> None:
        """``paused -> active``."""
        self._expect(MatchStatus.PAUSED, "resume")
        self.status = MatchStatus.ACTIVE
        self._touch()

    def complete(self, engine: GameEngine) -> None:
        """``active -> completed``: score the current state and record the result."""
        self._expect(MatchStatus.ACTIVE, "complete")
        scores = dict(engine.score(self.game_state))
        winner = _sole_leader(scores)
        self.result = MatchResult(completed=True, winner_participant_id=winner, scores=scores)
        self.status = MatchStatus.COMPLETED
        self._touch()

    def _expect(self, required: MatchStatus, action: str) -> None:
        if self.status is not required:
            raise InvalidTransitionError(action, self.status.value)

    def _touch(self) -> None:
        self.updated_at = utcnow()


def _sole_leader(scores: dict[str, float]) -> str | None:
    """The one participant with the strictly highest score, or ``None`` on a tie.

    ``None`` covers both "no scores at all" and "two or more participants
    tied for the top score" — the latter is a draw (e.g. a 0.0-0.0 finish)
    and must not crown a winner just because ``dict`` iteration order picked
    one tied participant over another. This mirrors
    :class:`~league_site.ratings.system.IntegerEloRatingSystem`, which
    already treats equal hard scores as a draw (500/500 millipoints); a
    match's own recorded winner must agree with how it gets rated.
    """
    if not scores:
        return None
    best = max(scores.values())
    leaders = [participant_id for participant_id, score in scores.items() if score == best]
    return leaders[0] if len(leaders) == 1 else None
