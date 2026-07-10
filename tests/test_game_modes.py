"""Unit tests for :mod:`league_site.game.modes` — launch modes as data.

Covers the mode registry, generic participant->team assignment, and the
platform-side mode-fairness enforcement (:func:`enforce_action_cap`) that
backs the h14 honesty condition: excess solo-mode actions are refused
*before* anything is ever handed to ``league match act`` — proven here at
the pure-data level; ``tests/test_game_adapter_fake.py`` proves the same
thing at the adapter/subprocess-call boundary with a spy runner.
"""

from __future__ import annotations

import pytest

from league_site.game.modes import (
    COOP_2,
    SOLO_VS_BOT,
    TEAM_VS_TEAM,
    LaunchMode,
    Rejection,
    TeamSpec,
    assign_participants,
    enforce_action_cap,
    get_mode,
    mode_names,
    registry,
)
from league_site.matches.models import AgentIdentity, Participant, ParticipantKind


def _agent(pid: str, name: str = "P") -> Participant:
    return Participant(
        display_name=name,
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id=pid,
    )


# -- registry -----------------------------------------------------------------


def test_registry_contains_exactly_the_three_launch_modes() -> None:
    assert mode_names() == ("solo-vs-bot", "team-vs-team", "coop-2")
    assert registry() == (SOLO_VS_BOT, TEAM_VS_TEAM, COOP_2)


def test_get_mode_returns_the_named_mode() -> None:
    assert get_mode("solo-vs-bot") is SOLO_VS_BOT
    assert get_mode("coop-2") is COOP_2


def test_get_mode_raises_on_an_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown launch mode"):
        get_mode("solo-vs-everyone")


def test_solo_vs_bot_has_a_one_action_cap_and_a_bot_house_side() -> None:
    assert SOLO_VS_BOT.team("solo").action_cap == 1
    assert SOLO_VS_BOT.team("house").is_bot is True
    assert SOLO_VS_BOT.bot_team_ids == ("house",)
    assert SOLO_VS_BOT.team_ids == ("solo", "house")


def test_solo_vs_bot_house_side_is_driven_by_the_game_greedy_bot_policy() -> None:
    """platform#9: the house team declares the game's own bot policy that
    drives it every turn — and the solo handicap stays on the player side
    only, exactly like the game's own solo preset (solo capped at 1, house
    uncapped)."""
    assert SOLO_VS_BOT.team("house").bot_policy == "bot:greedy"
    assert SOLO_VS_BOT.team("house").action_cap is None
    assert SOLO_VS_BOT.team("solo").bot_policy is None
    assert SOLO_VS_BOT.team("solo").action_cap == 1


def test_bot_policy_defaults_to_none_on_a_team_spec() -> None:
    assert TeamSpec(team_id="t", driver_kind="stateless").bot_policy is None


def test_team_vs_team_has_no_bot_side_and_no_action_cap() -> None:
    assert TEAM_VS_TEAM.bot_team_ids == ()
    assert all(t.action_cap is None for t in TEAM_VS_TEAM.teams)
    assert all(t.bot_policy is None for t in TEAM_VS_TEAM.teams)


def test_coop_2_is_exactly_one_team() -> None:
    assert COOP_2.team_ids == ("coop",)
    assert COOP_2.bot_team_ids == ()


def test_mode_team_raises_keyerror_for_an_unknown_team() -> None:
    with pytest.raises(KeyError):
        SOLO_VS_BOT.team("nonexistent")


def test_with_overrides_replaces_only_the_given_fields() -> None:
    overridden = SOLO_VS_BOT.with_overrides(scenario_id="skirmish-2", seed=42)
    assert overridden.scenario_id == "skirmish-2"
    assert overridden.seed == 42
    assert overridden.name == SOLO_VS_BOT.name
    assert overridden.teams == SOLO_VS_BOT.teams
    # the original is untouched (frozen dataclass, dataclasses.replace copies)
    assert SOLO_VS_BOT.scenario_id != "skirmish-2"


def test_with_overrides_is_a_no_op_when_nothing_is_given() -> None:
    assert SOLO_VS_BOT.with_overrides() is SOLO_VS_BOT


# -- assign_participants --------------------------------------------------


def test_solo_vs_bot_assigns_its_one_participant_to_the_solo_team() -> None:
    p = _agent("p-1")
    assert assign_participants(SOLO_VS_BOT, [p]) == {"p-1": "solo"}


def test_team_vs_team_assigns_participants_1_to_1_in_order() -> None:
    p1, p2 = _agent("p-1"), _agent("p-2")
    assert assign_participants(TEAM_VS_TEAM, [p1, p2]) == {"p-1": "blue", "p-2": "red"}
    # order matters: swap the input order, get the swapped assignment
    assert assign_participants(TEAM_VS_TEAM, [p2, p1]) == {"p-2": "blue", "p-1": "red"}


def test_coop_2_assigns_both_participants_to_the_shared_team() -> None:
    p1, p2 = _agent("p-1"), _agent("p-2")
    assert assign_participants(COOP_2, [p1, p2]) == {"p-1": "coop", "p-2": "coop"}


def test_assign_participants_rejects_the_wrong_participant_count() -> None:
    with pytest.raises(ValueError, match="needs exactly 1"):
        assign_participants(SOLO_VS_BOT, [_agent("p-1"), _agent("p-2")])
    with pytest.raises(ValueError, match="needs exactly 2"):
        assign_participants(TEAM_VS_TEAM, [_agent("p-1")])


def test_assign_participants_rejects_a_malformed_multi_team_mode_mismatch() -> None:
    """Defensive branch: a hand-built (non-bundled) mode whose
    ``expected_participants`` disagrees with its own non-bot team count.
    None of the three bundled modes can hit this (their counts always
    agree), but a caller building a custom :class:`LaunchMode` should still
    get a clear error rather than a silently wrong 1:1 zip."""
    malformed = LaunchMode(
        name="malformed",
        game_mode="competitive",
        scenario_id="skirmish-1",
        seed=1,
        expected_participants=3,
        teams=(
            TeamSpec(team_id="a", driver_kind="stateless"),
            TeamSpec(team_id="b", driver_kind="stateless"),
        ),
    )
    with pytest.raises(ValueError, match="one participant per non-bot team"):
        assign_participants(malformed, [_agent("p-1"), _agent("p-2"), _agent("p-3")])


def test_assign_participants_rejects_a_mode_with_no_non_bot_team() -> None:
    all_bot_mode = LaunchMode(
        name="all-bot",
        game_mode="competitive",
        scenario_id="skirmish-1",
        seed=1,
        expected_participants=0,
        teams=(TeamSpec(team_id="a", driver_kind="bot", is_bot=True),),
    )
    with pytest.raises(ValueError, match="no non-bot team"):
        assign_participants(all_bot_mode, [])


# -- enforce_action_cap -----------------------------------------------------


def test_enforce_action_cap_passes_through_under_the_cap() -> None:
    orders = {"actions": [{"unit_id": "solo-u1", "action": "hold"}]}
    trimmed, rejections = enforce_action_cap(SOLO_VS_BOT, "solo", orders)
    assert trimmed == {"actions": [{"unit_id": "solo-u1", "action": "hold"}]}
    assert rejections == []


def test_enforce_action_cap_trims_excess_and_reports_rejections() -> None:
    orders = {
        "actions": [
            {"unit_id": "solo-u1", "action": "hold"},
            {"unit_id": "solo-u2", "action": "hold"},
            {"unit_id": "solo-u3", "action": "move", "to": [1, 1]},
        ]
    }
    trimmed, rejections = enforce_action_cap(SOLO_VS_BOT, "solo", orders)
    assert trimmed["actions"] == [{"unit_id": "solo-u1", "action": "hold"}]
    assert [r.unit_id for r in rejections] == ["solo-u2", "solo-u3"]
    assert all(isinstance(r, Rejection) and r.team_id == "solo" for r in rejections)
    assert all("allows at most 1 action" in r.reason for r in rejections)


def test_enforce_action_cap_rejection_to_dict_shape() -> None:
    rejection = Rejection(team_id="solo", unit_id="solo-u2", reason="capped")
    assert rejection.to_dict() == {"team_id": "solo", "unit_id": "solo-u2", "reason": "capped"}


def test_enforce_action_cap_never_trims_an_unlimited_team() -> None:
    many_actions = {"actions": [{"unit_id": f"blue-u{i}", "action": "hold"} for i in range(10)]}
    trimmed, rejections = enforce_action_cap(TEAM_VS_TEAM, "blue", many_actions)
    assert len(trimmed["actions"]) == 10
    assert rejections == []


def test_enforce_action_cap_handles_orders_with_no_actions_key() -> None:
    trimmed, rejections = enforce_action_cap(SOLO_VS_BOT, "solo", {})
    assert trimmed == {"actions": []}
    assert rejections == []


def test_enforce_action_cap_does_not_mutate_the_input_orders() -> None:
    orders = {"actions": [{"unit_id": "solo-u1"}, {"unit_id": "solo-u2"}]}
    enforce_action_cap(SOLO_VS_BOT, "solo", orders)
    assert len(orders["actions"]) == 2  # untouched
