"""Tests for :mod:`league_site.play.actions` — turn interpretation for the play surface.

Pure unit tests over plain state dicts: the two documented ``legal_actions``
shapes (grid-style mapping, sequence-of-mappings), the "whose turn is it"
heuristics, and the server-side membership check that a submitted form value
must pass before it is ever handed to an engine.
"""

from __future__ import annotations

import json
from typing import Any

from league_site.play.actions import ActionChoice, action_choices, is_waiting, match_choice

HUMAN = "human:github:42"
OTHER = "human:github:99"


def _grid_state(**overrides: Any) -> dict[str, Any]:
    """A fake ``GridLaneEngine``-shaped state (no subprocess anywhere)."""
    state: dict[str, Any] = {
        "game_id": "league-of-agents-grid",
        "mode": "solo-vs-bot",
        "participant_teams": {HUMAN: "solo"},
        "staged_teams": [],
        "legal_actions": {
            "solo-u1": {
                "move": [[1, 2], [0, 1]],
                "gather": True,
                "deliver": False,
                "hold": True,
                "can_gather": True,
                "can_capture": False,
            },
            "house-u1": {"move": [[5, 5]], "hold": True},
        },
    }
    state.update(overrides)
    return state


# --- sequence-shaped legal_actions -------------------------------------------


def test_sequence_of_mappings_becomes_verbatim_choices() -> None:
    state = {"legal_actions": [{"points": 1}, {"points": 2}]}
    choices = action_choices(state, HUMAN)
    assert [choice.action for choice in choices] == [{"points": 1}, {"points": 2}]


def test_sequence_choice_values_round_trip_as_json() -> None:
    state = {"legal_actions": [{"points": 3}]}
    (choice,) = action_choices(state, HUMAN)
    assert json.loads(choice.value) == {"points": 3}


def test_scalar_legal_action_entries_are_not_submittable_choices() -> None:
    """The built-in stub publishes bare point values ([1, 2, 3]) — legal
    *parameters*, not whole actions its ``apply_turn`` would accept — so the
    play surface must not render them as submittable choices."""
    state = {"legal_actions": [1, 2, 3]}
    assert action_choices(state, HUMAN) == ()


def test_missing_or_non_dict_state_yields_no_choices() -> None:
    assert action_choices(None, HUMAN) == ()
    assert action_choices({"legal_actions": None}, HUMAN) == ()
    assert action_choices({}, HUMAN) == ()
    assert action_choices({"legal_actions": "nope"}, HUMAN) == ()


# --- grid-shaped legal_actions -----------------------------------------------


def test_grid_choices_cover_only_the_participants_own_units() -> None:
    choices = action_choices(_grid_state(), HUMAN)
    labels = [choice.label for choice in choices]
    assert labels, "expected choices for the participant's team"
    assert all("solo-u1" in label for label in labels)
    assert not any("house-u1" in label for label in labels)


def test_grid_choices_wrap_each_order_in_the_actions_envelope() -> None:
    choices = action_choices(_grid_state(), HUMAN)
    move_choice = next(c for c in choices if c.action["actions"][0]["action"] == "move")
    assert move_choice.action == {
        "actions": [{"unit_id": "solo-u1", "action": "move", "to": [0, 1]}]
    }


def test_grid_choices_include_gather_and_hold_but_not_capability_flags() -> None:
    verbs = {
        choice.action["actions"][0]["action"] for choice in action_choices(_grid_state(), HUMAN)
    }
    assert verbs == {"move", "gather", "hold"}


def test_grid_choices_for_a_non_participant_are_empty() -> None:
    assert action_choices(_grid_state(), OTHER) == ()


# --- whose turn is it ----------------------------------------------------------


def test_turn_order_state_reports_waiting_for_the_out_of_turn_participant() -> None:
    state = {"participant_order": [HUMAN, OTHER], "turn_index": 0}
    assert is_waiting(state, HUMAN) is False
    assert is_waiting(state, OTHER) is True


def test_turn_order_wraps_around() -> None:
    state = {"participant_order": [HUMAN, OTHER], "turn_index": 3}
    assert is_waiting(state, OTHER) is False
    assert is_waiting(state, HUMAN) is True


def test_grid_state_reports_waiting_once_the_participants_team_has_staged() -> None:
    assert is_waiting(_grid_state(staged_teams=["solo"]), HUMAN) is True
    assert is_waiting(_grid_state(staged_teams=[]), HUMAN) is False


def test_unknown_state_shapes_never_report_waiting() -> None:
    assert is_waiting(None, HUMAN) is False
    assert is_waiting({}, HUMAN) is False
    assert is_waiting({"participant_order": []}, HUMAN) is False


# --- the server-side membership check -----------------------------------------


def test_match_choice_accepts_a_value_generated_from_the_choices() -> None:
    choices = (ActionChoice(label="one", action={"points": 1}),)
    assert match_choice(choices, choices[0].value) is choices[0]


def test_match_choice_is_key_order_insensitive() -> None:
    choices = (
        ActionChoice(
            label="m", action={"actions": [{"unit_id": "u1", "action": "move", "to": [1, 2]}]}
        ),
    )
    submitted = json.dumps({"actions": [{"to": [1, 2], "action": "move", "unit_id": "u1"}]})
    assert match_choice(choices, submitted) is choices[0]


def test_match_choice_rejects_actions_outside_the_current_legal_set() -> None:
    choices = (ActionChoice(label="one", action={"points": 1}),)
    assert match_choice(choices, json.dumps({"points": 99})) is None


def test_match_choice_rejects_malformed_and_missing_values() -> None:
    choices = (ActionChoice(label="one", action={"points": 1}),)
    assert match_choice(choices, "not json") is None
    assert match_choice(choices, None) is None
    assert match_choice((), json.dumps({"points": 1})) is None
