"""Tests for the benchmark-grade participant/turn/result schema."""

from __future__ import annotations

import dataclasses

import pytest

from league_site.matches import AgentIdentity, MatchResult, Participant, ParticipantKind, TurnRecord


def test_human_participant_has_no_agent_identity() -> None:
    human = Participant(display_name="Ada", kind=ParticipantKind.HUMAN)
    assert human.kind is ParticipantKind.HUMAN
    assert human.agent_identity is None
    assert human.participant_id  # auto-generated, non-empty


def test_agent_participant_requires_model_and_provider_identity() -> None:
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
    )
    assert agent.kind is ParticipantKind.AGENT
    assert agent.agent_identity is not None
    assert agent.agent_identity.model == "claude-sonnet-5"
    assert agent.agent_identity.provider == "anthropic"


def test_agent_participant_without_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="AgentIdentity"):
        Participant(display_name="Sonnet", kind=ParticipantKind.AGENT)


def test_human_participant_with_agent_identity_is_rejected() -> None:
    with pytest.raises(ValueError, match="AgentIdentity"):
        Participant(
            display_name="Ada",
            kind=ParticipantKind.HUMAN,
            agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        )


def test_participant_ids_are_unique_by_default() -> None:
    a = Participant(display_name="A", kind=ParticipantKind.HUMAN)
    b = Participant(display_name="B", kind=ParticipantKind.HUMAN)
    assert a.participant_id != b.participant_id


def test_participant_is_immutable() -> None:
    human = Participant(display_name="Ada", kind=ParticipantKind.HUMAN)
    with pytest.raises(dataclasses.FrozenInstanceError):
        human.display_name = "Grace"  # type: ignore[misc]


def test_turn_record_carries_participant_and_action() -> None:
    turn = TurnRecord(turn_number=1, participant_id="p-1", action={"delta": 3})
    assert turn.turn_number == 1
    assert turn.participant_id == "p-1"
    assert turn.action == {"delta": 3}
    assert turn.timestamp is not None


def test_match_result_defaults_are_incomplete() -> None:
    result = MatchResult()
    assert result.completed is False
    assert result.winner_participant_id is None
    assert result.scores == {}


def test_match_result_carries_scores_by_participant_id() -> None:
    result = MatchResult(completed=True, winner_participant_id="p-agent", scores={"p-agent": 10.0})
    assert result.completed is True
    assert result.winner_participant_id == "p-agent"
    assert result.scores == {"p-agent": 10.0}
