"""Tests for MatchStore / InMemoryMatchStore.

Covers the acceptance criterion "a mid-game match state save->load
round-trips to identical state" via the actual persistence interface
(as opposed to the lower-level serialization functions exercised in
test_matches_serialization.py).
"""

from __future__ import annotations

import pytest

from league_site.matches import (
    InMemoryMatchStore,
    Match,
    MatchNotFoundError,
    MatchStatus,
    MatchStore,
)
from tests._matches_support import CounterGameEngine, make_participants


def _mid_game_match(match_id: str = "m-store") -> tuple[Match, CounterGameEngine]:
    human, agent = make_participants()
    engine = CounterGameEngine(target=100, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 3})
    return match, engine


def test_matchstore_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        MatchStore()  # type: ignore[abstract]


def test_save_then_load_round_trips_mid_game_match_to_identical_state() -> None:
    """The acceptance criterion, exercised end-to-end through MatchStore."""
    store = InMemoryMatchStore()
    match, _ = _mid_game_match()
    assert match.status is MatchStatus.ACTIVE

    store.save(match)
    loaded = store.load(match.match_id)

    assert loaded == match
    assert loaded.status is MatchStatus.ACTIVE
    assert loaded.game_state == match.game_state
    assert loaded.turns == match.turns
    assert loaded.participants == match.participants


def test_save_load_round_trips_paused_match() -> None:
    store = InMemoryMatchStore()
    match, _ = _mid_game_match()
    match.pause()

    store.save(match)
    loaded = store.load(match.match_id)

    assert loaded == match
    assert loaded.status is MatchStatus.PAUSED


def test_load_missing_match_raises_match_not_found() -> None:
    store = InMemoryMatchStore()
    with pytest.raises(MatchNotFoundError):
        store.load("does-not-exist")


def test_delete_missing_match_raises_match_not_found() -> None:
    store = InMemoryMatchStore()
    with pytest.raises(MatchNotFoundError):
        store.delete("does-not-exist")


def test_delete_removes_match() -> None:
    store = InMemoryMatchStore()
    match, _ = _mid_game_match()
    store.save(match)

    store.delete(match.match_id)

    with pytest.raises(MatchNotFoundError):
        store.load(match.match_id)


def test_list_ids_reflects_saved_and_deleted_matches() -> None:
    store = InMemoryMatchStore()
    first, _ = _mid_game_match("m-1")
    second, _ = _mid_game_match("m-2")

    store.save(first)
    store.save(second)
    assert set(store.list_ids()) == {"m-1", "m-2"}

    store.delete("m-1")
    assert store.list_ids() == ["m-2"]


def test_save_isolates_stored_state_from_later_mutation_of_the_live_match() -> None:
    """Saving copies state in; mutating the caller's Match afterwards must not
    leak into the persisted record (a real DynamoDB write would have the
    same isolation)."""
    store = InMemoryMatchStore()
    match, engine = _mid_game_match()
    store.save(match)

    match.take_turn(engine, match.participants[1].participant_id, {"delta": 1})

    reloaded = store.load(match.match_id)
    assert len(reloaded.turns) == 1
    assert len(match.turns) == 2


def test_load_returns_a_copy_not_aliased_across_calls() -> None:
    store = InMemoryMatchStore()
    match, engine = _mid_game_match()
    store.save(match)

    first_load = store.load(match.match_id)
    first_load.take_turn(engine, first_load.participants[1].participant_id, {"delta": 9})

    second_load = store.load(match.match_id)
    assert len(second_load.turns) == 1  # unaffected by mutating first_load
