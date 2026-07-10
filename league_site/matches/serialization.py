"""``Match`` <-> persistence-shape conversions.

Pure stdlib — no ``boto3`` import here or anywhere else in this package
except :mod:`league_site.matches.aws`. These functions produce/consume
plain ``dict``s so they work equally for the in-memory store
(:mod:`league_site.matches.store`), a real DynamoDB table, and S3 JSON
archives, without this module knowing which backend is in use.

DynamoDB single-table design
-----------------------------
One table, keyed by a generic ``PK``/``SK`` pair. A match's canonical record
is a single item::

    PK              SK          Attributes
    MATCH#<id>      METADATA    entity_type, match_id, game_id, status,
                                 participants (List), turns (List), result
                                 (Map | null), game_state, created_at,
                                 updated_at

The full turn history is embedded as a ``List`` attribute on the metadata
item rather than fanned out into ``SK=TURN#<seq>`` items. That keeps
``to_item``/``from_item`` a single round-trip (matching the "mid-game
save/load round-trips to identical state" requirement) and is well within
DynamoDB's 400 KB item limit for the turn-based, human/agent-paced games
this platform targets. If a future game accumulates enough turns to
threaten that limit, splitting turns into their own ``SK=TURN#<seq>`` items
under the same ``PK`` is a natural, additive migration — the single-table
design already reserves the ``SK`` axis for it.

Suggested access patterns (not wired up yet — a later task):

* Get one match: ``GetItem(PK=MATCH#<id>, SK=METADATA)``.
* List matches by status/game: a GSI with
  ``GSI1PK=STATUS#<status>`` or ``GSI1PK=GAME#<game_id>``,
  ``GSI1SK=updated_at`` for recency ordering.

S3 archive layout
------------------
Completed matches archive as one JSON object per match, at::

    archives/{year}/{match_id}.json

where ``{year}`` is the four-digit UTC year of ``match.created_at`` and the
body is :func:`to_archive_dict` (the same shape as :func:`to_item` minus the
DynamoDB ``PK``/``SK``/``entity_type`` bookkeeping attributes). Archiving is
a later task's concern (price-aware archive/cleanup); this module only
defines the key scheme and payload shape so that task has a stable target.

Fidelity note: ``game_state`` and each turn's ``action`` are opaque,
engine-defined payloads (see :mod:`league_site.matches.engine`). Games that
want full archive fidelity should keep them JSON-safe
(``dict``/``list``/``str``/``int``/``float``/``bool``/``None``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from league_site.matches.match import Match, MatchStatus
from league_site.matches.models import (
    AgentIdentity,
    MatchResult,
    Participant,
    ParticipantKind,
    TurnRecord,
)

# --- participant -----------------------------------------------------------


def _agent_identity_to_dict(identity: AgentIdentity | None) -> dict[str, str] | None:
    if identity is None:
        return None
    return {"model": identity.model, "provider": identity.provider}


def _agent_identity_from_dict(data: dict[str, str] | None) -> AgentIdentity | None:
    if data is None:
        return None
    return AgentIdentity(model=data["model"], provider=data["provider"])


def _participant_to_dict(participant: Participant) -> dict[str, Any]:
    return {
        "participant_id": participant.participant_id,
        "display_name": participant.display_name,
        "kind": participant.kind.value,
        "agent_identity": _agent_identity_to_dict(participant.agent_identity),
    }


def _participant_from_dict(data: dict[str, Any]) -> Participant:
    return Participant(
        participant_id=data["participant_id"],
        display_name=data["display_name"],
        kind=ParticipantKind(data["kind"]),
        agent_identity=_agent_identity_from_dict(data.get("agent_identity")),
    )


# --- turn history ------------------------------------------------------------


def _turn_to_dict(turn: TurnRecord) -> dict[str, Any]:
    return {
        "turn_number": turn.turn_number,
        "participant_id": turn.participant_id,
        "action": turn.action,
        "timestamp": turn.timestamp.isoformat(),
    }


def _turn_from_dict(data: dict[str, Any]) -> TurnRecord:
    return TurnRecord(
        turn_number=data["turn_number"],
        participant_id=data["participant_id"],
        action=data["action"],
        timestamp=datetime.fromisoformat(data["timestamp"]),
    )


# --- result --------------------------------------------------------------


def _result_to_dict(result: MatchResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "completed": result.completed,
        "winner_participant_id": result.winner_participant_id,
        "scores": dict(result.scores),
        "summary": result.summary,
    }


def _result_from_dict(data: dict[str, Any] | None) -> MatchResult | None:
    if data is None:
        return None
    return MatchResult(
        completed=data["completed"],
        winner_participant_id=data.get("winner_participant_id"),
        scores=dict(data.get("scores", {})),
        summary=data.get("summary", ""),
    )


# --- DynamoDB item mapping -------------------------------------------------


def to_item(match: Match) -> dict[str, Any]:
    """Convert ``match`` to a DynamoDB item dict (see module docstring for the key scheme)."""
    return {
        "PK": f"MATCH#{match.match_id}",
        "SK": "METADATA",
        "entity_type": "match",
        "match_id": match.match_id,
        "game_id": match.game_id,
        "status": match.status.value,
        "participants": [_participant_to_dict(p) for p in match.participants],
        "turns": [_turn_to_dict(t) for t in match.turns],
        "result": _result_to_dict(match.result),
        "game_state": match.game_state,
        "created_at": match.created_at.isoformat(),
        "updated_at": match.updated_at.isoformat(),
    }


def from_item(item: dict[str, Any]) -> Match:
    """Reconstruct a ``Match`` from a DynamoDB item dict produced by :func:`to_item`."""
    return Match(
        match_id=item["match_id"],
        game_id=item["game_id"],
        participants=tuple(_participant_from_dict(p) for p in item["participants"]),
        status=MatchStatus(item["status"]),
        game_state=item.get("game_state"),
        turns=[_turn_from_dict(t) for t in item["turns"]],
        result=_result_from_dict(item.get("result")),
        created_at=datetime.fromisoformat(item["created_at"]),
        updated_at=datetime.fromisoformat(item["updated_at"]),
    )


# --- S3 archive layout -----------------------------------------------------


def archive_key(match: Match) -> str:
    """S3 key for ``match``'s archive: ``archives/{year}/{match_id}.json``."""
    return f"archives/{match.created_at.year}/{match.match_id}.json"


def to_archive_dict(match: Match) -> dict[str, Any]:
    """JSON-serializable archive payload: :func:`to_item` minus the DynamoDB-only keys."""
    item = to_item(match)
    for dynamo_only_key in ("PK", "SK", "entity_type"):
        item.pop(dynamo_only_key, None)
    return item


def from_archive_dict(data: dict[str, Any]) -> Match:
    """Reconstruct a ``Match`` from an archive payload produced by :func:`to_archive_dict`."""
    return from_item(data)
