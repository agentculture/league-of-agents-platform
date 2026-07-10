"""Shared test-only fixtures for the ``league_site.profiles`` test suite.

Not collected by pytest (module name doesn't match ``test_*``); imported
directly by the ``test_profiles_*`` modules.

Builds one small, deterministic scenario — three identities (one human, two
agents), three completed matches, played and recorded through the exact
domain APIs (:mod:`league_site.matches`, :mod:`league_site.ratings`) rather
than hand-rolled shortcuts — that every ``test_profiles_*`` module reuses so
the "same ledger state -> byte-identical output" determinism tests are
comparing against one canonical scenario.
"""

from __future__ import annotations

from dataclasses import dataclass

from league_site.matches import (
    AgentIdentity,
    InMemoryMatchStore,
    Match,
    MatchResult,
    MatchStatus,
    Participant,
    ParticipantKind,
)
from league_site.ratings import (
    InMemoryRatingLedgerStore,
    IntegerEloRatingSystem,
    RatingIdentity,
    outcome_from_match,
)

ADA = Participant(display_name="Ada", kind=ParticipantKind.HUMAN, participant_id="p-ada")
SONNET = Participant(
    display_name="Sonnet",
    kind=ParticipantKind.AGENT,
    agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
    participant_id="p-sonnet",
)
RIVAL = Participant(
    display_name="Rival",
    kind=ParticipantKind.AGENT,
    agent_identity=AgentIdentity(model="gpt-4", provider="openai"),
    participant_id="p-rival",
)

ADA_IDENTITY = RatingIdentity.from_participant(ADA)
SONNET_IDENTITY = RatingIdentity.from_participant(SONNET)
RIVAL_IDENTITY = RatingIdentity.from_participant(RIVAL)


def make_completed_match(
    match_id: str,
    participants: tuple[Participant, ...],
    scores: dict[str, float],
    *,
    game_id: str = "league-of-agents-v1",
) -> Match:
    """A already-``COMPLETED`` :class:`Match` with *scores*, ready for
    :func:`~league_site.ratings.system.outcome_from_match`."""
    match = Match.create(game_id=game_id, participants=participants, match_id=match_id)
    match.status = MatchStatus.COMPLETED
    winner = max(scores, key=scores.get) if scores else None  # type: ignore[arg-type]
    match.result = MatchResult(completed=True, winner_participant_id=winner, scores=scores)
    return match


@dataclass(frozen=True)
class Scenario:
    """A fully wired ledger + match store, plus the identities/match ids used."""

    ledger_store: InMemoryRatingLedgerStore
    match_store: InMemoryMatchStore
    match_m1: Match
    match_m2: Match
    match_m3: Match


def build_scenario() -> Scenario:
    """Ada beats Sonnet, Sonnet beats Rival, Ada and Rival draw.

    Each match is saved to the match store *and* recorded into the ledger via
    :func:`~league_site.ratings.system.outcome_from_match`, in match order —
    the same two-step flow a real match completion would drive. Resulting
    per-identity history lengths: Ada 2 (m1, m3), Sonnet 2 (m1, m2), Rival 2
    (m2, m3) — enough for "recent matches" + "rating history" to be
    meaningfully non-trivial for every identity.
    """
    match_store = InMemoryMatchStore()
    ledger_store = InMemoryRatingLedgerStore()
    system = IntegerEloRatingSystem(k_factor=32)

    m1 = make_completed_match("m1", (ADA, SONNET), {"p-ada": 10.0, "p-sonnet": 3.0})
    m2 = make_completed_match("m2", (SONNET, RIVAL), {"p-sonnet": 8.0, "p-rival": 2.0})
    m3 = make_completed_match("m3", (ADA, RIVAL), {"p-ada": 5.0, "p-rival": 5.0})

    for match in (m1, m2, m3):
        match_store.save(match)
        ledger_store.record_match(outcome_from_match(match), system)

    return Scenario(
        ledger_store=ledger_store, match_store=match_store, match_m1=m1, match_m2=m2, match_m3=m3
    )
