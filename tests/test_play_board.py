"""Tests for :mod:`league_site.play.board` — legal actions → board overlay.

Pure unit tests: :func:`~league_site.play.board.build_overlay` turns the
play surface's current :class:`~league_site.play.actions.ActionChoice` set
into the :class:`~league_site.viewer.board.BoardOverlay` the shared board
renderer understands — unit-selection hrefs, and (once a unit is selected)
one per-cell control per legal action, each carrying the exact submittable
form value the turn handler re-validates server-side.
"""

from __future__ import annotations

from typing import Any

from league_site.play.actions import ActionChoice, action_choices
from league_site.play.board import build_overlay
from league_site.viewer.board import build_board_model

HUMAN = "human:github:42"
BASE_PATH = "/play/matches/m-1"
FORM_ACTION = "/play/matches/m-1/turns"


def _grid_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "participant_teams": {HUMAN: "solo"},
        "grid_width": 6,
        "grid_height": 5,
        "units": [
            {"id": "solo-u1", "team_id": "solo", "role": "scout", "pos": [1, 1], "alive": True},
            {
                "id": "solo-u2",
                "team_id": "solo",
                "role": "harvester",
                "pos": [2, 3],
                "alive": True,
            },
            {"id": "house-u1", "team_id": "house", "role": "scout", "pos": [5, 4], "alive": True},
        ],
        "control_points": [],
        "resource_nodes": [],
        "missions": [],
        "legal_actions": {
            "solo-u1": {"move": [[1, 2], [2, 1]], "gather": False, "hold": True},
            "solo-u2": {"move": [[2, 2]], "gather": True, "hold": True},
            "house-u1": {"move": [[5, 3]], "hold": True},
        },
    }
    state.update(overrides)
    return state


def _model_and_choices(state: dict[str, Any]) -> tuple[Any, tuple[ActionChoice, ...]]:
    model = build_board_model(state)
    assert model is not None
    return model, action_choices(state, HUMAN)


def test_every_actionable_unit_gets_a_selection_href() -> None:
    model, choices = _model_and_choices(_grid_state())
    overlay = build_overlay(
        model, choices, selected_unit=None, base_path=BASE_PATH, form_action=FORM_ACTION
    )
    assert overlay is not None
    assert set(overlay.select_hrefs) == {"solo-u1", "solo-u2"}
    assert overlay.select_hrefs["solo-u1"] == f"{BASE_PATH}?unit=solo-u1"
    assert overlay.selected_unit is None
    assert overlay.controls == ()
    assert overlay.form_action == FORM_ACTION


def test_selection_hrefs_url_encode_the_unit_id() -> None:
    state = _grid_state()
    state["units"][0]["id"] = "solo-u 1/&x"
    state["legal_actions"] = {"solo-u 1/&x": {"move": [[1, 2]], "hold": True}}
    model, choices = _model_and_choices(state)
    overlay = build_overlay(
        model, choices, selected_unit=None, base_path=BASE_PATH, form_action=FORM_ACTION
    )
    assert overlay is not None
    assert overlay.select_hrefs["solo-u 1/&x"] == f"{BASE_PATH}?unit=solo-u%201%2F%26x"


def test_selected_unit_yields_one_control_per_legal_action() -> None:
    model, choices = _model_and_choices(_grid_state())
    overlay = build_overlay(
        model, choices, selected_unit="solo-u1", base_path=BASE_PATH, form_action=FORM_ACTION
    )
    assert overlay is not None
    assert overlay.selected_unit == "solo-u1"
    by_verb = {(control.verb, control.x, control.y) for control in overlay.controls}
    # moves land on their target cells; hold lands on the unit's own cell
    assert by_verb == {("move", 1, 2), ("move", 2, 1), ("hold", 1, 1)}
    # every control's value is the exact submittable ActionChoice value
    choice_values = {choice.value for choice in choices}
    assert all(control.value in choice_values for control in overlay.controls)


def test_own_cell_verbs_share_the_units_cell() -> None:
    model, choices = _model_and_choices(_grid_state())
    overlay = build_overlay(
        model, choices, selected_unit="solo-u2", base_path=BASE_PATH, form_action=FORM_ACTION
    )
    assert overlay is not None
    own_cell = [(c.verb, c.x, c.y) for c in overlay.controls if (c.x, c.y) == (2, 3)]
    assert ("gather", 2, 3) in own_cell and ("hold", 2, 3) in own_cell


def test_stale_or_unknown_selection_degrades_to_no_selection() -> None:
    model, choices = _model_and_choices(_grid_state())
    for stale in ("house-u1", "gone-unit", ""):
        overlay = build_overlay(
            model, choices, selected_unit=stale, base_path=BASE_PATH, form_action=FORM_ACTION
        )
        assert overlay is not None, stale
        assert overlay.selected_unit is None, stale
        assert overlay.controls == (), stale


def test_non_grid_choices_yield_no_overlay() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    choices = (
        ActionChoice(label="one", action={"points": 1}),
        ActionChoice(label="two", action={"points": 2}),
    )
    assert (
        build_overlay(
            model, choices, selected_unit=None, base_path=BASE_PATH, form_action=FORM_ACTION
        )
        is None
    )


def test_no_choices_yield_no_overlay() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    assert (
        build_overlay(model, (), selected_unit=None, base_path=BASE_PATH, form_action=FORM_ACTION)
        is None
    )


def test_choices_for_units_absent_from_the_board_are_skipped() -> None:
    state = _grid_state()
    # legal actions name a unit the board doesn't carry (e.g. it just died)
    state["legal_actions"]["solo-ghost"] = {"move": [[0, 0]], "hold": True}
    model, choices = _model_and_choices(state)
    overlay = build_overlay(
        model, choices, selected_unit=None, base_path=BASE_PATH, form_action=FORM_ACTION
    )
    assert overlay is not None
    assert "solo-ghost" not in overlay.select_hrefs
