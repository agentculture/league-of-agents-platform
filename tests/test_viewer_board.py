"""Tests for :mod:`league_site.viewer.board` — the shared match-board rendering.

The board model/renderer is shared between the public spectate viewer and
the play surface: :func:`~league_site.viewer.board.build_board_model` turns
a ``GridLaneEngine``-shaped game state into a render-ready model,
:func:`~league_site.viewer.board.render_board` renders it as HTML — static
(no links, no forms — the spectate contract) unless the caller passes the
play surface's :class:`~league_site.viewer.board.BoardOverlay`, which adds
the plan-then-ack interaction: unit-selection links, per-cell *staging*
links (idempotent GETs — never forms; the one POST is the play panel's
End-turn form), staged-unit re-stage links, and ghosted plan marks.
"""

from __future__ import annotations

from typing import Any

from league_site.viewer.board import (
    BoardOverlay,
    CellControl,
    StagedMark,
    build_board_model,
    render_board,
)


def _grid_state(**overrides: Any) -> dict[str, Any]:
    state: dict[str, Any] = {
        "grid_width": 6,
        "grid_height": 5,
        "units": [
            {
                "id": "solo-u1",
                "team_id": "solo",
                "role": "scout",
                "pos": [1, 1],
                "carrying": 0,
                "alive": True,
            },
            {
                "id": "solo-u2",
                "team_id": "solo",
                "role": "harvester",
                "pos": [2, 3],
                "carrying": 2,
                "alive": True,
            },
            {
                "id": "house-u1",
                "team_id": "house",
                "role": "defender",
                "pos": [5, 4],
                "carrying": 0,
                "alive": True,
            },
        ],
        "control_points": [{"id": "cp-mid", "pos": [3, 2], "owner": None, "hold": []}],
        "resource_nodes": [{"id": "rn-a", "pos": [2, 3], "remaining": 5}],
        "missions": [{"id": "ms-x", "kind": "deliver", "pos": [4, 0], "status": "open"}],
    }
    state.update(overrides)
    return state


# --- the model -----------------------------------------------------------------


def test_grid_shaped_state_builds_a_board_model() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    assert (model.width, model.height) == (6, 5)
    assert [unit.unit_id for unit in model.units] == ["solo-u1", "solo-u2", "house-u1"]
    scout = model.units[0]
    assert (scout.team, scout.role, scout.x, scout.y) == ("solo", "scout", 1, 1)
    assert model.units[1].carrying == 2
    assert [post.marker_id for post in model.posts] == ["cp-mid"]
    assert [res.marker_id for res in model.resources] == ["rn-a"]
    assert [mission.marker_id for mission in model.missions] == ["ms-x"]


def test_non_grid_states_yield_no_board_model() -> None:
    assert build_board_model(None) is None
    assert build_board_model({}) is None
    assert build_board_model({"scores": {"p": 0}}) is None  # the stub family
    assert build_board_model({"grid_width": 6, "grid_height": 5}) is None  # no units
    assert build_board_model(_grid_state(grid_width="wide")) is None


def test_dead_and_out_of_bounds_units_are_left_off_the_board() -> None:
    state = _grid_state()
    state["units"][0]["alive"] = False
    state["units"][1]["pos"] = [99, 99]
    model = build_board_model(state)
    assert model is not None
    assert [unit.unit_id for unit in model.units] == ["house-u1"]


def test_closed_missions_and_exhausted_resources_are_left_off_the_board() -> None:
    state = _grid_state()
    state["missions"][0]["status"] = "completed"
    state["resource_nodes"][0]["remaining"] = 0
    model = build_board_model(state)
    assert model is not None
    assert model.missions == ()
    assert model.resources == ()


# --- static rendering (the spectate contract) -----------------------------------


def test_static_board_renders_cells_by_grid_area_with_no_links_or_forms() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    html_out = render_board(model)
    assert "--bw:6" in html_out and "--bh:5" in html_out
    # pos [x, y] -> grid-area row/col (1-based): scout at (1,1) -> 2/2
    assert "grid-area:2/2" in html_out
    assert "grid-area:4/3" in html_out  # harvester at (2, 3)
    assert "<a " not in html_out
    assert "<form" not in html_out
    assert "<button" not in html_out


def test_static_board_marks_teams_roles_and_markers() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    html_out = render_board(model, accent_team="solo")
    assert html_out.count("board-team-accent") == 2  # both solo units
    assert html_out.count("board-team-rival") == 1  # the house unit
    for role in ("scout", "harvester", "defender"):
        assert f"board-role-{role}" in html_out
    assert "board-post" in html_out
    assert "board-res" in html_out
    assert "board-mission" in html_out


def test_carrying_units_expose_their_load_as_a_count() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    html_out = render_board(model, accent_team="solo")
    # solo-u2 carries 2; the badge carries the number for the CSS counter
    assert 'data-carry="2"' in html_out


def test_accent_team_defaults_to_the_first_units_team() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    assert render_board(model) == render_board(model, accent_team="solo")


def test_post_ownership_is_rendered_relative_to_the_accent_team() -> None:
    owned = _grid_state()
    owned["control_points"][0]["owner"] = "solo"
    model = build_board_model(owned)
    assert model is not None
    assert 'data-owner="accent"' in render_board(model, accent_team="solo")
    assert 'data-owner="rival"' in render_board(model, accent_team="house")
    neutral = build_board_model(_grid_state())
    assert neutral is not None
    assert 'data-owner="none"' in render_board(neutral, accent_team="solo")


def test_a_capture_in_progress_shows_its_streak_counter_on_the_post() -> None:
    """2026-07-11 feedback: standing on a post gave no sign anything was
    happening. With the scenario's threshold known, the post announces the
    streak — visibly and in its accessible name."""
    contested = _grid_state(scenario_rules={"capture_hold_turns": 2})
    contested["control_points"][0]["hold"] = [["solo", 1]]
    model = build_board_model(contested)
    assert model is not None
    assert model.capture_hold_turns == 2
    html_out = render_board(model, accent_team="solo")
    assert "board-post-progress" in html_out
    assert ">1/2<" in html_out
    assert "solo capturing, 1/2" in html_out


def test_a_completed_capture_shows_ownership_not_a_counter() -> None:
    owned = _grid_state(scenario_rules={"capture_hold_turns": 2})
    owned["control_points"][0]["owner"] = "solo"
    owned["control_points"][0]["hold"] = [["solo", 3]]
    model = build_board_model(owned)
    assert model is not None
    html_out = render_board(model, accent_team="solo")
    assert "board-post-progress" not in html_out
    assert "held by solo" in html_out


def test_a_state_without_scenario_rules_shows_no_streak_counter() -> None:
    contested = _grid_state()
    contested["control_points"][0]["hold"] = [["solo", 1]]
    model = build_board_model(contested)
    assert model is not None
    assert model.capture_hold_turns is None
    assert "board-post-progress" not in render_board(model, accent_team="solo")


def test_hostile_ids_and_roles_are_escaped_not_executed() -> None:
    state = _grid_state()
    state["units"][0]["id"] = "<script>alert(1)</script>"
    state["units"][0]["role"] = '"><img src=x>'
    model = build_board_model(state)
    assert model is not None
    html_out = render_board(model)
    assert "<script>" not in html_out
    assert "<img" not in html_out


# --- overlay rendering (the play surface's interaction layer) --------------------


def _overlay(**overrides: Any) -> BoardOverlay:
    kwargs: dict[str, Any] = {
        "select_hrefs": {"solo-u1": "/play/matches/m-1?unit=solo-u1#board"},
        "selected_unit": None,
        "controls": (),
    }
    kwargs.update(overrides)
    return BoardOverlay(**kwargs)


def test_overlay_renders_selectable_units_as_links() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    html_out = render_board(model, overlay=_overlay(), accent_team="solo")
    assert 'href="/play/matches/m-1?unit=solo-u1#board"' in html_out
    assert "board-unit-live" in html_out
    # only the selectable unit becomes a link; the rest stay inert
    assert html_out.count("<a ") == 1
    assert "<form" not in html_out  # the board never carries a form


def test_overlay_marks_the_selected_unit_and_renders_staging_links() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    href = "/play/matches/m-1?staged=x#board"
    overlay = _overlay(
        selected_unit="solo-u1",
        controls=(CellControl(x=1, y=2, verb="move", label="Move solo-u1 to (1, 2)", href=href),),
    )
    html_out = render_board(model, overlay=overlay, accent_team="solo")
    assert "board-unit-selected" in html_out
    # the target is a staging LINK — an idempotent GET, double-tap safe
    assert f'href="{href}"' in html_out
    assert "board-target-link" in html_out
    assert "<form" not in html_out and 'method="post"' not in html_out
    # the control lands on the target cell: (1, 2) -> grid-area 3/2
    assert "grid-area:3/2" in html_out
    assert 'aria-label="Move solo-u1 to (1, 2)"' in html_out


def test_two_verbs_on_one_cell_render_disambiguated_stacked_pills() -> None:
    """Acceptance: ambiguous targets (two verbs, one cell) stay distinct —
    one labeled staging link per verb, stacked in the cell."""
    model = build_board_model(_grid_state())
    assert model is not None
    overlay = _overlay(
        select_hrefs={"solo-u2": "/play/matches/m-1?unit=solo-u2#board"},
        selected_unit="solo-u2",
        controls=(
            CellControl(
                x=2, y=3, verb="gather", label="Gather with solo-u2 at (2, 3)", href="/p?g#board"
            ),
            CellControl(x=2, y=3, verb="hold", label="Hold solo-u2 at (2, 3)", href="/p?h#board"),
        ),
    )
    html_out = render_board(model, overlay=overlay, accent_team="solo")
    assert "board-target-stack" in html_out
    assert ">gather</a>" in html_out
    assert ">hold</a>" in html_out
    assert 'href="/p?g#board"' in html_out and 'href="/p?h#board"' in html_out
    assert html_out.count("grid-area:4/3") >= 2  # unit glyph + the stacked pills share the cell


def test_single_action_cells_render_one_full_cell_link() -> None:
    model = build_board_model(_grid_state())
    assert model is not None
    overlay = _overlay(
        selected_unit="solo-u1",
        controls=(
            CellControl(x=2, y=1, verb="move", label="Move solo-u1 to (2, 1)", href="/p?m#board"),
        ),
    )
    html_out = render_board(model, overlay=overlay, accent_team="solo")
    assert "board-target-stack" not in html_out
    assert html_out.count("board-target-link") == 1


def test_own_cell_controls_are_always_labeled_pills_never_an_anonymous_cover() -> None:
    """A control on the selected unit's own cell must say its verb: an
    unlabeled full-cell link there would cover the unit and turn a stray
    tap on your own piece into staging an unnamed action."""
    model = build_board_model(_grid_state())
    assert model is not None
    overlay = _overlay(
        selected_unit="solo-u1",
        # solo-u1 stands at (1, 1); its single own-cell action is hold
        controls=(
            CellControl(x=1, y=1, verb="hold", label="Hold solo-u1 at (1, 1)", href="/p?h#board"),
        ),
    )
    html_out = render_board(model, overlay=overlay, accent_team="solo")
    assert "board-target-self" in html_out
    assert ">hold</a>" in html_out
    assert "board-target-link" not in html_out  # no anonymous cover


def test_staged_units_render_as_restage_links_with_ghosted_marks() -> None:
    """A planned unit wears the staged treatment and links back to change
    its order; the planned order ghosts onto its target cell."""
    model = build_board_model(_grid_state())
    assert model is not None
    overlay = _overlay(
        select_hrefs={},
        staged_units={"solo-u1": "/play/matches/m-1?unit=solo-u1#board"},
        staged_marks=(StagedMark(x=1, y=2, verb="move", unit_id="solo-u1"),),
    )
    html_out = render_board(model, overlay=overlay, accent_team="solo")
    assert "board-unit-staged" in html_out
    assert 'aria-label="solo-u1 (scout) — order planned; change it"' in html_out
    assert "board-staged-mark" in html_out
    assert 'aria-label="Planned: solo-u1 move here"' in html_out
    # the mark lands on the planned target cell: (1, 2) -> grid-area 3/2
    assert "grid-area:3/2" in html_out
