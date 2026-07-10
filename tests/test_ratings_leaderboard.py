"""Tests for ``league_site.ratings.leaderboard``.

Covers the acceptance criteria "a newly recorded match result is
reflected in the very next leaderboard() call" and "the leaderboard
tie-break is stable and documented", plus the leaderboard half of the
replay-determinism criterion.
"""

from __future__ import annotations

from league_site.matches import ParticipantKind
from league_site.ratings import (
    InMemoryRatingLedgerStore,
    IntegerEloRatingSystem,
    LeaderboardRow,
    MatchOutcome,
    OutcomeEntry,
    RatingIdentity,
    leaderboard,
    leaderboard_markdown,
)

HUMAN = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Ada")
AGENT = RatingIdentity(
    kind=ParticipantKind.AGENT,
    display_name="Sonnet",
    model="claude-sonnet-5",
    provider="anthropic",
)
THIRD = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Zed")
SYSTEM = IntegerEloRatingSystem(k_factor=32)


def _outcome(match_id: str, *pairs: tuple[RatingIdentity, int]) -> MatchOutcome:
    return MatchOutcome(
        match_id=match_id,
        entries=tuple(OutcomeEntry(identity=identity, score=score) for identity, score in pairs),
    )


def test_leaderboard_is_empty_for_a_fresh_store() -> None:
    store = InMemoryRatingLedgerStore()
    assert leaderboard(store) == []


def test_leaderboard_orders_by_rating_descending() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)

    rows = leaderboard(store)
    assert [row.identity for row in rows] == [HUMAN, AGENT]
    assert rows[0].rating > rows[1].rating
    assert [row.rank for row in rows] == [1, 2]


def test_leaderboard_respects_limit() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3), (THIRD, 1)), SYSTEM)

    rows = leaderboard(store, limit=2)
    assert len(rows) == 2
    assert rows == leaderboard(store)[:2]


def test_leaderboard_tie_break_is_ascending_by_identity_sort_key() -> None:
    """Documented rule: equal rating -> ascending (kind, display_name, model, provider).

    Construct two identities that never played each other, so both sit at
    INITIAL_RATING, and confirm the tie is broken purely by identity, not
    by insertion order into the store.
    """
    store = InMemoryRatingLedgerStore()
    # A draw at equal rating yields a delta of 0 for both sides, so HUMAN
    # and THIRD are still tied at INITIAL_RATING after this one match.
    store.record_match(_outcome("m1", (HUMAN, 5), (THIRD, 5)), SYSTEM)

    rows = leaderboard(store)
    ratings = {row.identity: row.rating for row in rows}
    assert ratings[HUMAN] == ratings[THIRD]
    human_rank = next(row.rank for row in rows if row.identity == HUMAN)
    third_rank = next(row.rank for row in rows if row.identity == THIRD)
    # HUMAN.display_name "Ada" < THIRD.display_name "Zed" -> HUMAN ranks first
    assert human_rank < third_rank
    assert HUMAN.sort_key() < THIRD.sort_key()


def test_leaderboard_tie_break_orders_agent_kind_before_human_kind() -> None:
    store = InMemoryRatingLedgerStore()
    agent_no_history_peer = RatingIdentity(
        kind=ParticipantKind.AGENT, display_name="Aaa", model="m", provider="p"
    )
    # Give both identities a recorded (drawn) match so they both appear on
    # the leaderboard at the same rating.
    store.record_match(_outcome("m1", (HUMAN, 5), (agent_no_history_peer, 5)), SYSTEM)

    rows = leaderboard(store)
    assert [row.identity for row in rows] == [agent_no_history_peer, HUMAN]
    assert agent_no_history_peer.sort_key() < HUMAN.sort_key()


def test_leaderboard_includes_provider_identity_for_agents() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)

    rows = leaderboard(store)
    agent_row = next(row for row in rows if row.identity.kind is ParticipantKind.AGENT)
    assert agent_row.identity.model == "claude-sonnet-5"
    assert agent_row.identity.provider == "anthropic"


# --- within-one-refresh (core acceptance criterion) -------------------------


def test_leaderboard_reflects_a_newly_recorded_match_on_the_very_next_call() -> None:
    store = InMemoryRatingLedgerStore()
    assert leaderboard(store) == []

    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)
    first_call = leaderboard(store)
    assert {row.identity for row in first_call} == {HUMAN, AGENT}

    store.record_match(_outcome("m2", (THIRD, 9), (AGENT, 1)), SYSTEM)
    second_call = leaderboard(store)
    assert {row.identity for row in second_call} == {HUMAN, AGENT, THIRD}
    assert second_call != first_call


# --- replay determinism -----------------------------------------------------


def test_leaderboard_is_identical_across_independent_replays_of_the_same_history() -> None:
    def _replayed_leaderboard() -> list[LeaderboardRow]:
        store = InMemoryRatingLedgerStore()
        system = IntegerEloRatingSystem(k_factor=32)
        store.record_match(_outcome("m1", (THIRD, 1), (AGENT, 9), (HUMAN, 5)), system)
        store.record_match(_outcome("m2", (HUMAN, 4), (THIRD, 4), (AGENT, 4)), system)
        store.record_match(_outcome("m3", (AGENT, 2), (HUMAN, 9), (THIRD, 1)), system)
        return leaderboard(store)

    first = _replayed_leaderboard()
    second = _replayed_leaderboard()
    assert first == second


# --- markdown rendering ------------------------------------------------------


def test_leaderboard_markdown_renders_header_and_rows() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)

    rendered = leaderboard_markdown(store)
    lines = rendered.splitlines()
    assert lines[0] == "| Rank | Identity | Kind | Model | Provider | Rating | Matches |"
    assert lines[1].startswith("| --- |")
    assert len(lines) == 4  # header + separator + 2 rows
    assert "Ada" in rendered
    assert "Sonnet" in rendered


def test_leaderboard_markdown_shows_provider_for_agents_and_dash_for_humans() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)

    rendered = leaderboard_markdown(store)
    human_line = next(line for line in rendered.splitlines() if "Ada" in line)
    agent_line = next(line for line in rendered.splitlines() if "Sonnet" in line)

    assert "| - | - |" in human_line
    assert "claude-sonnet-5" in agent_line
    assert "anthropic" in agent_line


def test_leaderboard_markdown_respects_limit() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3), (THIRD, 1)), SYSTEM)

    rendered = leaderboard_markdown(store, limit=1)
    lines = rendered.splitlines()
    assert len(lines) == 3  # header + separator + 1 row
