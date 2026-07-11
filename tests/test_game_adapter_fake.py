"""Unit tests for :class:`~league_site.game.adapter.GridLaneEngine` against a
scripted fake runner — no real ``league`` binary involved.

The fake (:class:`ScriptedRunner`) is a spy: it records every call made to
it (verb + argv), and tracks just enough turn/staging state to drive a
believable multi-turn match without ever touching a subprocess or the
filesystem's ``.league/`` layout. That is exactly what lets
``test_solo_mode_excess_actions_never_reach_match_act`` prove the h14
honesty condition directly: inspect the *recorded calls* and show no
``match act`` call ever carried more than the mode's action cap.

Real end-to-end behavior (actually driving the CLI, workdir round-trips,
score matching ``league match score --json`` byte for byte) is covered in
``tests/test_game_real_cli.py``, gated on the CLI being installed.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from league_site.game.adapter import GAME_ID, GridLaneEngine
from league_site.game.modes import SOLO_VS_BOT, LaunchMode, TeamSpec
from league_site.matches.engine import GameEngine
from league_site.matches.models import AgentIdentity, Participant, ParticipantKind


def _agent(pid: str, name: str = "Agent") -> Participant:
    return Participant(
        display_name=name,
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id=pid,
    )


def _human(pid: str, name: str = "Human") -> Participant:
    return Participant(display_name=name, kind=ParticipantKind.HUMAN, participant_id=pid)


class ScriptedRunner:
    """Records every call; simulates just enough of the league CLI's
    ``arena show`` / ``team register`` / ``match new|show|act|tick|score|probe``
    contract to drive :class:`GridLaneEngine` through a full match."""

    def __init__(
        self,
        *,
        team_ids: list[str],
        turn_limit: int = 3,
        roles: tuple[str, ...] = ("scout", "harvester", "defender"),
        outcome_totals: dict[str, int] | None = None,
        cooperation_scores: dict[str, int] | None = None,
        probe_scores: dict[str, int] | None = None,
        mvp_unit: dict[str, str] | None = None,
        lvp_unit: dict[str, str] | None = None,
        units: dict[str, dict[str, Any]] | None = None,
        board_state: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.harness_configs: list[dict[str, Any]] = []
        self.team_ids = list(team_ids)
        self.turn_limit = turn_limit
        self.roles = roles
        self.turn = 0
        self.status = "active"
        self.staged: set[str] = set()
        self.match_id: str | None = None
        self._outcome_totals = outcome_totals or {t: 0 for t in team_ids}
        self._cooperation_scores = cooperation_scores or {t: 0 for t in team_ids}
        self._probe_scores = probe_scores or {t: 0 for t in team_ids}
        self._mvp_unit = mvp_unit
        self._lvp_unit = lvp_unit
        self._units = units or {}
        #: Extra `match show --json` "state" keys (the board projections a
        #: real CLI always includes: grid_width/units/...); absent by default
        #: so pre-board scripted shows keep exercising the degraded path.
        self._board_state = board_state or {}

    def run_text(self, args: list[str], *, cwd: Path, timeout: float | None = None) -> str:
        self.calls.append(("run_text", tuple(args)))
        assert args == ["--version"]
        return "league-of-agents 0.13.1\n"

    def run(self, args: list[str], *, cwd: Path, timeout: float | None = None) -> Any:
        self.calls.append(("run", tuple(args)))
        head = tuple(args[:2])
        if head == ("arena", "show"):
            return {"roles": {role: {} for role in self.roles}}
        if args[:1] == ["whoami"]:
            return {"nick": "x", "version": "0.13.1", "backend": "unknown", "model": "unknown"}
        if head == ("team", "register"):
            return {"id": args[2], "applied": True}
        if head == ("match", "new"):
            self.match_id = args[args.index("--id") + 1]
            return {"match_id": self.match_id, "applied": True}
        if head == ("match", "act"):
            team_id = args[args.index("--team") + 1]
            orders_json = args[args.index("--orders-json") + 1]
            json.loads(orders_json)  # must always be valid JSON
            self.staged.add(team_id)
            resolves = self.staged >= set(self.team_ids)
            if resolves:
                self._resolve()
            return {"resolves_turn": resolves}
        if head == ("match", "tick"):
            self._resolve()
            return {"resolution": {"turn": self.turn}}
        if head == ("harness", "run"):
            # `league harness run` resuming an existing match: stage every
            # bot team named in the config, auto-resolving once all teams
            # have staged — mirroring run_match's act-per-team loop. The
            # config file lives in the adapter's temp workdir (deleted right
            # after apply_turn), so capture its parsed content now.
            config_path = Path(args[args.index("--config") + 1])
            config = json.loads(config_path.read_text(encoding="utf-8"))
            self.harness_configs.append(config)
            for team in config["teams"]:
                self.staged.add(team["id"])
            if self.staged >= set(self.team_ids):
                self._resolve()
            return {
                "match_id": self.match_id,
                "status": self.status,
                "turns_played": self.turn,
                "winner": None,
                "score": {},
            }
        if head == ("match", "show"):
            return self._show()
        if head == ("match", "score"):
            return self._score()
        if head == ("match", "probe"):
            return self._probe()
        raise AssertionError(f"unscripted call: {args}")

    def act_calls(self) -> list[tuple[str, ...]]:
        return [args for verb, args in self.calls if verb == "run" and args[:2] == ("match", "act")]

    def tick_calls(self) -> list[tuple[str, ...]]:
        return [
            args for verb, args in self.calls if verb == "run" and args[:2] == ("match", "tick")
        ]

    def harness_calls(self) -> list[tuple[str, ...]]:
        return [
            args for verb, args in self.calls if verb == "run" and args[:2] == ("harness", "run")
        ]

    def _resolve(self) -> None:
        self.turn += 1
        self.staged.clear()
        if self.turn >= self.turn_limit:
            self.status = "finished"

    def _show(self) -> dict[str, Any]:
        return {
            "state": {
                "status": self.status,
                "turn": self.turn,
                "turn_limit": self.turn_limit,
                "winner": None,
                **self._board_state,
            },
            "legal_actions": {"fake-unit": {"move": []}},
            "last_turn_rejections": [],
            "staged_teams": sorted(self.staged),
        }

    def _score(self) -> dict[str, Any]:
        return {
            "outcome": {t: {"total": self._outcome_totals.get(t, 0)} for t in self.team_ids},
            "cooperation": {
                t: {"score": self._cooperation_scores.get(t, 0)} for t in self.team_ids
            },
            "units": {"mvp": self._mvp_unit, "lvp": self._lvp_unit, "units": self._units},
        }

    def _probe(self) -> dict[str, Any]:
        return {"teams": {t: {"score": self._probe_scores.get(t, 0)} for t in self.team_ids}}


# -- construction / ABC conformance ------------------------------------------


def test_grid_lane_engine_is_a_game_engine() -> None:
    engine = GridLaneEngine("solo-vs-bot", runner=ScriptedRunner(team_ids=["solo", "house"]))
    assert isinstance(engine, GameEngine)
    assert engine.game_id == GAME_ID == "league-of-agents-grid"


def test_constructor_accepts_a_launch_mode_object_directly() -> None:
    engine = GridLaneEngine(SOLO_VS_BOT, runner=ScriptedRunner(team_ids=["solo", "house"]))
    assert engine.mode.name == "solo-vs-bot"


def test_constructor_overrides_scenario_and_seed() -> None:
    engine = GridLaneEngine(
        "solo-vs-bot",
        runner=ScriptedRunner(team_ids=["solo", "house"]),
        scenario_id="skirmish-2",
        seed=999,
    )
    assert engine.mode.scenario_id == "skirmish-2"
    assert engine.mode.seed == 999


# -- initial_state ------------------------------------------------------------


def test_initial_state_registers_every_team_and_creates_the_match(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["solo", "house"])
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)
    participant = _agent("p-1")

    state = engine.initial_state([participant])

    register_calls = [a for v, a in scripted.calls if v == "run" and a[:2] == ("team", "register")]
    registered_team_ids = {call[2] for call in register_calls}
    assert registered_team_ids == {"solo", "house"}

    new_call = next(a for v, a in scripted.calls if v == "run" and a[:2] == ("match", "new"))
    assert "--scenario" in new_call and "skirmish-1" in new_call
    assert "--mode" in new_call and "competitive" in new_call
    assert new_call.count("--team") == 2
    assert "--driver" in new_call

    assert state["game_id"] == GAME_ID
    assert state["mode"] == "solo-vs-bot"
    assert state["participant_teams"] == {"p-1": "solo"}
    assert state["team_participants"] == {"solo": ["p-1"]}
    assert state["status"] == "active"
    assert state["turn"] == 0
    assert state["game_version"] == "0.13.1"
    assert state["match_id"] == scripted.match_id


def test_initial_state_rejects_the_wrong_participant_count() -> None:
    engine = GridLaneEngine("solo-vs-bot", runner=ScriptedRunner(team_ids=["solo", "house"]))
    with pytest.raises(ValueError, match="needs exactly 1"):
        engine.initial_state([_agent("p-1"), _agent("p-2")])


# -- board projections: mirrored from `match show --json` for the board UI ----


def test_state_mirrors_the_shows_board_projections(tmp_path: Path) -> None:
    """The board fields (grid dims, unit positions, control points, resource
    nodes, missions) ride the state verbatim — they are what the play/viewer
    board (:mod:`league_site.viewer.board`) renders."""
    board = {
        "grid_width": 14,
        "grid_height": 12,
        "units": [{"id": "solo-u1", "team_id": "solo", "role": "scout", "pos": [0, 0]}],
        "control_points": [{"id": "cp-relay", "pos": [7, 5], "owner": None}],
        "resource_nodes": [{"id": "rn-lowland", "pos": [5, 5], "remaining": 12}],
        "missions": [{"id": "ms-caravan", "kind": "deliver", "pos": [6, 6], "status": "open"}],
    }
    scripted = ScriptedRunner(team_ids=["solo", "house"], board_state=board)
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)

    state = engine.initial_state([_agent("p-1")])
    for key, value in board.items():
        assert state[key] == value, key

    state = engine.apply_turn(state, "p-1", {"actions": []})
    for key, value in board.items():
        assert state[key] == value, key


def test_a_show_without_board_projections_still_builds_a_state(tmp_path: Path) -> None:
    """Board fields are additive: a `match show` payload without them (or a
    pre-board persisted state) must not break state construction — the play
    surface then simply renders without a board."""
    scripted = ScriptedRunner(team_ids=["solo", "house"])
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)
    state = engine.initial_state([_agent("p-1")])
    assert state["status"] == "active"
    assert state.get("units") in (None, [])


# -- apply_turn: the house/bot side is driven, not force-ticked (platform#9) --


def test_solo_vs_bot_apply_turn_drives_the_house_via_harness_run(tmp_path: Path) -> None:
    """After the solo side stages, the adapter stages the house side through
    ``league harness run`` (the game's own bot policy), which auto-resolves
    the turn — ``match tick`` (all-holds) is never needed."""
    scripted = ScriptedRunner(team_ids=["solo", "house"], turn_limit=5)
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)
    participant = _agent("p-1")
    state = engine.initial_state([participant])

    state = engine.apply_turn(state, "p-1", {"actions": []})

    (harness_call,) = scripted.harness_calls()
    assert "--apply" in harness_call and "--json" in harness_call
    assert scripted.tick_calls() == []
    assert state["turn"] == 1
    assert state["status"] == "active"


def test_the_harness_config_resumes_the_match_and_drives_only_the_house(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["solo", "house"], turn_limit=5)
    engine = GridLaneEngine(
        "solo-vs-bot", runner=scripted, workdir_root=tmp_path, scenario_id="skirmish-2"
    )
    state = engine.initial_state([_agent("p-1")])

    engine.apply_turn(state, "p-1", {"actions": []})

    (config,) = scripted.harness_configs
    assert config == {
        "match": {"scenario": "skirmish-2", "id": scripted.match_id},
        "teams": [{"id": "house", "driver": {"type": "bot"}}],
        "max_rounds": 1,
    }


def test_a_policy_less_bot_team_still_falls_back_to_match_tick(tmp_path: Path) -> None:
    """A mode may declare a deliberately passive house (``bot_policy=None``);
    the pre-#9 behavior — force-resolve with ``match tick``, unstaged team
    holds — remains for exactly that case."""
    mode = LaunchMode(
        name="solo-vs-idle-bot",
        game_mode="competitive",
        scenario_id="skirmish-1",
        seed=1,
        expected_participants=1,
        teams=(
            TeamSpec(team_id="solo", driver_kind="stateless", action_cap=1),
            TeamSpec(team_id="idle-house", driver_kind="bot", is_bot=True, bot_policy=None),
        ),
    )
    scripted = ScriptedRunner(team_ids=["solo", "idle-house"], turn_limit=5)
    engine = GridLaneEngine(mode, runner=scripted, workdir_root=tmp_path)
    state = engine.initial_state([_agent("p-1")])

    state = engine.apply_turn(state, "p-1", {"actions": []})

    assert scripted.harness_calls() == []
    assert len(scripted.tick_calls()) == 1
    assert state["turn"] == 1


def test_initial_state_records_which_bot_policy_the_house_runs(tmp_path: Path) -> None:
    """Issue #9 acceptance: mode metadata records which bot policy the house
    ran — on the match state and on the house roster's model label."""
    scripted = ScriptedRunner(team_ids=["solo", "house"])
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)

    state = engine.initial_state([_agent("p-1")])

    assert state["bot_policies"] == {"house": "bot:greedy"}
    # ...and the policy survives a turn (carried forward through apply_turn).
    state = engine.apply_turn(state, "p-1", {"actions": []})
    assert state["bot_policies"] == {"house": "bot:greedy"}

    register_calls = [a for v, a in scripted.calls if v == "run" and a[:2] == ("team", "register")]
    house_call = next(c for c in register_calls if c[2] == "house")
    # the game's --agent spec is <id>:<model>:<role>; the model keeps its
    # own colons (the CLI parses id before the first colon, role after the
    # last), so the policy label rides along verbatim.
    assert "house-scout:bot:greedy:scout" in house_call


def test_team_vs_team_apply_turn_never_ticks_and_resolves_when_both_stage(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["blue", "red"], turn_limit=5)
    engine = GridLaneEngine("team-vs-team", runner=scripted, workdir_root=tmp_path)
    p1, p2 = _agent("p-1"), _agent("p-2")
    state = engine.initial_state([p1, p2])
    assert state["participant_teams"] == {"p-1": "blue", "p-2": "red"}

    state = engine.apply_turn(state, "p-1", {"actions": []})
    assert state["turn"] == 0  # waiting on p-2
    assert scripted.tick_calls() == []
    assert scripted.harness_calls() == []

    state = engine.apply_turn(state, "p-2", {"actions": []})
    assert state["turn"] == 1  # both staged -> auto-resolved
    assert scripted.tick_calls() == []
    assert scripted.harness_calls() == []


def test_coop_2_apply_turn_resolves_immediately_every_call(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["coop"], turn_limit=4)
    engine = GridLaneEngine("coop-2", runner=scripted, workdir_root=tmp_path)
    p1, p2 = _human("p-1"), _human("p-2")
    state = engine.initial_state([p1, p2])
    assert state["participant_teams"] == {"p-1": "coop", "p-2": "coop"}

    state = engine.apply_turn(state, "p-1", {"actions": []})
    assert state["turn"] == 1
    state = engine.apply_turn(state, "p-2", {"actions": []})
    assert state["turn"] == 2
    assert scripted.tick_calls() == []
    assert scripted.harness_calls() == []


def test_apply_turn_rejects_an_unknown_participant(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["solo", "house"])
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)
    state = engine.initial_state([_agent("p-1")])
    with pytest.raises(ValueError, match="does not control any team"):
        engine.apply_turn(state, "p-ghost", {"actions": []})


def test_apply_turn_rejects_a_non_mapping_action(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["solo", "house"])
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)
    state = engine.initial_state([_agent("p-1")])
    with pytest.raises(TypeError, match="JSON-object-shaped mapping"):
        engine.apply_turn(state, "p-1", "not a dict")  # type: ignore[arg-type]


# -- h14: solo mode caps orders BEFORE match act is ever invoked -------------


def test_solo_mode_excess_actions_never_reach_match_act(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["solo", "house"], turn_limit=5)
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)
    state = engine.initial_state([_agent("p-1")])

    excess_action = {
        "actions": [
            {"unit_id": "solo-u1", "action": "hold"},
            {"unit_id": "solo-u2", "action": "hold"},
            {"unit_id": "solo-u3", "action": "move", "to": [1, 1]},
        ]
    }
    state = engine.apply_turn(state, "p-1", excess_action)

    # exactly one `match act` call, and it carries at most 1 action
    (act_call,) = scripted.act_calls()
    submitted = json.loads(act_call[act_call.index("--orders-json") + 1])
    assert len(submitted["actions"]) == 1
    assert submitted["actions"][0]["unit_id"] == "solo-u1"

    # the refusal is recorded on the returned state (load-bearing for callers)
    assert len(state["last_turn_platform_rejections"]) == 2
    refused_units = {r["unit_id"] for r in state["last_turn_platform_rejections"]}
    assert refused_units == {"solo-u2", "solo-u3"}
    assert all(r["team_id"] == "solo" for r in state["last_turn_platform_rejections"])


def test_team_vs_team_never_caps_actions(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["blue", "red"], turn_limit=5)
    engine = GridLaneEngine("team-vs-team", runner=scripted, workdir_root=tmp_path)
    state = engine.initial_state([_agent("p-1"), _agent("p-2")])
    many_actions = {"actions": [{"unit_id": f"blue-u{i}", "action": "hold"} for i in range(3)]}

    state = engine.apply_turn(state, "p-1", many_actions)

    assert state["last_turn_platform_rejections"] == []
    (act_call,) = scripted.act_calls()
    submitted = json.loads(act_call[act_call.index("--orders-json") + 1])
    assert len(submitted["actions"]) == 3


# -- is_over -------------------------------------------------------------------


def test_is_over_reflects_the_finished_status(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["solo", "house"], turn_limit=2)
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)
    state = engine.initial_state([_agent("p-1")])
    assert engine.is_over(state) is False

    state = engine.apply_turn(state, "p-1", {"actions": []})
    assert engine.is_over(state) is False
    state = engine.apply_turn(state, "p-1", {"actions": []})
    assert engine.is_over(state) is True


# -- score / quality_axes mapping ---------------------------------------------


def test_score_maps_outcome_total_per_participant(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["blue", "red"], outcome_totals={"blue": 7, "red": 3})
    engine = GridLaneEngine("team-vs-team", runner=scripted, workdir_root=tmp_path)
    state = {
        "match_id": "m-x",
        "snapshot": {},
        "participant_teams": {"p-1": "blue", "p-2": "red"},
    }
    assert engine.score(state) == {"p-1": 7.0, "p-2": 3.0}


def test_score_is_a_plain_dict_of_floats_matching_the_game_engine_contract(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["solo", "house"], outcome_totals={"solo": 12, "house": 0})
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)
    state = {"match_id": "m-x", "snapshot": {}, "participant_teams": {"p-1": "solo"}}
    scores = engine.score(state)
    assert scores == {"p-1": 12.0, "house": 0.0}
    assert all(isinstance(v, float) for v in scores.values())


def test_score_includes_house_teams_so_a_winning_house_wins(tmp_path: Path) -> None:
    """Live-prod finding: with only participant teams in the score map, a

    solo player who lost 0-21 to the house was crowned
    winner_participant_id (sole leader of a one-entry dict). Non-participant
    teams are keyed by their bare team id — participant ids are namespaced
    ("agent:...", "human:..."), so team ids cannot collide.
    """
    scripted = ScriptedRunner(team_ids=["solo", "house"], outcome_totals={"solo": 0, "house": 21})
    engine = GridLaneEngine("solo-vs-bot", runner=scripted, workdir_root=tmp_path)
    state = {"match_id": "m-x", "snapshot": {}, "participant_teams": {"p-1": "solo"}}

    scores = engine.score(state)

    assert scores == {"p-1": 0.0, "house": 21.0}

    from league_site.matches.match import _sole_leader

    assert _sole_leader(scores) == "house"


def test_quality_axes_maps_cooperation_mvp_lvp_and_span_of_control(tmp_path: Path) -> None:
    scripted = ScriptedRunner(
        team_ids=["blue", "red"],
        cooperation_scores={"blue": 80, "red": 20},
        probe_scores={"blue": 55, "red": 10},
        mvp_unit={"unit_id": "blue-u2", "team_id": "blue", "grade": 9},
        lvp_unit={"unit_id": "red-u1", "team_id": "red", "grade": 1},
        units={
            "blue-u1": {"team_id": "blue"},
            "blue-u2": {"team_id": "blue"},
            "red-u1": {"team_id": "red"},
        },
    )
    engine = GridLaneEngine("team-vs-team", runner=scripted, workdir_root=tmp_path)
    state = {
        "match_id": "m-x",
        "snapshot": {},
        "participant_teams": {"p-1": "blue", "p-2": "red"},
    }

    axes = engine.quality_axes(state)

    assert axes["p-1"] == {
        "cooperation_score": 80.0,
        "mvp": 1.0,
        "lvp": 0.0,
        "span_of_control_score": 55.0,
    }
    assert axes["p-2"] == {
        "cooperation_score": 20.0,
        "mvp": 0.0,
        "lvp": 1.0,
        "span_of_control_score": 10.0,
    }


def test_quality_axes_handles_no_mvp_or_lvp(tmp_path: Path) -> None:
    scripted = ScriptedRunner(team_ids=["coop"], cooperation_scores={"coop": 0})
    engine = GridLaneEngine("coop-2", runner=scripted, workdir_root=tmp_path)
    state = {
        "match_id": "m-x",
        "snapshot": {},
        "participant_teams": {"p-1": "coop", "p-2": "coop"},
    }
    axes = engine.quality_axes(state)
    assert axes["p-1"]["mvp"] == 0.0
    assert axes["p-1"]["lvp"] == 0.0
