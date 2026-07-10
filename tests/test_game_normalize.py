"""Tests for :mod:`league_site.game.normalize` — the ``GridLaneEngine`` <-> BYOK bridge.

Covers the task's acceptance criteria: expansion correctness (a
``GridLaneEngine``-shaped ``legal_actions`` dict becomes the exact flat
BYOK pair list), determinism (sorted, order-independent-of-input-order
output), and an end-to-end fake test driving a game state through
:func:`~league_site.game.normalize.build_match_view` into
:func:`~league_site.byok.runner.run_turn` with a stubbed provider — one
legal order passes through untouched, one illegal order is dropped.
"""

from __future__ import annotations

import json

from league_site.byok.providers import TransportRequest, TransportResponse
from league_site.byok.runner import run_turn
from league_site.byok.vault import InMemoryKeyVault
from league_site.game.normalize import build_match_view, legal_actions_to_pairs

API_KEY = "sk-test-key-normalize"  # nosec B105 - test fixture


class RecordingTransport:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.requests: list[TransportRequest] = []

    def __call__(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        return self.response


def _json_response(payload: dict) -> TransportResponse:
    return TransportResponse(status=200, body=json.dumps(payload).encode("utf-8"))


# --- legal_actions_to_pairs: expansion correctness --------------------------


def test_expands_move_cells_gather_deliver_hold_per_unit() -> None:
    legal_actions = {
        "solo-u1": {
            "move": [],
            "gather": True,
            "deliver": False,
            "hold": True,
            "can_gather": True,
            "can_capture": False,
        },
        "solo-u2": {
            "move": [[2, 1], [1, 2]],
            "gather": False,
            "deliver": True,
            "hold": True,
            "can_gather": True,
            "can_capture": False,
        },
    }

    pairs = legal_actions_to_pairs(legal_actions)

    assert pairs == [
        {"unit": "solo-u1", "action": {"unit_id": "solo-u1", "action": "gather"}},
        {"unit": "solo-u1", "action": {"unit_id": "solo-u1", "action": "hold"}},
        {
            "unit": "solo-u2",
            "action": {"unit_id": "solo-u2", "action": "move", "to": [1, 2]},
        },
        {
            "unit": "solo-u2",
            "action": {"unit_id": "solo-u2", "action": "move", "to": [2, 1]},
        },
        {"unit": "solo-u2", "action": {"unit_id": "solo-u2", "action": "deliver"}},
        {"unit": "solo-u2", "action": {"unit_id": "solo-u2", "action": "hold"}},
    ]


def test_a_false_flag_never_produces_a_pair() -> None:
    legal_actions = {
        "u1": {
            "move": [],
            "gather": False,
            "deliver": False,
            "hold": False,
            "can_gather": True,
            "can_capture": True,
        }
    }

    assert legal_actions_to_pairs(legal_actions) == []


def test_can_gather_and_can_capture_capability_flags_are_never_expanded() -> None:
    """``can_gather``/``can_capture`` are unit-type capability metadata, not
    this-turn legality — only ``gather``/``deliver``/``hold``/``move`` (the
    actual per-turn legality flags) ever produce a pair."""
    legal_actions = {
        "u1": {
            "move": [],
            "gather": False,
            "deliver": False,
            "hold": False,
            "can_gather": True,
            "can_capture": True,
        }
    }

    pairs = legal_actions_to_pairs(legal_actions)

    assert not any(p["action"]["action"] in ("can_gather", "can_capture") for p in pairs)
    assert pairs == []


def test_empty_legal_actions_yields_no_pairs() -> None:
    assert legal_actions_to_pairs({}) == []


def test_a_pair_action_shape_matches_the_games_own_orders_json_action_schema() -> None:
    """Every pair's ``action`` value is directly usable as one entry of the
    game's own ``{"actions": [...]}`` orders-json body — see
    ``league_site.game.modes.enforce_action_cap``'s ``{"unit_id", "action",
    "to"}`` shape."""
    legal_actions = {"u1": {"move": [[3, 4]], "gather": False, "deliver": False, "hold": True}}

    pairs = legal_actions_to_pairs(legal_actions)

    move_action = pairs[0]["action"]
    assert set(move_action) == {"unit_id", "action", "to"}
    hold_action = pairs[1]["action"]
    assert set(hold_action) == {"unit_id", "action"}


# --- determinism --------------------------------------------------------------


def test_expansion_is_deterministic_regardless_of_input_dict_key_order() -> None:
    forward = {
        "u1": {"move": [], "gather": False, "deliver": False, "hold": True},
        "u2": {"move": [[1, 1]], "gather": False, "deliver": False, "hold": True},
    }
    reversed_keys = {"u2": forward["u2"], "u1": forward["u1"]}

    assert legal_actions_to_pairs(forward) == legal_actions_to_pairs(reversed_keys)


def test_move_cells_are_expanded_in_sorted_order_regardless_of_input_order() -> None:
    legal_actions = {"u1": {"move": [[5, 0], [0, 0], [1, 9]], "hold": False}}

    pairs = legal_actions_to_pairs(legal_actions)

    assert [p["action"]["to"] for p in pairs] == [[0, 0], [1, 9], [5, 0]]


def test_calling_twice_on_the_same_input_produces_byte_identical_output() -> None:
    legal_actions = {
        "u2": {"move": [[1, 0]], "gather": True, "hold": True},
        "u1": {"move": [], "gather": False, "hold": True},
    }

    assert legal_actions_to_pairs(legal_actions) == legal_actions_to_pairs(legal_actions)


# --- build_match_view ---------------------------------------------------------


def _grid_state(**overrides: object) -> dict:
    fields: dict = {
        "game_id": "league-of-agents-grid",
        "mode": "solo-vs-bot",
        "match_id": "m-1",
        "turn": 2,
        "legal_actions": {
            "solo-u1": {"move": [[1, 1]], "gather": False, "deliver": False, "hold": True},
        },
        "last_turn_rejections": [],
        "last_turn_platform_rejections": [],
    }
    fields.update(overrides)
    return fields


def test_build_match_view_expands_legal_actions_and_passes_state_through() -> None:
    state = _grid_state()

    view = build_match_view(state)

    assert view.state == state
    assert view.legal_actions == legal_actions_to_pairs(state["legal_actions"])


def test_build_match_view_combines_game_and_platform_rejections_game_first() -> None:
    game_rejection = {"team_id": "solo", "unit_id": "solo-u2", "reason": "game refused"}
    platform_rejection = {"team_id": "solo", "unit_id": "solo-u3", "reason": "platform cap"}
    state = _grid_state(
        last_turn_rejections=[game_rejection],
        last_turn_platform_rejections=[platform_rejection],
    )

    view = build_match_view(state)

    assert view.last_turn_rejections == [game_rejection, platform_rejection]


def test_build_match_view_with_no_team_keeps_every_units_legal_actions() -> None:
    state = _grid_state(
        legal_actions={
            "blue-u1": {"move": [], "gather": False, "deliver": False, "hold": True},
            "red-u1": {"move": [], "gather": False, "deliver": False, "hold": True},
        }
    )

    view = build_match_view(state)

    assert view.team is None
    assert {pair["unit"] for pair in view.legal_actions} == {"blue-u1", "red-u1"}


def test_build_match_view_with_a_team_filters_to_that_teams_units_only() -> None:
    state = _grid_state(
        legal_actions={
            "blue-u1": {"move": [], "gather": False, "deliver": False, "hold": True},
            "red-u1": {"move": [], "gather": False, "deliver": False, "hold": True},
        }
    )

    view = build_match_view(state, team="blue")

    assert view.team == "blue"
    assert {pair["unit"] for pair in view.legal_actions} == {"blue-u1"}


def test_build_match_view_tolerates_a_missing_legal_actions_key() -> None:
    state = _grid_state()
    del state["legal_actions"]

    view = build_match_view(state)

    assert view.legal_actions == []


# --- end-to-end fake test: game state -> normalize -> byok run_turn ---------


def test_end_to_end_game_state_through_normalize_into_run_turn_legal_passes_illegal_dropped() -> (
    None
):
    state = _grid_state(
        legal_actions={
            "solo-u1": {
                "move": [[1, 1]],
                "gather": False,
                "deliver": False,
                "hold": True,
                "can_gather": False,
                "can_capture": False,
            }
        }
    )
    view = build_match_view(state, team="solo")

    vault = InMemoryKeyVault()
    handle = vault.put("player-1", "openai", API_KEY)

    legal_order = {"unit": "solo-u1", "action": {"unit_id": "solo-u1", "action": "hold"}}
    illegal_order = {
        "unit": "solo-u1",
        "action": {"unit_id": "solo-u1", "action": "move", "to": [9, 9]},
    }
    reply = json.dumps({"orders": [legal_order, illegal_order]})
    transport = RecordingTransport(_json_response({"choices": [{"message": {"content": reply}}]}))

    decision = run_turn(
        view,
        provider="openai",
        model="gpt-4o",
        handle=handle,
        vault=vault,
        transport=transport,
    )

    assert decision.orders == [legal_order]
    assert len(decision.dropped) == 1
    assert decision.dropped[0].unit == "solo-u1"
    assert decision.dropped[0].action == illegal_order["action"]

    # the validated orders' `action` values are directly the game's own
    # orders-json `{"actions": [...]}` shape, unchanged.
    actions_payload = {"actions": [order["action"] for order in decision.orders]}
    assert actions_payload == {"actions": [{"unit_id": "solo-u1", "action": "hold"}]}
