"""Tests for :mod:`league_site.play.board` — legal orders + plan → board overlay.

Pure unit tests: :func:`~league_site.play.board.build_overlay` turns the
play surface's current :class:`~league_site.play.actions.OrderChoice` set
plus the URL-carried staged plan into the
:class:`~league_site.viewer.board.BoardOverlay` the shared board renderer
understands — unit-selection hrefs, per-cell *staging* links (each an
idempotent GET carrying the grown plan), staged-unit re-stage hrefs, and
ghosted plan marks. Nothing here submits anything: the one commit control
is the play panel's End-turn form.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

from league_site.play.actions import OrderChoice, unit_orders
from league_site.play.board import build_overlay, play_view_href
from league_site.viewer.board import build_board_model

HUMAN = "human:github:42"
BASE_PATH = "/play/matches/m-1"


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


def _model_and_orders(state: dict[str, Any]) -> tuple[Any, tuple[OrderChoice, ...]]:
    model = build_board_model(state)
    assert model is not None
    return model, unit_orders(state, HUMAN)


def _order_for(orders: tuple[OrderChoice, ...], unit_id: str, verb: str) -> OrderChoice:
    return next(o for o in orders if o.unit_id == unit_id and o.verb == verb)


def _staged_values(href: str) -> list[str]:
    return parse_qs(urlparse(href).query).get("staged", [])


def test_every_unplanned_actionable_unit_gets_a_selection_href() -> None:
    model, orders = _model_and_orders(_grid_state())
    overlay = build_overlay(model, orders, staged=(), selected_unit=None, base_path=BASE_PATH)
    assert overlay is not None
    assert set(overlay.select_hrefs) == {"solo-u1", "solo-u2"}
    assert overlay.select_hrefs["solo-u1"] == f"{BASE_PATH}?unit=solo-u1#board"
    assert overlay.selected_unit is None
    assert overlay.controls == ()
    assert overlay.staged_units == {}
    assert overlay.staged_marks == ()


def test_selection_hrefs_url_encode_the_unit_id() -> None:
    state = _grid_state()
    state["units"][0]["id"] = "solo-u 1/&x"
    state["legal_actions"] = {"solo-u 1/&x": {"move": [[1, 2]], "hold": True}}
    model, orders = _model_and_orders(state)
    overlay = build_overlay(model, orders, staged=(), selected_unit=None, base_path=BASE_PATH)
    assert overlay is not None
    assert overlay.select_hrefs["solo-u 1/&x"] == f"{BASE_PATH}?unit=solo-u+1%2F%26x#board"


def test_selected_unit_yields_one_staging_link_per_legal_order() -> None:
    model, orders = _model_and_orders(_grid_state())
    overlay = build_overlay(model, orders, staged=(), selected_unit="solo-u1", base_path=BASE_PATH)
    assert overlay is not None
    assert overlay.selected_unit == "solo-u1"
    by_verb = {(control.verb, control.x, control.y) for control in overlay.controls}
    # moves land on their target cells; hold lands on the unit's own cell
    assert by_verb == {("move", 1, 2), ("move", 2, 1), ("hold", 1, 1)}
    # every control's href carries exactly the staged order it plans
    order_values = {order.value for order in orders if order.unit_id == "solo-u1"}
    for control in overlay.controls:
        (staged_value,) = _staged_values(control.href)
        assert staged_value in order_values


def test_staging_links_grow_the_existing_plan_and_preselect_the_next_unit() -> None:
    model, orders = _model_and_orders(_grid_state())
    planned = _order_for(orders, "solo-u2", "gather")
    overlay = build_overlay(
        model, orders, staged=(planned,), selected_unit="solo-u1", base_path=BASE_PATH
    )
    assert overlay is not None
    control = overlay.controls[0]
    values = _staged_values(control.href)
    # the already-planned order rides along, the new one is appended
    assert values[0] == planned.value
    assert len(values) == 2
    # no unit left unplanned after this tap -> nothing preselected
    assert "unit=" not in control.href

    # with a third plannable unit still free, it gets preselected
    overlay_first_tap = build_overlay(
        model, orders, staged=(), selected_unit="solo-u1", base_path=BASE_PATH
    )
    assert overlay_first_tap is not None
    next_units = {
        parse_qs(urlparse(c.href).query).get("unit", [None])[0] for c in overlay_first_tap.controls
    }
    assert next_units == {"solo-u2"}


def test_staged_units_get_restage_hrefs_and_marks_not_selection() -> None:
    model, orders = _model_and_orders(_grid_state())
    planned_move = _order_for(orders, "solo-u1", "move")
    overlay = build_overlay(
        model, orders, staged=(planned_move,), selected_unit=None, base_path=BASE_PATH
    )
    assert overlay is not None
    assert set(overlay.select_hrefs) == {"solo-u2"}
    assert set(overlay.staged_units) == {"solo-u1"}
    # the re-stage href drops the unit's own order and re-selects it
    restage = overlay.staged_units["solo-u1"]
    assert _staged_values(restage) == []
    assert "unit=solo-u1" in restage
    # the planned move ghosts onto its destination cell
    (mark,) = overlay.staged_marks
    assert (mark.x, mark.y, mark.verb, mark.unit_id) == (
        planned_move.target[0],
        planned_move.target[1],
        "move",
        "solo-u1",
    )


def test_in_place_orders_mark_the_units_own_cell() -> None:
    model, orders = _model_and_orders(_grid_state())
    planned_gather = _order_for(orders, "solo-u2", "gather")
    overlay = build_overlay(
        model, orders, staged=(planned_gather,), selected_unit=None, base_path=BASE_PATH
    )
    assert overlay is not None
    (mark,) = overlay.staged_marks
    assert (mark.x, mark.y, mark.verb) == (2, 3, "gather")


def test_own_cell_verbs_share_the_units_cell() -> None:
    model, orders = _model_and_orders(_grid_state())
    overlay = build_overlay(model, orders, staged=(), selected_unit="solo-u2", base_path=BASE_PATH)
    assert overlay is not None
    own_cell = [(c.verb, c.x, c.y) for c in overlay.controls if (c.x, c.y) == (2, 3)]
    assert ("gather", 2, 3) in own_cell and ("hold", 2, 3) in own_cell


def test_stale_or_unknown_selection_degrades_to_no_selection() -> None:
    model, orders = _model_and_orders(_grid_state())
    for stale in ("house-u1", "gone-unit", ""):
        overlay = build_overlay(model, orders, staged=(), selected_unit=stale, base_path=BASE_PATH)
        assert overlay is not None, stale
        assert overlay.selected_unit is None, stale
        assert overlay.controls == (), stale


def test_a_staged_unit_cannot_also_be_selected() -> None:
    model, orders = _model_and_orders(_grid_state())
    planned = _order_for(orders, "solo-u1", "move")
    overlay = build_overlay(
        model, orders, staged=(planned,), selected_unit="solo-u1", base_path=BASE_PATH
    )
    assert overlay is not None
    assert overlay.selected_unit is None
    assert "solo-u1" in overlay.staged_units


def test_no_orders_yield_no_overlay() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    assert build_overlay(model, (), staged=(), selected_unit=None, base_path=BASE_PATH) is None


def test_orders_for_units_absent_from_the_board_are_skipped() -> None:
    state = _grid_state()
    # legal actions name a unit the board doesn't carry (e.g. it just died)
    state["legal_actions"]["solo-ghost"] = {"move": [[0, 0]], "hold": True}
    model, orders = _model_and_orders(state)
    overlay = build_overlay(model, orders, staged=(), selected_unit=None, base_path=BASE_PATH)
    assert overlay is not None
    assert "solo-ghost" not in overlay.select_hrefs


def test_play_view_href_is_the_single_url_builder() -> None:
    state = _grid_state()
    orders = unit_orders(state, HUMAN)
    planned = _order_for(orders, "solo-u2", "gather")
    href = play_view_href(BASE_PATH, (planned,), unit="solo-u1")
    parsed = urlparse(href)
    assert parsed.path == BASE_PATH
    assert parsed.fragment == "board"
    query = parse_qs(parsed.query)
    assert query["staged"] == [planned.value]
    assert query["unit"] == ["solo-u1"]
    # and without anything staged, the bare path still anchors to the board
    assert play_view_href(BASE_PATH, ()) == f"{BASE_PATH}#board"
