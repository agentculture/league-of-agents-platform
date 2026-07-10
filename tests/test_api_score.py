"""Tests for ``GET /api/v1/matches/<id>/score``'s additive quality-axes +
outcome-breakdown payload (platform issue #10).

Three layers, cheapest first:

* the built-in stub engine (no ``quality_axes``) -> the score response is
  unchanged, byte for byte, from before this task (additive-only contract);
* a real :class:`~league_site.game.adapter.GridLaneEngine` wired to a
  scripted fake runner (no ``league`` binary involved, always runs) fed a
  real, unedited ``league match score --json`` capture
  (``tests/fixtures/grid_match_score_solo_vs_house.json``) -> the endpoint's
  ``outcome``/``quality_axes`` match what
  :mod:`league_site.game.normalize` computes from that same fixture;
* one real-CLI, end-to-end test (skipped where ``league`` isn't installed):
  play a full ``solo-vs-bot`` match through the public API, then
  independently re-derive ``league match score --json`` from a third
  hydration of the match's own persisted snapshot (mirrors
  ``tests/test_game_real_cli.py``'s h9 pattern) and assert the platform's
  normalized payload agrees with the CLI's own numbers field for field —
  the acceptance criterion this task ships against.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from league_site.api.wsgi import with_api
from league_site.auth import tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.game.adapter import GridLaneEngine
from league_site.game.normalize import normalize_outcome, normalize_quality_axes
from league_site.game.runner import LeagueRunner
from league_site.game.workdir import hydrate
from league_site.matches import InMemoryMatchStore
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from tests._api_support import bearer, call

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((_FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _passthrough(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [f"not an api route: {environ.get('PATH_INFO')}".encode("utf-8")]


def _build(*, engine_registry: Any = None) -> tuple[Any, InMemoryTokenStore]:
    token_store = InMemoryTokenStore()
    app = with_api(
        _passthrough,
        match_store=InMemoryMatchStore(),
        token_store=token_store,
        ledger_store=InMemoryRatingLedgerStore(),
        engine_registry=engine_registry,
    )
    return app, token_store


def _issue_agent(token_store: InMemoryTokenStore, *, agent_name: str = "Sonnet") -> dict:
    issued = tokens.issue(
        token_store, agent_name=agent_name, model="claude-sonnet-5", provider="anthropic"
    )
    return {"headers": bearer(issued.token)}


# --- scripted grid-lane fake: no `league` binary required --------------------


class ScriptedRunner:
    """Drives :class:`GridLaneEngine` through one full ``solo-vs-bot`` match
    without touching a subprocess — see ``tests/test_game_adapter_fake.py``
    for the fuller version this is adapted from. ``score_report``/
    ``probe_report`` are returned verbatim from ``match score``/``match
    probe``, letting a test feed in a real captured fixture rather than a
    hand-typed guess at the CLI's shape."""

    def __init__(
        self,
        *,
        team_ids: list[str],
        turn_limit: int = 1,
        score_report: dict[str, Any],
        probe_report: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.team_ids = list(team_ids)
        self.turn_limit = turn_limit
        self.turn = 0
        self.status = "active"
        self.staged: set[str] = set()
        self.match_id: str | None = None
        self._score_report = score_report
        self._probe_report = probe_report or {"teams": {t: {"score": 0} for t in team_ids}}

    def run_text(self, args: list[str], *, cwd: Path, timeout: float | None = None) -> str:
        self.calls.append(("run_text", tuple(args)))
        return "league-of-agents 0.16.0\n"

    def run(self, args: list[str], *, cwd: Path, timeout: float | None = None) -> Any:
        self.calls.append(("run", tuple(args)))
        head = tuple(args[:2])
        if head == ("arena", "show"):
            return {"roles": {"scout": {}, "harvester": {}, "defender": {}}}
        if args[:1] == ["whoami"]:
            return {"nick": "x", "version": "0.16.0", "backend": "unknown", "model": "unknown"}
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
            # platform#9: the adapter drives bot-policy teams via `league
            # harness run` resuming the existing match — stage the config's
            # bot teams and auto-resolve once every team has staged (same
            # scripted branch as tests/test_web_http_grid.py's fake).
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
        if head == ("match", "score"):
            return self._score_report
        if head == ("match", "probe"):
            return self._probe_report
        raise AssertionError(f"unscripted call: {args}")

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


# --- stub engine: additive-only, no extra keys -------------------------------


def test_stub_engine_score_omits_outcome_and_quality_axes_keys() -> None:
    app, token_store = _build()
    auth = _issue_agent(token_store)
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **auth)
    match_id = created["match_id"]
    participant_id = created["participants"][0]["participant_id"]

    payload = created
    for _ in range(10):
        if payload["status"] == "completed":
            break
        status, _, payload = call(
            app,
            "POST",
            f"/api/v1/matches/{match_id}/turns",
            body={"action": {"points": 3}},
            **auth,
        )
        assert status == "200 OK", payload
    assert payload["status"] == "completed"
    assert participant_id  # sanity: the practice match had exactly one participant

    status, _, score = call(app, "GET", f"/api/v1/matches/{match_id}/score")

    assert status == "200 OK"
    assert score["result"]["completed"] is True  # unchanged, pre-existing field
    assert "outcome" not in score
    assert "quality_axes" not in score


# --- grid-lane engine, scripted (no `league` binary): fixture-verified ------


def _wire_scripted_runner(monkeypatch: pytest.MonkeyPatch, scripted: ScriptedRunner) -> None:
    """Make :func:`league_site.game.normalize.fetch_score_report`'s default
    ``LeagueRunner()`` construction resolve to *this same* scripted fake —
    the one already wired into the :class:`GridLaneEngine` under test —
    instead of a real :class:`~league_site.game.runner.LeagueRunner` that
    would try (and fail, no ``league`` binary involved in this test tier)
    to shell out for real. Patches the name as imported into
    :mod:`league_site.game.normalize` specifically, so
    ``league_site.game.adapter``'s own (unrelated, already-explicit
    ``runner=scripted``) reference is untouched."""
    monkeypatch.setattr("league_site.game.normalize.LeagueRunner", lambda: scripted)


def test_grid_lane_score_carries_outcome_and_quality_axes_from_a_real_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _load_fixture("grid_match_score_solo_vs_house.json")
    scripted = ScriptedRunner(team_ids=["solo", "house"], score_report=report)
    _wire_scripted_runner(monkeypatch, scripted)
    registry = {"solo-vs-bot": lambda: GridLaneEngine("solo-vs-bot", runner=scripted)}
    app, token_store = _build(engine_registry=registry)
    auth = _issue_agent(token_store)

    _, _, created = call(app, "POST", "/api/v1/matches", body={"mode": "solo-vs-bot"}, **auth)
    match_id = created["match_id"]
    participant_id = created["participants"][0]["participant_id"]

    status, _, after_turn = call(
        app,
        "POST",
        f"/api/v1/matches/{match_id}/turns",
        body={"action": {"actions": []}},
        **auth,
    )
    assert status == "200 OK", after_turn
    assert after_turn["status"] == "completed"

    status, _, score = call(app, "GET", f"/api/v1/matches/{match_id}/score")

    assert status == "200 OK"
    # pre-existing fields are untouched.
    assert score["match_id"] == match_id
    assert score["status"] == "completed"
    assert score["result"]["completed"] is True

    assert score["outcome"] == normalize_outcome(report)
    assert set(score["quality_axes"]) == {participant_id}
    axes = score["quality_axes"][participant_id]
    assert axes["cooperation_score"] == float(report["cooperation"]["solo"]["score"])
    assert axes["mvp"] == 1.0  # report's units.mvp.unit_id ("solo-u1") is on team "solo"
    assert axes["lvp"] == 0.0  # report's units.lvp.unit_id ("house-u1") is on team "house"
    assert isinstance(axes["cooperation_score"], float)


def test_grid_lane_score_outcome_keys_are_the_games_own_team_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _load_fixture("grid_match_score_solo_vs_house.json")
    scripted = ScriptedRunner(team_ids=["solo", "house"], score_report=report)
    _wire_scripted_runner(monkeypatch, scripted)
    registry = {"solo-vs-bot": lambda: GridLaneEngine("solo-vs-bot", runner=scripted)}
    app, token_store = _build(engine_registry=registry)
    auth = _issue_agent(token_store)
    _, _, created = call(app, "POST", "/api/v1/matches", body={"mode": "solo-vs-bot"}, **auth)
    match_id = created["match_id"]
    call(
        app,
        "POST",
        f"/api/v1/matches/{match_id}/turns",
        body={"action": {"actions": []}},
        **auth,
    )

    _, _, score = call(app, "GET", f"/api/v1/matches/{match_id}/score")

    assert set(score["outcome"]) == {"solo", "house"}
    for breakdown in score["outcome"].values():
        assert set(breakdown) == {"total", "missions", "control", "resources"}
        assert all(isinstance(v, int) for v in breakdown.values())


# --- real CLI, end to end: platform payload vs. the CLI's own numbers -------


@pytest.mark.skipif(shutil.which("league") is None, reason="league CLI is not installed")
def test_score_matches_the_real_cli_field_for_field_for_a_finished_grid_match(
    tmp_path: Path,
) -> None:
    # `skirmish-2` (turn_limit=16) keeps this real-subprocess test's wall
    # clock reasonable -- see tests/test_game_real_cli.py's `_FAST_SCENARIO`.
    registry = {
        "solo-vs-bot": lambda: GridLaneEngine(
            "solo-vs-bot", workdir_root=str(tmp_path), scenario_id="skirmish-2"
        )
    }
    app, token_store = _build(engine_registry=registry)
    auth = _issue_agent(token_store)

    status, _, created = call(app, "POST", "/api/v1/matches", body={"mode": "solo-vs-bot"}, **auth)
    assert status == "201 Created", created
    match_id = created["match_id"]

    payload = created
    for _ in range(30):
        if payload["status"] == "completed":
            break
        status, _, payload = call(
            app,
            "POST",
            f"/api/v1/matches/{match_id}/turns",
            body={"action": {"actions": []}},
            **auth,
        )
        assert status == "200 OK", payload
    assert payload["status"] == "completed", "solo-vs-bot match did not finish in time"

    status, _, fetched = call(app, "GET", f"/api/v1/matches/{match_id}")
    assert status == "200 OK"
    state = fetched["state"]

    status, _, score = call(app, "GET", f"/api/v1/matches/{match_id}/score")
    assert status == "200 OK"
    assert "outcome" in score
    assert "quality_axes" in score

    # Independently re-derive `league match score --json` from a THIRD
    # hydration of the persisted snapshot (never the workdir the platform
    # itself just used) and compare field for field.
    runner = LeagueRunner()
    verify_dir = tmp_path / "verify"
    hydrate(verify_dir, state["snapshot"])
    cli_report = runner.run(["match", "score", state["match_id"], "--json"], cwd=verify_dir)

    assert score["outcome"] == normalize_outcome(cli_report)

    verify_engine = GridLaneEngine(
        "solo-vs-bot", workdir_root=str(tmp_path), scenario_id="skirmish-2"
    )
    expected_axes = normalize_quality_axes(verify_engine.quality_axes(state))
    assert score["quality_axes"] == expected_axes
