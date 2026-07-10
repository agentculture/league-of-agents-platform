"""Tests for :mod:`league_site.api.engines` — the built-in stub ``GameEngine``."""

from __future__ import annotations

import pytest

from league_site.api.engines import (
    DEFAULT_ENGINE_REGISTRY,
    DEFAULT_MODE,
    LEGAL_POINTS,
    StubDuelEngine,
)
from league_site.matches import Participant, ParticipantKind


def _participant(participant_id: str) -> Participant:
    return Participant(
        display_name=participant_id, kind=ParticipantKind.HUMAN, participant_id=participant_id
    )


def test_default_engine_registry_keys_the_default_mode() -> None:
    assert DEFAULT_MODE in DEFAULT_ENGINE_REGISTRY
    engine = DEFAULT_ENGINE_REGISTRY[DEFAULT_MODE]()
    assert isinstance(engine, StubDuelEngine)
    assert engine.game_id == DEFAULT_MODE


def test_default_engine_registry_factory_returns_a_fresh_instance_each_call() -> None:
    factory = DEFAULT_ENGINE_REGISTRY[DEFAULT_MODE]
    assert factory() is not factory()


def test_initial_state_seeds_zero_scores_and_legal_actions() -> None:
    engine = StubDuelEngine(target=5, max_turns=10)
    p1, p2 = _participant("p1"), _participant("p2")
    state = engine.initial_state([p1, p2])
    assert state["scores"] == {"p1": 0, "p2": 0}
    assert state["turn_index"] == 0
    assert state["turns_taken"] == 0
    assert state["legal_actions"] == list(LEGAL_POINTS)


def test_apply_turn_alternates_and_accumulates_score() -> None:
    engine = StubDuelEngine(target=100, max_turns=100)
    state = engine.initial_state([_participant("p1"), _participant("p2")])

    state = engine.apply_turn(state, "p1", {"points": 3})
    assert state["scores"] == {"p1": 3, "p2": 0}
    assert state["turn_index"] == 1
    assert state["turns_taken"] == 1

    state = engine.apply_turn(state, "p2", {"points": 2})
    assert state["scores"] == {"p1": 3, "p2": 2}
    assert state["turn_index"] == 2


def test_apply_turn_out_of_order_raises_value_error() -> None:
    engine = StubDuelEngine()
    state = engine.initial_state([_participant("p1"), _participant("p2")])
    with pytest.raises(ValueError, match="not participant"):
        engine.apply_turn(state, "p2", {"points": 1})


def test_apply_turn_on_a_match_with_no_participants_raises_value_error() -> None:
    engine = StubDuelEngine()
    state = engine.initial_state([])
    with pytest.raises(ValueError, match="no participants"):
        engine.apply_turn(state, "anyone", {"points": 1})


@pytest.mark.parametrize(
    "action",
    [{"points": 0}, {"points": 99}, {}, None, "nope", {"points": "x"}, ["points", 1]],
)
def test_apply_turn_illegal_action_raises_value_error(action: object) -> None:
    engine = StubDuelEngine()
    state = engine.initial_state([_participant("p1")])
    with pytest.raises(ValueError):
        engine.apply_turn(state, "p1", action)


def test_apply_turn_does_not_mutate_the_input_state() -> None:
    """``Match.take_turn`` reassigns ``game_state`` from the return value; a
    real engine must not rely on (or produce) aliasing between old and new
    state, since ``InMemoryMatchStore`` round-trips through a deep copy."""
    engine = StubDuelEngine()
    before = engine.initial_state([_participant("p1")])
    after = engine.apply_turn(before, "p1", {"points": 1})
    assert before["scores"]["p1"] == 0
    assert after["scores"]["p1"] == 1


def test_solo_participant_always_plays_its_own_turn() -> None:
    engine = StubDuelEngine(target=100, max_turns=100)
    state = engine.initial_state([_participant("p1")])
    state = engine.apply_turn(state, "p1", {"points": 3})
    state = engine.apply_turn(state, "p1", {"points": 3})
    assert state["scores"]["p1"] == 6


def test_is_over_false_until_target_reached() -> None:
    engine = StubDuelEngine(target=5, max_turns=100)
    state = engine.initial_state([_participant("p1")])
    assert engine.is_over(state) is False
    state = engine.apply_turn(state, "p1", {"points": 3})
    assert engine.is_over(state) is False
    state = engine.apply_turn(state, "p1", {"points": 3})
    assert engine.is_over(state) is True


def test_is_over_true_once_max_turns_reached_even_below_target() -> None:
    engine = StubDuelEngine(target=1000, max_turns=2)
    state = engine.initial_state([_participant("p1"), _participant("p2")])
    state = engine.apply_turn(state, "p1", {"points": 1})
    assert engine.is_over(state) is False
    state = engine.apply_turn(state, "p2", {"points": 1})
    assert engine.is_over(state) is True


def test_score_returns_every_participant_not_just_the_leader() -> None:
    engine = StubDuelEngine(target=5, max_turns=10)
    state = engine.initial_state([_participant("p1"), _participant("p2")])
    state = engine.apply_turn(state, "p1", {"points": 3})
    state = engine.apply_turn(state, "p2", {"points": 1})
    assert engine.score(state) == {"p1": 3.0, "p2": 1.0}
