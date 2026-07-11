"""Unit tests for :func:`league_site.game.events.diff_turn_events` — the
platform-derived "what happened last turn" feed (2026-07-11 feedback round:
live players couldn't tell when a post flipped or a resource was gathered).

Pure-function tests here; the adapter wiring (events computed on every
``apply_turn``, empty on ``initial_state``) is covered in
``tests/test_game_adapter_fake.py``.
"""

from __future__ import annotations

from typing import Any

from league_site.game.events import diff_turn_events


def _state(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "control_points": [{"id": "cp-west", "pos": [3, 8], "owner": None, "hold": []}],
        "resource_nodes": [{"id": "rn-west", "pos": [0, 5], "remaining": 12}],
        "missions": [
            {"id": "ms-supply", "kind": "deliver", "reward": 10, "completed_by": []},
        ],
        "units": [
            {"id": "solo-u2", "team_id": "solo", "role": "harvester", "carrying": 0, "alive": True},
            {"id": "house-u1", "team_id": "house", "role": "scout", "carrying": 0, "alive": True},
        ],
        "team_resources": {"solo": 0, "house": 0},
    }
    base.update(overrides)
    return base


def test_no_change_yields_no_events() -> None:
    assert diff_turn_events(_state(), _state()) == []


def test_post_captured_when_owner_flips_from_none_to_a_team() -> None:
    after = _state(
        control_points=[{"id": "cp-west", "pos": [3, 8], "owner": "solo", "hold": [["solo", 2]]}]
    )
    assert diff_turn_events(_state(), after) == [
        {"kind": "post_captured", "post_id": "cp-west", "team": "solo"}
    ]


def test_post_captured_when_owner_flips_between_teams() -> None:
    before = _state(control_points=[{"id": "cp-west", "owner": "solo", "pos": [3, 8]}])
    after = _state(control_points=[{"id": "cp-west", "owner": "house", "pos": [3, 8]}])
    assert diff_turn_events(before, after) == [
        {"kind": "post_captured", "post_id": "cp-west", "team": "house"}
    ]


def test_post_lost_when_owner_reverts_to_none() -> None:
    before = _state(control_points=[{"id": "cp-west", "owner": "solo", "pos": [3, 8]}])
    after = _state(control_points=[{"id": "cp-west", "owner": None, "pos": [3, 8]}])
    assert diff_turn_events(before, after) == [
        {"kind": "post_lost", "post_id": "cp-west", "team": "solo"}
    ]


def test_gathered_reports_the_carrying_delta_per_unit() -> None:
    after = _state(
        units=[
            {"id": "solo-u2", "team_id": "solo", "role": "harvester", "carrying": 3, "alive": True},
            {"id": "house-u1", "team_id": "house", "role": "scout", "carrying": 0, "alive": True},
        ]
    )
    assert diff_turn_events(_state(), after) == [
        {"kind": "gathered", "unit_id": "solo-u2", "team": "solo", "amount": 3}
    ]


def test_a_carrying_drop_alone_is_not_an_event() -> None:
    """Unloading shows up as the team's `delivered` event, never as a
    negative gather."""
    before = _state(
        units=[
            {"id": "solo-u2", "team_id": "solo", "role": "harvester", "carrying": 3, "alive": True},
        ]
    )
    after = _state(
        units=[
            {"id": "solo-u2", "team_id": "solo", "role": "harvester", "carrying": 0, "alive": True},
        ]
    )
    assert diff_turn_events(before, after) == []


def test_delivered_reports_the_banked_delta_per_team() -> None:
    after = _state(team_resources={"solo": 3, "house": 0})
    assert diff_turn_events(_state(), after) == [{"kind": "delivered", "team": "solo", "amount": 3}]


def test_node_exhausted_only_on_the_turn_it_empties() -> None:
    drained = _state(resource_nodes=[{"id": "rn-west", "pos": [0, 5], "remaining": 0}])
    assert diff_turn_events(_state(), drained) == [{"kind": "node_exhausted", "node_id": "rn-west"}]
    # already empty before -> no repeat event
    assert diff_turn_events(drained, drained) == []


def test_unit_fell_when_a_unit_dies_or_disappears() -> None:
    dead = _state(
        units=[
            {
                "id": "solo-u2",
                "team_id": "solo",
                "role": "harvester",
                "carrying": 0,
                "alive": False,
            },
        ]
    )
    events = diff_turn_events(_state(), dead)
    assert {"kind": "unit_fell", "unit_id": "solo-u2", "team": "solo"} in events
    assert {"kind": "unit_fell", "unit_id": "house-u1", "team": "house"} in events
    assert len(events) == 2


def test_a_unit_dead_before_the_turn_never_falls_again() -> None:
    dead = _state(
        units=[
            {
                "id": "solo-u2",
                "team_id": "solo",
                "role": "harvester",
                "carrying": 0,
                "alive": False,
            },
        ]
    )
    assert diff_turn_events(dead, dead) == []


def test_mission_completed_names_the_team_and_reward() -> None:
    after = _state(
        missions=[{"id": "ms-supply", "kind": "deliver", "reward": 10, "completed_by": ["solo"]}]
    )
    assert diff_turn_events(_state(), after) == [
        {"kind": "mission_completed", "mission_id": "ms-supply", "team": "solo", "reward": 10}
    ]


def test_missing_or_malformed_projections_yield_no_events() -> None:
    # A pre-board persisted state has none of the projections at all.
    assert diff_turn_events({}, {}) == []
    assert diff_turn_events({}, _state()) == []
    hostile = {
        "control_points": "not-a-list",
        "units": [{"no-id": True}, "junk"],
        "missions": {"also": "wrong"},
        "resource_nodes": None,
        "team_resources": [1, 2, 3],
    }
    assert diff_turn_events(hostile, _state()) == []


def test_one_turn_can_carry_several_event_kinds_at_once() -> None:
    after = _state(
        control_points=[{"id": "cp-west", "pos": [3, 8], "owner": "house", "hold": []}],
        units=[
            {"id": "solo-u2", "team_id": "solo", "role": "harvester", "carrying": 1, "alive": True},
        ],
        team_resources={"solo": 0, "house": 2},
    )
    events = diff_turn_events(_state(), after)
    kinds = [event["kind"] for event in events]
    assert sorted(kinds) == ["delivered", "gathered", "post_captured", "unit_fell"]
