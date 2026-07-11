"""Grid-lane wiring tests for :func:`league_site.web.http.site_app`.

Proves the seam ``tests/test_api_registry.py`` only unit-tests in
isolation: creating a match through the *public, composed* API with
``mode`` set to one of the three bundled launch modes actually dispatches
to :class:`~league_site.game.adapter.GridLaneEngine`, not the built-in stub
— once against a scripted fake runner (no ``league`` binary involved, always
runs), and once against the real ``league`` CLI end to end (skipped where
that binary isn't installed).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from league_site.auth import tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.game.adapter import GAME_ID, GridLaneEngine
from league_site.matches import InMemoryMatchStore
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from league_site.web.http import site_app
from tests._api_support import bearer, call


class ScriptedRunner:
    """Records every call; simulates just enough of the league CLI's
    ``arena show`` / ``team register`` / ``match new|show|act|tick`` contract
    to drive :class:`GridLaneEngine` through ``initial_state`` + one
    ``apply_turn`` — see ``tests/test_game_adapter_fake.py`` for the fuller
    version this is adapted from."""

    def __init__(self, *, team_ids: list[str], turn_limit: int = 5) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.team_ids = list(team_ids)
        self.turn_limit = turn_limit
        self.turn = 0
        self.status = "active"
        self.staged: set[str] = set()
        self.match_id: str | None = None

    def run_text(self, args: list[str], *, cwd: Path, timeout: float | None = None) -> str:
        self.calls.append(("run_text", tuple(args)))
        return "league-of-agents 0.13.1\n"

    def run(self, args: list[str], *, cwd: Path, timeout: float | None = None) -> Any:
        self.calls.append(("run", tuple(args)))
        head = tuple(args[:2])
        if head == ("arena", "show"):
            return {"roles": {"scout": {}, "harvester": {}, "defender": {}}}
        if args[:1] == ["whoami"]:
            return {"nick": "x", "version": "0.13.1", "backend": "unknown", "model": "unknown"}
        if head == ("team", "register"):
            return {"id": args[2], "applied": True}
        if head == ("match", "new"):
            self.match_id = args[args.index("--id") + 1]
            return {"match_id": self.match_id, "applied": True}
        if head == ("match", "act"):
            team_id = args[args.index("--team") + 1]
            json.loads(args[args.index("--orders-json") + 1])  # must be valid JSON
            self.staged.add(team_id)
            resolves = self.staged >= set(self.team_ids)
            if resolves:
                self._resolve()
            return {"resolves_turn": resolves}
        if head == ("match", "tick"):
            self._resolve()
            return {"resolution": {"turn": self.turn}}
        if head == ("harness", "run"):
            # platform#9: the adapter drives the house side via `league
            # harness run` resuming the existing match — stage the config's
            # bot teams and auto-resolve once every team has staged.
            config = json.loads(Path(args[args.index("--config") + 1]).read_text("utf-8"))
            self.staged.update(team["id"] for team in config["teams"])
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
        raise AssertionError(f"unscripted call: {args}")

    def act_calls(self) -> list[tuple[str, ...]]:
        return [args for verb, args in self.calls if verb == "run" and args[:2] == ("match", "act")]

    def tick_calls(self) -> list[tuple[str, ...]]:
        return [
            args for verb, args in self.calls if verb == "run" and args[:2] == ("match", "tick")
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
            },
            "legal_actions": {"solo-u1": {"move": []}},
            "last_turn_rejections": [],
            "staged_teams": sorted(self.staged),
        }


def _agent_auth(token_store: InMemoryTokenStore, agent_name: str = "Sonnet") -> dict:
    issued = tokens.issue(
        token_store,
        agent_name=agent_name,
        model="claude-sonnet-5",
        provider="anthropic",
        owner_account_id="github:agent-owner",
    )
    return {"headers": bearer(issued.token)}


# -- fake runner: dispatch proof, no `league` binary involved ----------------


def test_creating_a_solo_vs_bot_match_through_site_app_dispatches_to_grid_lane_engine() -> None:
    scripted = ScriptedRunner(team_ids=["solo", "house"])
    registry = {"solo-vs-bot": lambda: GridLaneEngine("solo-vs-bot", runner=scripted)}
    token_store = InMemoryTokenStore()
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=token_store,
        ledger_store=InMemoryRatingLedgerStore(),
        engine_registry=registry,
    )
    auth = _agent_auth(token_store)

    status, _, created = call(app, "POST", "/api/v1/matches", body={"mode": "solo-vs-bot"}, **auth)

    assert status == "201 Created", created
    assert created["game_id"] == "solo-vs-bot"
    # This is the shape of a real GridLaneEngine state (StubDuelEngine's has
    # no `match_id`/`participant_teams`/`turn`), and the scripted runner
    # really was invoked to build it.
    assert created["state"]["match_id"] == scripted.match_id
    assert created["state"]["participant_teams"]
    register_calls = [a for v, a in scripted.calls if v == "run" and a[:2] == ("team", "register")]
    assert {call_args[2] for call_args in register_calls} == {"solo", "house"}

    match_id = created["match_id"]
    status, _, after_turn = call(
        app,
        "POST",
        f"/api/v1/matches/{match_id}/turns",
        body={"action": {"actions": []}},
        **auth,
    )

    assert status == "200 OK", after_turn
    # solo-vs-bot: the adapter drives the house side with the game's own bot
    # policy via `league harness run` (platform#9) — proof the *engine*, not
    # just its constructor, was actually exercised.
    harness_calls = [a for v, a in scripted.calls if v == "run" and a[:2] == ("harness", "run")]
    assert len(harness_calls) == 1
    assert scripted.tick_calls() == []
    assert after_turn["state"]["turn"] == 1


def test_creating_a_stub_duel_match_through_site_app_does_not_touch_the_grid_lane_engine() -> None:
    """Sanity check on the other side of the seam: the default (omitted)
    mode still resolves to the built-in stub, never a grid-lane engine."""
    token_store = InMemoryTokenStore()
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=token_store,
        ledger_store=InMemoryRatingLedgerStore(),
    )
    auth = _agent_auth(token_store)

    status, _, created = call(app, "POST", "/api/v1/matches", body={}, **auth)

    assert status == "201 Created"
    assert created["game_id"] == "stub-duel"
    assert "match_id" not in created["state"]  # not a GridLaneEngine state


# -- real CLI end to end: create + play one solo-vs-bot turn -----------------


@pytest.mark.skipif(shutil.which("league") is None, reason="league CLI is not installed")
def test_real_cli_create_and_play_one_solo_vs_bot_turn_through_the_public_api() -> None:
    token_store = InMemoryTokenStore()
    app = site_app(
        match_store=InMemoryMatchStore(),
        token_store=token_store,
        ledger_store=InMemoryRatingLedgerStore(),
        # default engine_registry: exercises the real, production
        # `default_engine_registry()` -> lazy-imported `GridLaneEngine` ->
        # real `league` subprocess wiring end to end, not an injected fake.
    )
    auth = _agent_auth(token_store)

    status, _, created = call(app, "POST", "/api/v1/matches", body={"mode": "solo-vs-bot"}, **auth)

    assert status == "201 Created", created
    assert created["game_id"] == "solo-vs-bot"
    assert created["state"]["game_id"] == GAME_ID
    assert created["state"]["mode"] == "solo-vs-bot"
    assert created["state"]["status"] == "active"
    assert created["state"]["turn"] == 0
    assert created["state"]["game_version"]
    match_id = created["match_id"]

    status, _, after_turn = call(
        app,
        "POST",
        f"/api/v1/matches/{match_id}/turns",
        body={"action": {"actions": []}},
        **auth,
    )

    assert status == "200 OK", after_turn
    # solo-vs-bot: a single apply_turn call plays one full game turn — the
    # solo orders stage, then the adapter stages the house side through the
    # game's own bot policy (`league harness run`), which auto-resolves.
    assert after_turn["state"]["turn"] == 1
    assert after_turn["status"] in ("active", "completed")

    status, _, fetched = call(app, "GET", f"/api/v1/matches/{match_id}")
    assert status == "200 OK"
    assert fetched["state"]["turn"] == 1
