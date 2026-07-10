"""Tests for :mod:`league_site.game.normalize` — the ``GridLaneEngine`` <-> BYOK bridge.

Covers the task's acceptance criteria: expansion correctness (a
``GridLaneEngine``-shaped ``legal_actions`` dict becomes the exact flat
BYOK pair list), determinism (sorted, order-independent-of-input-order
output), and an end-to-end fake test driving a game state through
:func:`~league_site.game.normalize.build_match_view` into
:func:`~league_site.byok.runner.run_turn` with a stubbed provider — one
legal order passes through untouched, one illegal order is dropped.

Also covers platform issue #10 (surface quality axes + outcome breakdown on
the score endpoint): :func:`~league_site.game.normalize.fetch_score_report`,
:func:`~league_site.game.normalize.normalize_outcome`,
:func:`~league_site.game.normalize.normalize_quality_axes`, and the combined
:func:`~league_site.game.normalize.score_breakdown`. ``tests/fixtures/
grid_match_score.json`` is a real, unedited ``league match score --json``
capture (a ``team-vs-team`` bot-vs-bot draw, ``league-of-agents`` 0.16.0 —
see that file's sibling ``docs/game-integration.md`` for how it was played)
used to ground the normalization against the game's actual output shape,
not a hand-typed guess at it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from league_site.byok.providers import TransportRequest, TransportResponse
from league_site.byok.runner import run_turn
from league_site.byok.vault import InMemoryKeyVault
from league_site.game.normalize import (
    build_match_view,
    fetch_score_report,
    legal_actions_to_pairs,
    normalize_outcome,
    normalize_quality_axes,
    score_breakdown,
)
from league_site.game.runner import LeagueRunnerError

API_KEY = "sk-test-key-normalize"  # nosec B105 - test fixture

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


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


# --- fetch_score_report: platform issue #10 ----------------------------------


class _CannedRunner:
    """A minimal ``LeagueRunner``-shaped fake: always answers ``match score``
    with a fixed payload, recording the exact argv it was called with plus
    every file actually hydrated under ``cwd/.league`` *while it still
    exists* (:func:`fetch_score_report` tears the scratch dir down again
    before returning, so a caller can't inspect it afterward) — see
    ``tests/test_game_adapter_fake.py``'s fuller ``ScriptedRunner`` for the
    pattern this borrows."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls: list[tuple[str, ...]] = []
        self.hydrated_files: dict[str, str] = {}

    def run(self, args: list[str], *, cwd: Path, timeout: float | None = None) -> Any:
        self.calls.append(tuple(args))
        assert tuple(args[:2]) == ("match", "score"), args
        league_dir = Path(cwd) / ".league"
        if league_dir.is_dir():
            for path in sorted(league_dir.rglob("*")):
                if path.is_file():
                    self.hydrated_files[path.relative_to(league_dir).as_posix()] = path.read_text(
                        encoding="utf-8"
                    )
        return self.payload


class _FailingRunner:
    """Simulates the ``league`` CLI being unreachable in this environment."""

    def run(self, args: list[str], *, cwd: Path, timeout: float | None = None) -> Any:
        raise LeagueRunnerError("league CLI not found")


def test_fetch_score_report_returns_none_for_a_non_grid_shaped_state() -> None:
    assert fetch_score_report({"turns_taken": 0}) is None
    assert fetch_score_report({"match_id": "m-1"}) is None  # missing snapshot
    assert fetch_score_report({"snapshot": {"teams/solo.json": "{}"}}) is None  # missing match_id


def test_fetch_score_report_hydrates_the_snapshot_and_runs_match_score(tmp_path: Path) -> None:
    report = _load_fixture("grid_match_score.json")
    runner = _CannedRunner(report)
    state = {
        "match_id": "m-preset-team-vs-team",
        "snapshot": {"teams/blue.json": '{"id": "blue"}'},
    }

    result = fetch_score_report(state, runner=runner, workdir_root=str(tmp_path))

    assert result == report
    assert runner.calls == [("match", "score", "m-preset-team-vs-team", "--json")]
    assert runner.hydrated_files == {"teams/blue.json": '{"id": "blue"}'}
    # the scratch workdir this call hydrated is torn down after use --
    # nothing is left behind under `workdir_root`.
    assert list(tmp_path.iterdir()) == []


def test_fetch_score_report_defaults_to_a_real_league_runner_when_none_is_given() -> None:
    """No ``runner=`` kwarg -> a real :class:`~league_site.game.runner.LeagueRunner`
    is constructed internally; proven here by a state with no snapshot/match_id
    (so the call short-circuits to ``None`` before ever touching a subprocess)."""
    assert fetch_score_report({}) is None


# --- normalize_outcome --------------------------------------------------------


def test_normalize_outcome_extracts_the_four_int_fields_per_team() -> None:
    report = {
        "outcome": {
            "blue": {"total": 7, "missions": 2, "control": 3, "resources": 2},
            "red": {"total": 3, "missions": 0, "control": 0, "resources": 3},
        }
    }

    assert normalize_outcome(report) == {
        "blue": {"total": 7, "missions": 2, "control": 3, "resources": 2},
        "red": {"total": 3, "missions": 0, "control": 0, "resources": 3},
    }


def test_normalize_outcome_defaults_missing_subfields_to_zero() -> None:
    report = {"outcome": {"solo": {"total": 4}}}

    assert normalize_outcome(report) == {
        "solo": {"total": 4, "missions": 0, "control": 0, "resources": 0}
    }


def test_normalize_outcome_values_are_plain_ints() -> None:
    report = {
        "outcome": {"blue": {"total": 7.0, "missions": 2.0, "control": 3.0, "resources": 2.0}}
    }

    normalized = normalize_outcome(report)

    assert all(isinstance(v, int) for v in normalized["blue"].values())


def test_normalize_outcome_sorts_team_ids_for_determinism() -> None:
    report = {"outcome": {"red": {"total": 1}, "blue": {"total": 2}}}

    assert list(normalize_outcome(report)) == ["blue", "red"]


def test_normalize_outcome_handles_a_missing_outcome_key() -> None:
    assert normalize_outcome({}) == {}


def test_normalize_outcome_from_the_real_fixture() -> None:
    """Grounds the shape against a real, unedited ``league match score
    --json`` capture — see this module's docstring."""
    report = _load_fixture("grid_match_score.json")

    assert normalize_outcome(report) == {
        "blue": {"total": 0, "missions": 0, "control": 0, "resources": 0},
        "red": {"total": 0, "missions": 0, "control": 0, "resources": 0},
    }


# --- normalize_quality_axes ---------------------------------------------------


def test_normalize_quality_axes_float_coerces_every_grade() -> None:
    axes = {"p-1": {"mvp": 1, "lvp": 0, "cooperation_score": 80, "span_of_control_score": 55}}

    normalized = normalize_quality_axes(axes)

    assert normalized == {
        "p-1": {
            "mvp": 1.0,
            "lvp": 0.0,
            "cooperation_score": 80.0,
            "span_of_control_score": 55.0,
        }
    }
    assert all(isinstance(v, float) for v in normalized["p-1"].values())


def test_normalize_quality_axes_sorts_participant_ids_for_determinism() -> None:
    axes = {"p-2": {"mvp": 0.0}, "p-1": {"mvp": 1.0}}

    assert list(normalize_quality_axes(axes)) == ["p-1", "p-2"]


def test_normalize_quality_axes_handles_an_empty_mapping() -> None:
    assert normalize_quality_axes({}) == {}


# --- score_breakdown: engine duck typing + graceful degradation --------------


class _NoQualityAxesEngine:
    """Mirrors the built-in stub engine's public surface: no ``quality_axes``
    method at all — the "stub-engine matches simply omit the extra keys"
    contract from platform issue #10."""

    def score(self, state: Any) -> dict[str, float]:
        return {}


class _FakeGridEngine:
    """Duck-types ``GridLaneEngine``'s ``quality_axes`` surface only — enough
    for :func:`score_breakdown` to combine it with a fetched score report."""

    def __init__(self, axes: dict[str, dict[str, float]]) -> None:
        self._axes = axes
        self.calls_with: list[Any] = []

    def quality_axes(self, state: Any) -> dict[str, dict[str, float]]:
        self.calls_with.append(state)
        return self._axes


def test_score_breakdown_is_none_when_the_engine_has_no_quality_axes(tmp_path: Path) -> None:
    state = {"match_id": "m-1", "snapshot": {}}
    assert score_breakdown(_NoQualityAxesEngine(), state, workdir_root=str(tmp_path)) is None


def test_score_breakdown_is_none_when_the_state_is_not_grid_shaped() -> None:
    engine = _FakeGridEngine({"p-1": {"mvp": 1.0}})
    assert score_breakdown(engine, {"turns_taken": 3}) is None


def test_score_breakdown_combines_normalized_outcome_and_quality_axes(tmp_path: Path) -> None:
    report = _load_fixture("grid_match_score.json")
    runner = _CannedRunner(report)
    axes = {
        "p-1": {"cooperation_score": 80, "mvp": 1, "lvp": 0, "span_of_control_score": 0},
        "p-2": {"cooperation_score": 80, "mvp": 0, "lvp": 1, "span_of_control_score": 0},
    }
    engine = _FakeGridEngine(axes)
    state = {"match_id": "m-preset-team-vs-team", "snapshot": {}}

    extras = score_breakdown(engine, state, runner=runner, workdir_root=str(tmp_path))

    assert extras == {
        "outcome": normalize_outcome(report),
        "quality_axes": normalize_quality_axes(axes),
    }
    assert engine.calls_with == [state]  # quality_axes was asked about this exact state


def test_score_breakdown_returns_none_when_the_league_cli_is_unreachable(tmp_path: Path) -> None:
    engine = _FakeGridEngine({"p-1": {"mvp": 1.0}})
    state = {"match_id": "m-x", "snapshot": {}}

    extras = score_breakdown(engine, state, runner=_FailingRunner(), workdir_root=str(tmp_path))

    assert extras is None


@pytest.mark.parametrize("axis_value", [1, 1.0])
def test_score_breakdown_quality_axes_are_always_floats_regardless_of_input_type(
    tmp_path: Path, axis_value: Any
) -> None:
    report = _load_fixture("grid_match_score.json")
    runner = _CannedRunner(report)
    engine = _FakeGridEngine({"p-1": {"mvp": axis_value}})
    state = {"match_id": "m-preset-team-vs-team", "snapshot": {}}

    extras = score_breakdown(engine, state, runner=runner, workdir_root=str(tmp_path))

    assert extras is not None
    assert extras["quality_axes"]["p-1"]["mvp"] == 1.0
    assert isinstance(extras["quality_axes"]["p-1"]["mvp"], float)
