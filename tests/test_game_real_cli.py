"""Real-CLI integration tests for the League of Agents grid-lane adapter.

Gated on the ``league`` binary being on ``PATH`` — CI has no ``league``
install, so these skip there; locally (the machine has ``league`` 0.13.1 —
grid mechanics identical to 0.14.0, see ``docs/game-integration.md``) they
run for real, driving the actual subprocess end to end. Nothing here
imports the ``league`` package — only :class:`GridLaneEngine` and
:class:`~league_site.game.runner.LeagueRunner`, both of which shell out.

Turns are driven with empty orders (``{"actions": []}``, always legal —
"every unit implicitly holds if it declares nothing", ``bots/README.md``)
so every launch mode reaches its scenario's ``turn_limit`` deterministically
without needing hand-scripted winning paths; ``scenario_id="skirmish-2"``
(``turn_limit=16``, same three roles as the default ``skirmish-1``) keeps
each full-match test's wall-clock reasonable.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from league_site.game.adapter import GridLaneEngine
from league_site.game.runner import LeagueRunner
from league_site.game.workdir import hydrate, persist
from league_site.matches.models import AgentIdentity, Participant, ParticipantKind

pytestmark = pytest.mark.skipif(
    shutil.which("league") is None, reason="league CLI is not installed on this machine"
)

_FAST_SCENARIO = "skirmish-2"  # turn_limit=16, vs. skirmish-1's 30 — same roles
_SAFETY_MARGIN = 5


def _agent(pid: str) -> Participant:
    return Participant(
        display_name="Real CLI Agent",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id=pid,
    )


def _human(pid: str, name: str) -> Participant:
    return Participant(display_name=name, kind=ParticipantKind.HUMAN, participant_id=pid)


def _drive_to_completion(engine: GridLaneEngine, state: dict, participant_ids: list[str]) -> dict:
    """Submit empty (always-legal) orders in round-robin participant order
    until the match ends. Every non-bot team must stage once per game turn
    before it resolves, so the call budget is ``len(participant_ids)`` calls
    per game turn, not one — a competitive 2p mode needs two ``apply_turn``
    calls to advance a single game turn, a cooperative shared-team mode
    needs only one."""
    turn_limit = state["turn_limit"]
    max_calls = (turn_limit + _SAFETY_MARGIN) * len(participant_ids)
    i = 0
    while not engine.is_over(state) and i < max_calls:
        pid = participant_ids[i % len(participant_ids)]
        state = engine.apply_turn(state, pid, {"actions": []})
        i += 1
    assert engine.is_over(state), f"match did not finish within {max_calls} apply_turn calls"
    return state


# -- h10: each launch mode completes a full match end to end -----------------


def test_solo_vs_bot_completes_end_to_end(tmp_path: Path) -> None:
    engine = GridLaneEngine("solo-vs-bot", workdir_root=tmp_path, scenario_id=_FAST_SCENARIO)
    participant = _agent("p-solo")
    state = engine.initial_state([participant])
    assert state["status"] == "active"
    assert state["game_version"]  # recorded on every match record

    state = _drive_to_completion(engine, state, ["p-solo"])

    assert state["status"] == "finished"
    scores = engine.score(state)
    # The house team scores alongside the participant — leaving it out let a
    # losing solo player be crowned sole leader (live-prod finding).
    assert set(scores) == {"p-solo", "house"}
    axes = engine.quality_axes(state)
    assert set(axes) == {"p-solo"}
    assert {"cooperation_score", "mvp", "lvp", "span_of_control_score"} <= set(axes["p-solo"])


def test_team_vs_team_completes_end_to_end(tmp_path: Path) -> None:
    engine = GridLaneEngine("team-vs-team", workdir_root=tmp_path, scenario_id=_FAST_SCENARIO)
    p1, p2 = _agent("p-blue"), _agent("p-red")
    state = engine.initial_state([p1, p2])
    assert state["participant_teams"] == {"p-blue": "blue", "p-red": "red"}

    state = _drive_to_completion(engine, state, ["p-blue", "p-red"])

    assert state["status"] == "finished"
    scores = engine.score(state)
    assert set(scores) == {"p-blue", "p-red"}


def test_coop_2_completes_end_to_end(tmp_path: Path) -> None:
    engine = GridLaneEngine("coop-2", workdir_root=tmp_path, scenario_id=_FAST_SCENARIO)
    p1, p2 = _human("p-a", "Ada"), _human("p-b", "Bob")
    state = engine.initial_state([p1, p2])
    assert state["participant_teams"] == {"p-a": "coop", "p-b": "coop"}

    state = _drive_to_completion(engine, state, ["p-a", "p-b"])

    assert state["status"] == "finished"
    scores = engine.score(state)
    assert set(scores) == {"p-a", "p-b"}
    # cooperative: both participants share one team, so they share one score
    assert scores["p-a"] == scores["p-b"]


# -- platform#9: the house side really plays (game bot policy, not all-holds) --


def test_solo_vs_bot_house_side_stages_real_orders_from_the_game_bot_policy(
    tmp_path: Path,
) -> None:
    """A played match, end to end: the house team's turn record in the
    game's own transcript (the append-only ``log.jsonl``, carried verbatim
    in the platform snapshot) contains non-hold actions staged by the
    game's greedy bot policy — never the all-holds of an unstaged team."""
    engine = GridLaneEngine("solo-vs-bot", workdir_root=tmp_path, scenario_id=_FAST_SCENARIO)
    state = engine.initial_state([_agent("p-solo")])
    # issue #9 acceptance: the match record says which policy the house ran.
    assert state["bot_policies"] == {"house": "bot:greedy"}

    state = _drive_to_completion(engine, state, ["p-solo"])
    assert state["status"] == "finished"

    log_text = state["snapshot"][f"matches/{state['match_id']}/log.jsonl"]
    events = [json.loads(line) for line in log_text.splitlines() if line.strip()]
    house_actions = [
        event["data"]
        for event in events
        if event.get("kind") == "action_declared" and event["data"].get("team_id") == "house"
    ]
    assert house_actions, "the house team never declared a single action"
    non_hold = [a for a in house_actions if a.get("action") != "hold"]
    assert non_hold, f"the house team only ever held: {house_actions[:5]}"

    # An acting house scores: against an all-holds solo side, gathering and
    # capturing must put real points on the board (the stationary-puppet
    # house of platform#9 always finished 0-0).
    runner = LeagueRunner()
    verify_dir = tmp_path / "verify-house"
    hydrate(verify_dir, state["snapshot"])
    report = runner.run(["match", "score", state["match_id"], "--json"], cwd=verify_dir)
    assert report["outcome"]["house"]["total"] > 0
    assert report["outcome"]["house"]["total"] > report["outcome"]["solo"]["total"]


# -- h14 (real CLI confirmation): the excess is refused before match act -----


def test_solo_mode_action_cap_is_enforced_against_the_real_cli(tmp_path: Path) -> None:
    engine = GridLaneEngine("solo-vs-bot", workdir_root=tmp_path, scenario_id=_FAST_SCENARIO)
    state = engine.initial_state([_agent("p-solo")])
    unit_ids = list(state["legal_actions"])
    solo_units = [u for u in unit_ids if u.startswith("solo-")]
    assert len(solo_units) == 3  # scout/harvester/defender, engine-generated ids

    excess = {"actions": [{"unit_id": uid, "action": "hold"} for uid in solo_units]}
    state = engine.apply_turn(state, "p-solo", excess)

    assert state["turn"] == 1  # the (single, capped) order still played fine
    assert len(state["last_turn_platform_rejections"]) == 2
    assert {r["unit_id"] for r in state["last_turn_platform_rejections"]} == set(solo_units[1:])


# -- h9: adapter.score matches `league match score --json` field for field,
# -- byte-identical across two runs ------------------------------------------


def test_score_matches_the_cli_field_for_field_and_is_deterministic(tmp_path: Path) -> None:
    engine = GridLaneEngine("team-vs-team", workdir_root=tmp_path, scenario_id=_FAST_SCENARIO)
    state = engine.initial_state([_agent("p-blue"), _agent("p-red")])
    state = _drive_to_completion(engine, state, ["p-blue", "p-red"])

    run_1 = engine.score(state)
    run_2 = engine.score(state)
    assert run_1 == run_2  # byte-identical across two adapter runs

    # Independently re-derive the same numbers straight from the CLI, from
    # a THIRD hydration of the persisted snapshot (never the workdir the
    # adapter itself just used), and compare field for field.
    runner = LeagueRunner()
    verify_dir = tmp_path / "verify"
    hydrate(verify_dir, state["snapshot"])
    cli_report = runner.run(["match", "score", state["match_id"], "--json"], cwd=verify_dir)

    for participant_id, team_id in state["participant_teams"].items():
        assert run_1[participant_id] == float(cli_report["outcome"][team_id]["total"])


# -- h8: hydrate/play/persist/rehydrate round trip against the real CLI ------


def test_workdir_round_trip_state_matches_after_rehydrating_in_a_second_dir(
    tmp_path: Path,
) -> None:
    runner = LeagueRunner()
    dir_a = tmp_path / "a"
    dir_a.mkdir()
    runner.run(
        [
            "team",
            "register",
            "solo",
            "--agent",
            "solo-scout:human:scout",
            "--agent",
            "solo-harvester:human:harvester",
            "--agent",
            "solo-defender:human:defender",
            "--apply",
            "--json",
        ],
        cwd=dir_a,
    )
    runner.run(
        [
            "team",
            "register",
            "house",
            "--agent",
            "house-scout:bot:scout",
            "--agent",
            "house-harvester:bot:harvester",
            "--agent",
            "house-defender:bot:defender",
            "--apply",
            "--json",
        ],
        cwd=dir_a,
    )
    runner.run(
        [
            "match",
            "new",
            "--scenario",
            _FAST_SCENARIO,
            "--mode",
            "competitive",
            "--seed",
            "7",
            "--id",
            "m-roundtrip",
            "--team",
            "solo",
            "--team",
            "house",
            "--driver",
            "solo:stateless",
            "--driver",
            "house:bot",
            "--apply",
            "--json",
        ],
        cwd=dir_a,
    )
    runner.run(
        [
            "match",
            "act",
            "m-roundtrip",
            "--team",
            "solo",
            "--orders-json",
            "{}",
            "--apply",
            "--json",
        ],
        cwd=dir_a,
    )
    runner.run(["match", "tick", "m-roundtrip", "--apply", "--json"], cwd=dir_a)

    show_from_a = runner.run(["match", "show", "m-roundtrip", "--json"], cwd=dir_a)
    assert show_from_a["state"]["turn"] == 1

    # Persist dir_a's .league/ tree, hydrate a brand-new second directory
    # from that snapshot, and prove `match show --json` folds to the exact
    # same state there (h8's own honesty condition, verbatim).
    snapshot = persist(dir_a)
    dir_b = tmp_path / "b"
    hydrate(dir_b, snapshot)
    show_from_b = runner.run(["match", "show", "m-roundtrip", "--json"], cwd=dir_b)

    assert show_from_b == show_from_a


# -- game version pinning -----------------------------------------------------


def test_game_version_is_recorded_on_the_match_state(tmp_path: Path) -> None:
    engine = GridLaneEngine("solo-vs-bot", workdir_root=tmp_path, scenario_id=_FAST_SCENARIO)
    state = engine.initial_state([_agent("p-solo")])
    runner = LeagueRunner()
    banner = runner.run_text(["--version"], cwd=tmp_path)
    assert state["game_version"] and state["game_version"] in banner
