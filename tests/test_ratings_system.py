"""Tests for ``league_site.ratings.system``: the integer-only Elo engine.

Covers the acceptance criteria "ratings use integer arithmetic only" (no
float appears in any delta) and half of "replaying the same sequence of
match results yields byte-identical ... leaderboards" — the other half
(store-level replay across several sequences) lives in
``test_ratings_ledger.py``.
"""

from __future__ import annotations

import random

import pytest

from league_site.matches import (
    AgentIdentity,
    Match,
    MatchResult,
    MatchStatus,
    Participant,
    ParticipantKind,
)
from league_site.ratings import (
    INITIAL_RATING,
    IntegerEloRatingSystem,
    MatchOutcome,
    OutcomeEntry,
    RatingIdentity,
    RatingSystem,
    outcome_from_match,
)
from tests._matches_support import CounterGameEngine, make_participants

HUMAN = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Ada")
AGENT = RatingIdentity(
    kind=ParticipantKind.AGENT,
    display_name="Sonnet",
    model="claude-sonnet-5",
    provider="anthropic",
)
AGENT_OTHER_MODEL = RatingIdentity(
    kind=ParticipantKind.AGENT,
    display_name="Sonnet",
    model="claude-opus-4",
    provider="anthropic",
)


def _outcome(match_id: str, *pairs: tuple[RatingIdentity, int]) -> MatchOutcome:
    return MatchOutcome(
        match_id=match_id,
        entries=tuple(OutcomeEntry(identity=identity, score=score) for identity, score in pairs),
    )


# --- RatingIdentity ----------------------------------------------------------


def test_rating_identity_rejects_agent_without_model_or_provider() -> None:
    with pytest.raises(ValueError):
        RatingIdentity(kind=ParticipantKind.AGENT, display_name="x")


def test_rating_identity_rejects_human_with_model_or_provider() -> None:
    with pytest.raises(ValueError):
        RatingIdentity(kind=ParticipantKind.HUMAN, display_name="x", model="m")


def test_rating_identity_from_participant_human() -> None:
    human, _ = make_participants()
    identity = RatingIdentity.from_participant(human)
    assert identity == RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Ada")


def test_rating_identity_from_participant_agent_carries_model_and_provider() -> None:
    _, agent = make_participants()
    identity = RatingIdentity.from_participant(agent)
    assert identity.kind is ParticipantKind.AGENT
    assert identity.model == "claude-sonnet-5"
    assert identity.provider == "anthropic"


def test_rating_identity_same_display_name_different_model_are_distinct_identities() -> None:
    assert AGENT != AGENT_OTHER_MODEL
    assert AGENT.sort_key() != AGENT_OTHER_MODEL.sort_key()


def test_rating_identity_sort_key_is_total_order_over_distinct_identities() -> None:
    identities = [HUMAN, AGENT, AGENT_OTHER_MODEL]
    keys = {identity.sort_key() for identity in identities}
    assert len(keys) == len(identities)


# --- RatingSystem interface ---------------------------------------------------


def test_rating_system_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        RatingSystem()  # type: ignore[abstract]


def test_integer_elo_rejects_non_positive_k_factor() -> None:
    with pytest.raises(ValueError):
        IntegerEloRatingSystem(k_factor=0)


# --- IntegerEloRatingSystem: basic shape -------------------------------------


def test_two_player_equal_rating_win_and_loss_are_exact_opposites() -> None:
    system = IntegerEloRatingSystem(k_factor=32)
    outcome = _outcome("m1", (HUMAN, 10), (AGENT, 3))
    deltas = system.compute_deltas({HUMAN: INITIAL_RATING, AGENT: INITIAL_RATING}, outcome)

    assert deltas[HUMAN] == 16
    assert deltas[AGENT] == -16
    assert deltas[HUMAN] + deltas[AGENT] == 0


def test_two_player_draw_at_equal_rating_yields_zero_deltas() -> None:
    system = IntegerEloRatingSystem(k_factor=32)
    outcome = _outcome("m1", (HUMAN, 5), (AGENT, 5))
    deltas = system.compute_deltas({HUMAN: INITIAL_RATING, AGENT: INITIAL_RATING}, outcome)

    assert deltas == {HUMAN: 0, AGENT: 0}


def test_higher_rated_winner_gains_less_than_a_lower_rated_winner_would() -> None:
    system = IntegerEloRatingSystem(k_factor=32)
    favorite_wins = system.compute_deltas(
        {HUMAN: 1800, AGENT: 1200}, _outcome("m1", (HUMAN, 10), (AGENT, 0))
    )
    underdog_wins = system.compute_deltas(
        {HUMAN: 1200, AGENT: 1800}, _outcome("m1", (HUMAN, 10), (AGENT, 0))
    )

    assert 0 < favorite_wins[HUMAN] < underdog_wins[HUMAN]


def test_missing_identity_defaults_to_initial_rating() -> None:
    system = IntegerEloRatingSystem(k_factor=32)
    outcome = _outcome("m1", (HUMAN, 10), (AGENT, 3))

    with_explicit_initial = system.compute_deltas(
        {HUMAN: INITIAL_RATING, AGENT: INITIAL_RATING}, outcome
    )
    with_absent_entries = system.compute_deltas({}, outcome)

    assert with_explicit_initial == with_absent_entries


def test_rejects_outcome_with_fewer_than_two_participants() -> None:
    system = IntegerEloRatingSystem()
    with pytest.raises(ValueError):
        system.compute_deltas({}, _outcome("m1", (HUMAN, 10)))


def test_k_factor_scales_delta_magnitude() -> None:
    outcome = _outcome("m1", (HUMAN, 10), (AGENT, 3))
    small_k = IntegerEloRatingSystem(k_factor=16).compute_deltas(
        {HUMAN: INITIAL_RATING, AGENT: INITIAL_RATING}, outcome
    )
    large_k = IntegerEloRatingSystem(k_factor=64).compute_deltas(
        {HUMAN: INITIAL_RATING, AGENT: INITIAL_RATING}, outcome
    )
    assert large_k[HUMAN] > small_k[HUMAN] > 0


# --- determinism ---------------------------------------------------------


def test_compute_deltas_is_deterministic_across_repeated_calls() -> None:
    system = IntegerEloRatingSystem(k_factor=24)
    ratings = {HUMAN: 1450, AGENT: 1610}
    outcome = _outcome("m1", (HUMAN, 7), (AGENT, 7))

    results = [system.compute_deltas(ratings, outcome) for _ in range(20)]
    assert all(result == results[0] for result in results)


def test_compute_deltas_is_independent_of_outcome_entry_order() -> None:
    system = IntegerEloRatingSystem(k_factor=24)
    third = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Zed")
    ratings = {HUMAN: 1500, AGENT: 1550, third: 1400}
    pairs = [(HUMAN, 8), (AGENT, 3), (third, 12)]

    baseline = system.compute_deltas(ratings, _outcome("m1", *pairs))
    rng = random.Random(1234)
    for _ in range(10):
        shuffled = list(pairs)
        rng.shuffle(shuffled)
        shuffled_result = system.compute_deltas(ratings, _outcome("m1", *shuffled))
        assert shuffled_result == baseline


def test_compute_deltas_is_independent_of_current_ratings_mapping_iteration_order() -> None:
    system = IntegerEloRatingSystem(k_factor=24)
    outcome = _outcome("m1", (HUMAN, 8), (AGENT, 3))

    forward = system.compute_deltas({HUMAN: 1500, AGENT: 1550}, outcome)
    backward = system.compute_deltas({AGENT: 1550, HUMAN: 1500}, outcome)
    assert forward == backward


# --- integer-only invariant ---------------------------------------------------


def test_all_deltas_are_plain_ints_never_floats() -> None:
    system = IntegerEloRatingSystem(k_factor=32)
    third = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Zed")
    scenarios = [
        _outcome("m1", (HUMAN, 10), (AGENT, 3)),
        _outcome("m2", (HUMAN, 5), (AGENT, 5)),
        _outcome("m3", (HUMAN, 1), (AGENT, 9), (third, 5)),
    ]
    ratings = {HUMAN: 1487, AGENT: 1523, third: 1399}

    for outcome in scenarios:
        deltas = system.compute_deltas(ratings, outcome)
        for identity, delta in deltas.items():
            assert isinstance(delta, int), f"{identity} delta {delta!r} is not an int"
            assert not isinstance(delta, bool)
            assert not isinstance(delta, float)


def test_multiway_match_deltas_are_all_ints_and_roughly_zero_sum() -> None:
    system = IntegerEloRatingSystem(k_factor=32)
    third = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Zed")
    ratings = {HUMAN: 1500, AGENT: 1500, third: 1500}
    outcome = _outcome("m1", (HUMAN, 10), (AGENT, 5), (third, 1))

    deltas = system.compute_deltas(ratings, outcome)
    assert all(isinstance(delta, int) for delta in deltas.values())
    # Per-identity rounding can drift the total by at most a couple of
    # points (see _round_half_away_from_zero's docstring); it must never
    # be wildly unbalanced.
    assert abs(sum(deltas.values())) <= 3


# --- outcome_from_match adapter ------------------------------------------


def _completed_match() -> Match:
    human, agent = make_participants()
    engine = CounterGameEngine(target=6, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id="m-adapter")
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 2})
    match.take_turn(engine, agent.participant_id, {"delta": 4})
    match.complete(engine)
    return match


def test_outcome_from_match_maps_participants_and_int_scores() -> None:
    """CounterGameEngine only scores the participant whose turn reached the
    target (see its ``score()``), so only that participant should appear."""
    match = _completed_match()
    outcome = outcome_from_match(match)

    assert outcome.match_id == "m-adapter"
    by_identity = {entry.identity: entry.score for entry in outcome.entries}
    agent_identity = RatingIdentity(
        kind=ParticipantKind.AGENT,
        display_name="Sonnet",
        model="claude-sonnet-5",
        provider="anthropic",
    )
    assert by_identity[agent_identity] == 6
    for score in by_identity.values():
        assert isinstance(score, int)
        assert not isinstance(score, float)


def test_outcome_from_match_maps_every_participant_when_every_participant_is_scored() -> None:
    human = Participant(display_name="Ada", kind=ParticipantKind.HUMAN, participant_id="p-human")
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-agent",
    )
    match = Match.create(game_id="dual-score-demo", participants=[human, agent], match_id="m-dual")
    match.status = MatchStatus.COMPLETED
    match.result = MatchResult(completed=True, scores={"p-human": 4.0, "p-agent": 9.0})

    outcome = outcome_from_match(match)
    by_display_name = {entry.identity.display_name: entry.score for entry in outcome.entries}
    assert by_display_name == {"Ada": 4, "Sonnet": 9}


def test_outcome_from_match_requires_completed_status() -> None:
    human, agent = make_participants()
    engine = CounterGameEngine(target=6)
    match = Match.create(game_id=engine.game_id, participants=[human, agent])
    match.start(engine)

    with pytest.raises(ValueError):
        outcome_from_match(match)


def test_outcome_from_match_requires_a_result() -> None:
    human, agent = make_participants()
    match = Match.create(game_id="counter-demo", participants=[human, agent])
    match.status = MatchStatus.COMPLETED  # force the shape without a result

    with pytest.raises(ValueError):
        outcome_from_match(match)


def test_outcome_from_match_skips_participants_with_no_recorded_score() -> None:
    human = Participant(display_name="Ada", kind=ParticipantKind.HUMAN, participant_id="p-human")
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-agent",
    )
    match = Match.create(game_id="counter-demo", participants=[human, agent], match_id="m-partial")
    match.status = MatchStatus.COMPLETED
    match.result = MatchResult(completed=True, scores={"p-human": 1.0})

    outcome = outcome_from_match(match)
    assert [entry.identity.display_name for entry in outcome.entries] == ["Ada"]


def test_outcome_from_match_with_no_scores_at_all_yields_an_empty_outcome() -> None:
    human = Participant(display_name="Ada", kind=ParticipantKind.HUMAN, participant_id="p-human")
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-agent",
    )
    match = Match.create(game_id="counter-demo", participants=[human, agent], match_id="m-empty")
    match.status = MatchStatus.COMPLETED
    match.result = MatchResult(completed=True, scores={})

    outcome = outcome_from_match(match)
    assert outcome.entries == ()
