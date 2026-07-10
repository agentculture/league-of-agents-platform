"""Tests for the GameEngine interface: turn exchanges only, no realtime concepts.

These are the tests behind the acceptance criterion "the game engine
interface exposes turn exchanges only — no tick or frame loop in any
signature."
"""

from __future__ import annotations

import inspect

import pytest

from league_site.matches import GameEngine
from tests._matches_support import CounterGameEngine, make_participants

EXPECTED_PUBLIC_SURFACE = {"game_id", "initial_state", "apply_turn", "is_over", "score"}

# Substrings that would signal a tick/frame/realtime-loop concept leaking
# into the interface — the platform models games as discrete turn
# exchanges only (see c6/h18 in the converged devague frame).
FORBIDDEN_SUBSTRINGS = ("tick", "frame", "loop", "realtime", "real_time", "fps", "render")


def test_game_engine_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        GameEngine()  # type: ignore[abstract]


def test_game_engine_public_surface_is_exactly_turn_exchange_methods() -> None:
    public_members = {name for name in vars(GameEngine) if not name.startswith("_")}
    assert public_members == EXPECTED_PUBLIC_SURFACE


def test_game_engine_public_surface_has_no_realtime_loop_concepts() -> None:
    public_members = [name for name in vars(GameEngine) if not name.startswith("_")]
    assert public_members, "expected GameEngine to expose at least one public member"
    for name in public_members:
        lowered = name.lower()
        for forbidden in FORBIDDEN_SUBSTRINGS:
            assert forbidden not in lowered, f"{name!r} looks like a realtime/loop concept"


def test_game_engine_signatures_have_no_realtime_parameters() -> None:
    forbidden_params = {"dt", "delta_time", "elapsed", "elapsed_time", "tick", "frame"}
    for name in EXPECTED_PUBLIC_SURFACE:
        member = getattr(GameEngine, name)
        raw = inspect.unwrap(member.fget) if isinstance(member, property) else member
        try:
            signature = inspect.signature(raw)
        except (TypeError, ValueError):
            continue
        param_names = {p.lower() for p in signature.parameters}
        overlap = param_names & forbidden_params
        assert not overlap, f"{name} signature has realtime-loop parameters: {overlap}"


def test_game_engine_is_abstract_and_requires_all_methods() -> None:
    class Incomplete(GameEngine):
        @property
        def game_id(self) -> str:
            return "incomplete"

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_counter_engine_conforms_to_the_turn_exchange_contract() -> None:
    engine = CounterGameEngine(target=5)
    human, agent = make_participants()
    state = engine.initial_state([human, agent])
    assert engine.is_over(state) is False

    state = engine.apply_turn(state, human.participant_id, {"delta": 2})
    state = engine.apply_turn(state, agent.participant_id, {"delta": 4})

    assert state["total"] == 6
    assert engine.is_over(state) is True
    assert engine.score(state) == {agent.participant_id: 6.0}
