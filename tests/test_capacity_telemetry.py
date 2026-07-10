"""Tests for :mod:`league_site.capacity.telemetry`.

Exercises h26's acceptance criterion — telemetry returns the three
month-one numbers (registrations, completed matches, distinct providers)
from seeded stores.
"""

from __future__ import annotations

from datetime import datetime, timezone

from league_site.auth.token_store import TokenRecord
from league_site.capacity.telemetry import telemetry_snapshot
from league_site.matches import InMemoryMatchStore, Match, ParticipantKind
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from league_site.ratings.system import (
    IntegerEloRatingSystem,
    MatchOutcome,
    OutcomeEntry,
    RatingIdentity,
)
from tests._matches_support import CounterGameEngine, make_participants


def _token(token_id: str) -> TokenRecord:
    return TokenRecord(
        token_id=token_id,
        token_hash=f"{token_id}-hash",
        agent_name=f"agent-{token_id}",
        model="claude-sonnet-5",
        provider="anthropic",
        created_at=datetime.now(timezone.utc),
    )


def _completed_match(match_id: str) -> Match:
    human, agent = make_participants()
    engine = CounterGameEngine(target=1, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 5})
    match.complete(engine)
    return match


def _active_match(match_id: str) -> Match:
    human, agent = make_participants()
    engine = CounterGameEngine(target=1000, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    match.start(engine)
    return match


def test_snapshot_with_no_sources_returns_all_zero_counters() -> None:
    assert telemetry_snapshot() == {
        "registrations": 0,
        "completed_matches": 0,
        "distinct_providers": 0,
    }


def test_returns_a_plain_dict_with_exactly_the_three_counters() -> None:
    result = telemetry_snapshot()
    assert isinstance(result, dict)
    assert set(result.keys()) == {"registrations", "completed_matches", "distinct_providers"}


def test_completed_matches_counts_only_completed_status() -> None:
    store = InMemoryMatchStore()
    store.save(_completed_match("m-1"))
    store.save(_completed_match("m-2"))
    store.save(_active_match("m-3"))

    result = telemetry_snapshot(match_store=store)

    assert result["completed_matches"] == 2


def test_registrations_dedupes_agent_tokens_by_token_id() -> None:
    tokens = [_token("t-1"), _token("t-2"), _token("t-1")]  # t-1 rotated, still one agent

    result = telemetry_snapshot(agent_tokens=tokens)

    assert result["registrations"] == 2


def test_registrations_dedupes_human_subjects() -> None:
    result = telemetry_snapshot(human_subjects=["u-1", "u-2", "u-1"])

    assert result["registrations"] == 2


def test_registrations_sums_agent_and_human_counts() -> None:
    result = telemetry_snapshot(
        agent_tokens=[_token("t-1"), _token("t-2")],
        human_subjects=["u-1"],
    )

    assert result["registrations"] == 3


def test_distinct_providers_counts_agent_providers_on_the_leaderboard() -> None:
    ledger = InMemoryRatingLedgerStore()
    rating_system = IntegerEloRatingSystem()

    agent_a = RatingIdentity(
        kind=ParticipantKind.AGENT, display_name="A", model="m1", provider="anthropic"
    )
    agent_b = RatingIdentity(
        kind=ParticipantKind.AGENT, display_name="B", model="m2", provider="openai"
    )
    agent_c = RatingIdentity(
        kind=ParticipantKind.AGENT, display_name="C", model="m3", provider="anthropic"
    )
    human = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Ada")

    ledger.record_match(
        MatchOutcome(
            match_id="r-1",
            entries=(
                OutcomeEntry(identity=agent_a, score=1),
                OutcomeEntry(identity=agent_b, score=0),
            ),
        ),
        rating_system,
    )
    ledger.record_match(
        MatchOutcome(
            match_id="r-2",
            entries=(
                OutcomeEntry(identity=agent_c, score=1),
                OutcomeEntry(identity=human, score=0),
            ),
        ),
        rating_system,
    )

    result = telemetry_snapshot(rating_store=ledger)

    # anthropic (agent_a, agent_c) + openai (agent_b) = 2 distinct providers;
    # the human identity carries no provider and must not be counted.
    assert result["distinct_providers"] == 2


def test_full_snapshot_from_seeded_stores_reads_all_three_month_one_numbers() -> None:
    match_store = InMemoryMatchStore()
    for i in range(3):
        match_store.save(_completed_match(f"m-{i}"))
    match_store.save(_active_match("m-active"))

    ledger = InMemoryRatingLedgerStore()
    rating_system = IntegerEloRatingSystem()
    agent_a = RatingIdentity(
        kind=ParticipantKind.AGENT, display_name="A", model="m1", provider="anthropic"
    )
    agent_b = RatingIdentity(
        kind=ParticipantKind.AGENT, display_name="B", model="m2", provider="openai"
    )
    ledger.record_match(
        MatchOutcome(
            match_id="r-1",
            entries=(
                OutcomeEntry(identity=agent_a, score=1),
                OutcomeEntry(identity=agent_b, score=0),
            ),
        ),
        rating_system,
    )

    result = telemetry_snapshot(
        match_store=match_store,
        rating_store=ledger,
        agent_tokens=[_token("t-1"), _token("t-2")],
        human_subjects=["u-1"],
    )

    assert result == {
        "registrations": 3,
        "completed_matches": 3,
        "distinct_providers": 2,
    }
