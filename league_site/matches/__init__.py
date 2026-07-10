"""Match domain: a game-agnostic, continuable match state machine.

This package is the core of the League of Agents arena: a match progresses
``created -> active -> paused -> active -> completed`` (see
:class:`~league_site.matches.match.Match`), driving whatever game is plugged
in behind the :class:`~league_site.matches.engine.GameEngine` interface —
turn exchanges only, no realtime/tick/frame concepts. Match records carry a
benchmark-grade schema (:mod:`league_site.matches.models`): game id,
participants (human or agent, with model/provider identity for agents),
full turn history, and a terminal result.

Persistence is behind :class:`~league_site.matches.store.MatchStore`
(:class:`~league_site.matches.store.InMemoryMatchStore` here; a DynamoDB/S3
adapter skeleton lives in :mod:`league_site.matches.aws`, imported
separately since it is the only module here that touches ``boto3``). See
:mod:`league_site.matches.serialization` for the DynamoDB single-table
design and the S3 archive key scheme.
"""

from __future__ import annotations

from league_site.matches.engine import GameEngine
from league_site.matches.errors import InvalidTransitionError, MatchError, MatchNotFoundError
from league_site.matches.match import Match, MatchStatus
from league_site.matches.models import (
    AgentIdentity,
    MatchResult,
    Participant,
    ParticipantKind,
    TurnRecord,
)
from league_site.matches.serialization import (
    archive_key,
    from_archive_dict,
    from_item,
    to_archive_dict,
    to_item,
)
from league_site.matches.store import InMemoryMatchStore, MatchStore

__all__ = [
    "AgentIdentity",
    "GameEngine",
    "InMemoryMatchStore",
    "InvalidTransitionError",
    "Match",
    "MatchError",
    "MatchNotFoundError",
    "MatchResult",
    "MatchStatus",
    "MatchStore",
    "Participant",
    "ParticipantKind",
    "TurnRecord",
    "archive_key",
    "from_archive_dict",
    "from_item",
    "to_archive_dict",
    "to_item",
]
