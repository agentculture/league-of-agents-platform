"""Benchmark-grade participant, turn, and result schema.

These types exist so match records are comparable across agents as
benchmark data: every participant records whether it was a human or an
agent, and agents additionally carry the exact model + provider identity
that produced their turns. A match's ``result`` (see
:class:`league_site.matches.match.Match`) scores participants by
``participant_id``, so results can be aggregated per model/provider across
many matches.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from league_site.matches._util import utcnow


class ParticipantKind(str, Enum):
    """Who is behind a participant: a human player or an AI agent."""

    HUMAN = "human"
    AGENT = "agent"


@dataclass(frozen=True)
class AgentIdentity:
    """Model + provider identity for an agent participant.

    Required for every ``kind=AGENT`` participant so benchmark results are
    attributable to a specific model (e.g. ``model="claude-sonnet-5"``,
    ``provider="anthropic"``).
    """

    model: str
    provider: str


@dataclass(frozen=True)
class Participant:
    """One side of a match.

    Human participants carry only a display name. Agent participants must
    also carry an :class:`AgentIdentity`; the reverse (a human with an
    agent identity) is also rejected — the schema keeps the two kinds
    mutually exclusive on purpose.
    """

    display_name: str
    kind: ParticipantKind
    agent_identity: AgentIdentity | None = None
    participant_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if self.kind is ParticipantKind.AGENT and self.agent_identity is None:
            raise ValueError("agent participants must carry an AgentIdentity (model + provider)")
        if self.kind is ParticipantKind.HUMAN and self.agent_identity is not None:
            raise ValueError("human participants must not carry an AgentIdentity")


@dataclass(frozen=True)
class TurnRecord:
    """One entry in a match's turn history.

    ``action`` is the opaque, game-defined payload a participant submitted
    (see :class:`league_site.matches.engine.GameEngine.apply_turn`). Games
    that want their matches archivable to S3 should keep ``action`` JSON-safe
    (``dict``/``list``/``str``/``int``/``float``/``bool``/``None``).
    """

    turn_number: int
    participant_id: str
    action: Any
    timestamp: datetime = field(default_factory=utcnow)


@dataclass
class MatchResult:
    """Terminal outcome of a completed match.

    ``scores`` maps ``participant_id`` to a numeric score so results are
    directly comparable across agents/models as benchmark data.
    """

    completed: bool = False
    winner_participant_id: str | None = None
    scores: dict[str, float] = field(default_factory=dict)
    summary: str = ""
