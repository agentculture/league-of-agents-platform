"""Tests for the Match state machine transitions and the benchmark schema it carries."""

from __future__ import annotations

import pytest

from league_site.matches import InvalidTransitionError, Match, MatchStatus
from tests._matches_support import CounterGameEngine, FixedScoreEngine, make_participants


def _new_match(*, target: int = 100) -> tuple[Match, CounterGameEngine]:
    human, agent = make_participants()
    engine = CounterGameEngine(target=target, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent])
    return match, engine


# --- created ----------------------------------------------------------------


def test_new_match_starts_in_created_status_with_empty_history() -> None:
    match, _ = _new_match()
    assert match.status is MatchStatus.CREATED
    assert match.game_state is None
    assert match.turns == []
    assert match.result is None
    assert match.match_id  # auto-generated


def test_create_accepts_an_explicit_match_id() -> None:
    human, agent = make_participants()
    match = Match.create(game_id="counter-demo", participants=[human, agent], match_id="m-1")
    assert match.match_id == "m-1"


# --- start --------------------------------------------------------------


def test_start_moves_created_to_active_and_sets_initial_state() -> None:
    match, engine = _new_match()
    match.start(engine)
    assert match.status is MatchStatus.ACTIVE
    assert match.game_state == {"total": 0, "turns_taken": 0, "last_participant_id": None}


def test_start_twice_raises_invalid_transition() -> None:
    match, engine = _new_match()
    match.start(engine)
    with pytest.raises(InvalidTransitionError):
        match.start(engine)


# --- take_turn ------------------------------------------------------------


def test_take_turn_requires_active_status() -> None:
    match, engine = _new_match()
    with pytest.raises(InvalidTransitionError):
        match.take_turn(engine, match.participants[0].participant_id, {"delta": 1})


def test_take_turn_updates_game_state_and_appends_history() -> None:
    match, engine = _new_match()
    match.start(engine)
    human = match.participants[0]

    match.take_turn(engine, human.participant_id, {"delta": 3})

    assert match.status is MatchStatus.ACTIVE
    assert match.game_state["total"] == 3
    assert len(match.turns) == 1
    assert match.turns[0].turn_number == 1
    assert match.turns[0].participant_id == human.participant_id
    assert match.turns[0].action == {"delta": 3}


def test_take_turn_does_not_auto_complete_even_if_engine_reports_over() -> None:
    match, engine = _new_match(target=5)
    match.start(engine)
    human = match.participants[0]

    match.take_turn(engine, human.participant_id, {"delta": 10})

    assert engine.is_over(match.game_state) is True
    assert match.status is MatchStatus.ACTIVE  # complete() must be called explicitly
    assert match.result is None


# --- pause / resume ---------------------------------------------------------


def test_pause_from_active_then_resume_back_to_active() -> None:
    match, engine = _new_match()
    match.start(engine)

    match.pause()
    assert match.status is MatchStatus.PAUSED

    match.resume()
    assert match.status is MatchStatus.ACTIVE


def test_pause_from_created_raises_invalid_transition() -> None:
    match, _ = _new_match()
    with pytest.raises(InvalidTransitionError):
        match.pause()


def test_resume_from_active_raises_invalid_transition() -> None:
    match, engine = _new_match()
    match.start(engine)
    with pytest.raises(InvalidTransitionError):
        match.resume()


def test_take_turn_while_paused_raises_invalid_transition() -> None:
    match, engine = _new_match()
    match.start(engine)
    match.pause()
    with pytest.raises(InvalidTransitionError):
        match.take_turn(engine, match.participants[0].participant_id, {"delta": 1})


# --- complete ---------------------------------------------------------------


def test_complete_from_active_sets_result_and_status() -> None:
    match, engine = _new_match(target=5)
    match.start(engine)
    human, agent = match.participants
    match.take_turn(engine, human.participant_id, {"delta": 2})
    match.take_turn(engine, agent.participant_id, {"delta": 4})

    match.complete(engine)

    assert match.status is MatchStatus.COMPLETED
    assert match.result is not None
    assert match.result.completed is True
    assert match.result.winner_participant_id == agent.participant_id
    assert match.result.scores == {agent.participant_id: 6.0}


def test_complete_with_equal_scores_yields_no_winner_a_draw() -> None:
    """A 0.0-0.0 (or any tied) finish must record a draw, not crown whichever
    participant happened to sort first out of ``max(scores, key=scores.get)``
    -- see ``league_site.ratings.system.IntegerEloRatingSystem``, which
    already treats an equal-score outcome as a draw (500/500 millipoints);
    the match record must agree with the rating layer, not contradict it."""
    human, agent = make_participants()
    engine = FixedScoreEngine({human.participant_id: 0.0, agent.participant_id: 0.0})
    match = Match.create(game_id=engine.game_id, participants=[human, agent])
    match.start(engine)

    match.complete(engine)

    assert match.result is not None
    assert match.result.winner_participant_id is None
    assert match.result.scores == {human.participant_id: 0.0, agent.participant_id: 0.0}


def test_complete_with_unequal_scores_still_picks_the_higher_scorer() -> None:
    human, agent = make_participants()
    engine = FixedScoreEngine({human.participant_id: 3.0, agent.participant_id: 7.0})
    match = Match.create(game_id=engine.game_id, participants=[human, agent])
    match.start(engine)

    match.complete(engine)

    assert match.result is not None
    assert match.result.winner_participant_id == agent.participant_id


def test_complete_from_created_raises_invalid_transition() -> None:
    match, engine = _new_match()
    with pytest.raises(InvalidTransitionError):
        match.complete(engine)


def test_complete_from_paused_raises_invalid_transition() -> None:
    match, engine = _new_match()
    match.start(engine)
    match.pause()
    with pytest.raises(InvalidTransitionError):
        match.complete(engine)


def test_invalid_transition_error_carries_action_and_status() -> None:
    match, engine = _new_match()
    with pytest.raises(InvalidTransitionError) as exc_info:
        match.complete(engine)
    err = exc_info.value
    assert err.action == "complete"
    assert err.current_status == "created"
    assert "complete" in str(err)
    assert "created" in str(err)


def test_full_lifecycle_created_active_paused_active_completed() -> None:
    match, engine = _new_match(target=3)
    human, agent = match.participants

    assert match.status is MatchStatus.CREATED
    match.start(engine)
    assert match.status is MatchStatus.ACTIVE

    match.take_turn(engine, human.participant_id, {"delta": 1})
    match.pause()
    assert match.status is MatchStatus.PAUSED

    match.resume()
    assert match.status is MatchStatus.ACTIVE

    match.take_turn(engine, agent.participant_id, {"delta": 5})
    match.complete(engine)
    assert match.status is MatchStatus.COMPLETED
    assert len(match.turns) == 2


# --- benchmark schema --------------------------------------------------------


def test_match_schema_carries_game_id_participants_and_agent_identity() -> None:
    match, engine = _new_match(target=5)
    match.start(engine)
    human, agent = match.participants

    assert match.game_id == "counter-demo"

    human_record = next(p for p in match.participants if p.participant_id == human.participant_id)
    agent_record = next(p for p in match.participants if p.participant_id == agent.participant_id)

    assert human_record.kind.value == "human"
    assert human_record.agent_identity is None

    assert agent_record.kind.value == "agent"
    assert agent_record.agent_identity is not None
    assert agent_record.agent_identity.model == "claude-sonnet-5"
    assert agent_record.agent_identity.provider == "anthropic"


def test_match_schema_carries_result_after_completion() -> None:
    match, engine = _new_match(target=1)
    match.start(engine)
    human = match.participants[0]
    match.take_turn(engine, human.participant_id, {"delta": 1})
    match.complete(engine)

    assert match.result is not None
    assert match.result.completed is True
    assert human.participant_id in match.result.scores
