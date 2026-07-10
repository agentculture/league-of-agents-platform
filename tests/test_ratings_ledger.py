"""Tests for ``league_site.ratings.ledger``.

Covers the acceptance criteria "replaying the same sequence of match
results yields byte-identical ledgers ... (property-style test with
several sequences, including out-of-order identities in a single match)"
and "ratings use integer arithmetic only (test asserts no float appears
in any stored rating or delta)".
"""

from __future__ import annotations

import dataclasses
import random

import pytest

from league_site.matches import ParticipantKind
from league_site.ratings import (
    INITIAL_RATING,
    IdentityRating,
    InMemoryRatingLedgerStore,
    IntegerEloRatingSystem,
    MatchOutcome,
    OutcomeEntry,
    RatingEntry,
    RatingIdentity,
    RatingLedgerStore,
)

HUMAN = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Ada")
AGENT = RatingIdentity(
    kind=ParticipantKind.AGENT,
    display_name="Sonnet",
    model="claude-sonnet-5",
    provider="anthropic",
)
THIRD = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Zed")


def _outcome(match_id: str, *pairs: tuple[RatingIdentity, int]) -> MatchOutcome:
    return MatchOutcome(
        match_id=match_id,
        entries=tuple(OutcomeEntry(identity=identity, score=score) for identity, score in pairs),
    )


# A handful of scripted match sequences used by the replay-determinism
# tests below. Sequence 3 deliberately lists a multiway match's entries
# out of a "natural" order to exercise the "out-of-order identities in a
# single match" half of the acceptance criterion.
SEQUENCES: list[list[MatchOutcome]] = [
    [
        _outcome("m1", (HUMAN, 10), (AGENT, 3)),
        _outcome("m2", (AGENT, 8), (HUMAN, 8)),
        _outcome("m3", (HUMAN, 1), (AGENT, 9)),
    ],
    [
        _outcome("m1", (AGENT, 1), (HUMAN, 1)),
        _outcome("m2", (AGENT, 5), (HUMAN, 2)),
    ],
    [
        _outcome("m1", (THIRD, 1), (AGENT, 9), (HUMAN, 5)),
        _outcome("m2", (HUMAN, 4), (THIRD, 4), (AGENT, 4)),
        _outcome("m3", (AGENT, 2), (HUMAN, 9), (THIRD, 1)),
    ],
]


def _replay(sequence: list[MatchOutcome]) -> InMemoryRatingLedgerStore:
    store = InMemoryRatingLedgerStore()
    system = IntegerEloRatingSystem(k_factor=32)
    for outcome in sequence:
        store.record_match(outcome, system)
    return store


def _full_ledger_snapshot(store: RatingLedgerStore) -> dict[RatingIdentity, IdentityRating]:
    return {identity: store.get(identity) for identity in store.all_identities()}


# --- abstract interface -------------------------------------------------


def test_rating_ledger_store_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        RatingLedgerStore()  # type: ignore[abstract]


# --- basic recording behavior ---------------------------------------------


def test_get_unknown_identity_returns_initial_rating_with_empty_history() -> None:
    store = InMemoryRatingLedgerStore()
    standing = store.get(HUMAN)
    assert standing == IdentityRating.initial(HUMAN)
    assert standing.rating == INITIAL_RATING
    assert standing.match_count == 0
    assert standing.history == ()


def test_record_match_updates_rating_match_count_and_history() -> None:
    store = InMemoryRatingLedgerStore()
    system = IntegerEloRatingSystem(k_factor=32)
    outcome = _outcome("m1", (HUMAN, 10), (AGENT, 3))

    applied = store.record_match(outcome, system)

    human_standing = store.get(HUMAN)
    assert human_standing.match_count == 1
    assert human_standing.rating == INITIAL_RATING + applied[HUMAN].delta
    assert human_standing.history == (applied[HUMAN],)
    assert applied[HUMAN].match_id == "m1"
    assert applied[HUMAN].resulting_rating == human_standing.rating


def test_record_match_is_append_only_across_multiple_matches() -> None:
    store = InMemoryRatingLedgerStore()
    system = IntegerEloRatingSystem(k_factor=32)

    first = store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), system)
    second = store.record_match(_outcome("m2", (HUMAN, 2), (AGENT, 9)), system)

    standing = store.get(HUMAN)
    assert standing.match_count == 2
    assert standing.history == (first[HUMAN], second[HUMAN])
    # each entry's resulting_rating chains from the previous one
    assert first[HUMAN].resulting_rating + second[HUMAN].delta == second[HUMAN].resulting_rating
    assert standing.rating == second[HUMAN].resulting_rating
    # history is a new tuple each time -- past entries are never mutated
    assert first[HUMAN].delta == standing.history[0].delta


def test_all_identities_reflects_recorded_participants_in_first_seen_order() -> None:
    store = InMemoryRatingLedgerStore()
    system = IntegerEloRatingSystem(k_factor=32)
    assert store.all_identities() == []

    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), system)
    assert store.all_identities() == [HUMAN, AGENT]

    store.record_match(_outcome("m2", (THIRD, 1), (AGENT, 1)), system)
    assert store.all_identities() == [HUMAN, AGENT, THIRD]


def test_a_previously_unseen_identity_starts_from_initial_rating_mid_sequence() -> None:
    store = InMemoryRatingLedgerStore()
    system = IntegerEloRatingSystem(k_factor=32)
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), system)

    applied = store.record_match(_outcome("m2", (THIRD, 5), (AGENT, 5)), system)

    assert store.get(THIRD).match_count == 1
    assert applied[THIRD].resulting_rating == INITIAL_RATING + applied[THIRD].delta


# --- replay determinism (core acceptance criterion) ------------------------


@pytest.mark.parametrize("sequence", SEQUENCES, ids=["two-player", "short", "multiway"])
def test_replaying_the_same_sequence_yields_byte_identical_ledgers(
    sequence: list[MatchOutcome],
) -> None:
    first_run = _full_ledger_snapshot(_replay(sequence))
    second_run = _full_ledger_snapshot(_replay(sequence))
    third_run = _full_ledger_snapshot(_replay(sequence))

    assert first_run == second_run == third_run


def test_replay_is_identical_when_a_multiway_match_lists_entries_out_of_order() -> None:
    """Same multiway sequence, but each match's entries are independently
    shuffled on each replay -- the ledger must not care."""
    canonical = SEQUENCES[2]
    rng = random.Random(99)

    def _shuffled_copy(sequence: list[MatchOutcome]) -> list[MatchOutcome]:
        shuffled = []
        for outcome in sequence:
            entries = list(outcome.entries)
            rng.shuffle(entries)
            shuffled.append(MatchOutcome(match_id=outcome.match_id, entries=tuple(entries)))
        return shuffled

    baseline = _full_ledger_snapshot(_replay(canonical))
    for _ in range(5):
        reshuffled = _shuffled_copy(canonical)
        result = _full_ledger_snapshot(_replay(reshuffled))
        assert result == baseline


def test_different_sequences_produce_different_ledgers() -> None:
    """Sanity check that the replay tests above aren't vacuously true because
    every sequence collapses to the same ledger regardless of content."""
    snapshots = [_full_ledger_snapshot(_replay(sequence)) for sequence in SEQUENCES]
    assert snapshots[0] != snapshots[1]


# --- integer-only invariant (core acceptance criterion) ----------------------


def _assert_no_floats(value: object, path: str = "$") -> None:
    if isinstance(value, float):
        raise AssertionError(f"found a float at {path}: {value!r}")
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        for f in dataclasses.fields(value):
            _assert_no_floats(getattr(value, f.name), f"{path}.{f.name}")
    elif isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _assert_no_floats(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _assert_no_floats(item, f"{path}[{key!r}]")


def test_no_float_appears_anywhere_in_stored_ledger_state() -> None:
    for sequence in SEQUENCES:
        store = _replay(sequence)
        for identity in store.all_identities():
            standing = store.get(identity)
            _assert_no_floats(standing)
            assert isinstance(standing.rating, int)
            assert not isinstance(standing.rating, bool)
            for entry in standing.history:
                assert isinstance(entry, RatingEntry)
                assert isinstance(entry.delta, int)
                assert isinstance(entry.resulting_rating, int)
                assert not isinstance(entry.delta, bool)
                assert not isinstance(entry.resulting_rating, bool)
