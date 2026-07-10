"""``GridLaneEngine`` — the League of Agents grid-lane game behind ``GameEngine``.

Drives the game exclusively through :class:`~league_site.game.runner.LeagueRunner`
(a subprocess wrapper around the ``league`` CLI) against a fresh, isolated
workdir hydrated from — and persisted back to — the opaque ``state`` dict
:class:`~league_site.matches.match.Match` carries (see
:mod:`league_site.game.workdir`). No module in this package imports
``league``/``from league`` (``tests/test_game_import_boundary.py`` proves it
for the whole ``league_site`` tree); the game is only ever an external
process.

``state`` shape (a plain, JSON-safe dict): identifying fields
(``game_id``, ``mode``, ``match_id``, ``scenario_id``, ``seed``,
``game_version``), the ``snapshot`` (the game's own ``.league/`` tree,
byte-exact), read projections mirrored straight from the last
``league match show --json`` (``status``, ``turn``, ``turn_limit``,
``winner``, ``legal_actions``, ``last_turn_rejections``, ``staged_teams``),
this adapter's own mode-fairness refusals for the turn just played
(``last_turn_platform_rejections`` — see :mod:`league_site.game.modes`),
which game bot policy plays each house team (``bot_policies``,
``team_id -> policy label`` — issue #9's "mode metadata records which bot
policy the house ran"), and the participant/team bookkeeping needed to
score by ``participant_id`` (``participant_teams``, ``team_participants``).
"""

from __future__ import annotations

import contextlib
import json
import shutil
import tempfile
import uuid
from collections.abc import Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from league_site.game import bot as bot_mod
from league_site.game import modes
from league_site.game import workdir as workdir_mod
from league_site.game.modes import LaunchMode, Rejection, TeamSpec
from league_site.game.runner import LeagueRunner, game_version
from league_site.matches.engine import GameEngine
from league_site.matches.models import AgentIdentity, Participant, ParticipantKind

#: Stable identifier for this engine — shared by every launch mode; the
#: mode itself lives in ``state["mode"]``, not in ``game_id`` (mirrors how
#: the game's own ``league/presets.py`` treats "mode" as data, never a
#: distinct game).
GAME_ID = "league-of-agents-grid"

#: Fallback house-side team model label for a bot team with no
#: :attr:`~league_site.game.modes.TeamSpec.bot_policy` — "bot:pass" means
#: "never stages orders; the adapter force-resolves with `match tick
#: --apply`" (see :meth:`GridLaneEngine.apply_turn`). A policy-driven bot
#: team's roster is labeled with its policy instead (e.g. ``bot:greedy``),
#: so the match record says which bot actually played (issue #9).
_BOT_MODEL_LABEL = "bot:pass"


class GridLaneEngine(GameEngine):
    """Subprocess adapter for the league-of-agents grid-lane game.

    One instance plays one :class:`~league_site.game.modes.LaunchMode`
    (``"solo-vs-bot"`` by default); construct a second instance for a
    different mode rather than branching inside this class — mode
    differences live entirely in the :mod:`league_site.game.modes` data.
    """

    def __init__(
        self,
        mode: str | LaunchMode = modes.SOLO_VS_BOT.name,
        *,
        runner: LeagueRunner | None = None,
        workdir_root: str | Path | None = None,
        scenario_id: str | None = None,
        seed: int | None = None,
    ) -> None:
        base_mode = mode if isinstance(mode, LaunchMode) else modes.get_mode(mode)
        self._mode = base_mode.with_overrides(scenario_id=scenario_id, seed=seed)
        self._runner = runner if runner is not None else LeagueRunner()
        self._workdir_root = str(workdir_root) if workdir_root is not None else None

    @property
    def game_id(self) -> str:
        return GAME_ID

    @property
    def mode(self) -> LaunchMode:
        """The :class:`~league_site.game.modes.LaunchMode` this engine plays."""
        return self._mode

    # -- GameEngine interface ------------------------------------------------

    def initial_state(self, participants: Sequence[Participant]) -> dict[str, Any]:
        participants = list(participants)
        mode = self._mode
        participant_teams = modes.assign_participants(mode, participants)
        team_participants = _invert_participant_teams(participant_teams)

        match_id = f"m-{mode.name}-{uuid.uuid4().hex[:12]}"

        with self._workdir() as workdir:
            roles = self._scenario_roles(workdir)
            for team in mode.teams:
                self._register_team(workdir, team, roles, participants, participant_teams)

            new_args = [
                "match",
                "new",
                "--scenario",
                mode.scenario_id,
                "--mode",
                mode.game_mode,
                "--seed",
                str(mode.seed),
                "--id",
                match_id,
            ]
            for team in mode.teams:
                new_args += ["--team", team.team_id]
            for team in mode.teams:
                new_args += ["--driver", f"{team.team_id}:{team.driver_kind}"]
            new_args += ["--apply", "--json"]
            self._runner.run(new_args, cwd=workdir)

            version = game_version(self._runner, cwd=workdir)
            show = self._runner.run(["match", "show", match_id, "--json"], cwd=workdir)
            snapshot = workdir_mod.persist(workdir)

        return self._state_from_show(
            base={
                "game_id": self.game_id,
                "mode": mode.name,
                "match_id": match_id,
                "scenario_id": mode.scenario_id,
                "seed": mode.seed,
                "game_version": version,
                "participant_teams": participant_teams,
                "team_participants": team_participants,
                "bot_policies": {
                    team.team_id: team.bot_policy for team in bot_mod.driven_bot_teams(mode)
                },
            },
            show=show,
            snapshot=snapshot,
            platform_rejections=[],
        )

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        # Prefer this engine's own (possibly custom/unregistered) mode when
        # it is the one the state was created under; fall back to the
        # registry so a state rehydrated into a freshly-registered engine
        # still resolves (the production engine-registry path).
        mode = self._mode if self._mode.name == state["mode"] else modes.get_mode(state["mode"])
        team_id = state["participant_teams"].get(participant_id)
        if team_id is None:
            raise ValueError(
                f"participant {participant_id!r} does not control any team in match "
                f"{state['match_id']!r}"
            )
        if not isinstance(action, Mapping):
            raise TypeError(
                "apply_turn(action=...) must be a JSON-object-shaped mapping of league "
                "orders, e.g. {'actions': [...]}"
            )
        trimmed_orders, platform_rejections = modes.enforce_action_cap(mode, team_id, action)

        with self._workdir(state["snapshot"]) as workdir:
            act_response = self._runner.run(
                [
                    "match",
                    "act",
                    state["match_id"],
                    "--team",
                    team_id,
                    "--orders-json",
                    json.dumps(trimmed_orders, sort_keys=True),
                    "--apply",
                    "--json",
                ],
                cwd=workdir,
            )
            # Bot/house teams never stage orders on their own (driver kinds
            # are audit labels, not gates — docs/game-integration.md); once
            # the participant side(s) have staged, the platform plays the
            # bot side before the turn resolves (issue #9).
            if not act_response.get("resolves_turn") and mode.bot_team_ids:
                self._drive_bot_teams(workdir, mode, state)
            show = self._runner.run(["match", "show", state["match_id"], "--json"], cwd=workdir)
            snapshot = workdir_mod.persist(workdir)

        return self._state_from_show(
            base=state,
            show=show,
            snapshot=snapshot,
            platform_rejections=platform_rejections,
        )

    def is_over(self, state: dict[str, Any]) -> bool:
        return state.get("status") == "finished"

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        """``participant_id -> outcome.total`` for their team — the hard score
        :meth:`~league_site.matches.match.Match.complete` ranks on.

        Teams with no registered participant (the solo-vs-bot house) are
        included under their bare team id: leaving them out let a solo
        player who lost 0-21 be crowned sole leader of a one-entry score
        map (live-prod finding). Participant ids are namespaced
        (``agent:...``/``human:...``), so a bare team id can never collide
        with one; consumers treat a non-participant winner as "no
        participant gets the winner chip" (see
        :mod:`league_site.viewer.render`) and rated flows never involve
        house teams.
        """
        report = self._match_score(state)
        participant_teams: dict[str, str] = dict(state["participant_teams"])
        scores = {
            participant_id: float(report["outcome"][team_id]["total"])
            for participant_id, team_id in participant_teams.items()
        }
        covered_teams = set(participant_teams.values())
        for team_id, outcome in report["outcome"].items():
            if team_id not in covered_teams:
                scores[team_id] = float(outcome["total"])
        return scores

    # -- graded quality axes (out-of-band; see league_site.datasets.export) --

    def quality_axes(self, state: dict[str, Any]) -> dict[str, dict[str, float]]:
        """``participant_id -> {axis_name: numeric grade}``.

        Feeds :func:`league_site.datasets.export.export_matches`'s
        ``quality_axes`` parameter, which that module's own docstring
        describes as supplied "out of band" — never through
        :meth:`score`'s ``dict[str, float]`` return, which
        :class:`~league_site.matches.match.Match` relies on staying exactly
        that shape (``max(scores, key=scores.get)`` for the match winner).
        """
        score_report = self._match_score(state)
        probe_report = self._match_probe(state)
        units_section = score_report.get("units", {})
        units = units_section.get("units", {})
        mvp_unit_id = (units_section.get("mvp") or {}).get("unit_id")
        lvp_unit_id = (units_section.get("lvp") or {}).get("unit_id")
        probe_teams = probe_report.get("teams", {})

        axes: dict[str, dict[str, float]] = {}
        for participant_id, team_id in state["participant_teams"].items():
            team_unit_ids = {uid for uid, unit in units.items() if unit.get("team_id") == team_id}
            axes[participant_id] = {
                "cooperation_score": float(score_report["cooperation"][team_id]["score"]),
                "mvp": 1.0 if mvp_unit_id in team_unit_ids else 0.0,
                "lvp": 1.0 if lvp_unit_id in team_unit_ids else 0.0,
                "span_of_control_score": float(probe_teams.get(team_id, {}).get("score", 0.0)),
            }
        return axes

    # -- internals -------------------------------------------------------------

    def _drive_bot_teams(self, workdir: Path, mode: LaunchMode, state: Mapping[str, Any]) -> None:
        """Play the house side of the turn being processed (issue #9).

        Bot teams with a declared :attr:`~league_site.game.modes.TeamSpec.
        bot_policy` are driven with the *game's own* bot policy via
        ``league harness run``: the config written here names the existing
        match, so the harness resumes it and acts exactly the configured
        bot teams for one round (``max_rounds: 1``) — and staging the last
        team auto-resolves the turn per the game's own rule. Any bot team
        *without* a policy is deliberately passive: it stays unstaged, and
        the turn is force-resolved with ``match tick --apply`` (that team
        simply holds) — the pre-#9 behavior, kept only for that case. The
        config file lives at the workdir root, outside ``.league/``, so it
        never leaks into the persisted snapshot.
        """
        driven = bot_mod.driven_bot_teams(mode)
        if driven:
            config = bot_mod.harness_config(
                mode, match_id=state["match_id"], scenario_id=state["scenario_id"]
            )
            config_path = workdir / bot_mod.HARNESS_CONFIG_FILENAME
            config_path.write_text(json.dumps(config, sort_keys=True), encoding="utf-8")
            self._runner.run(
                ["harness", "run", "--config", str(config_path), "--apply", "--json"],
                cwd=workdir,
            )
        if len(driven) < len(mode.bot_team_ids):
            self._runner.run(["match", "tick", state["match_id"], "--apply", "--json"], cwd=workdir)

    @contextlib.contextmanager
    def _workdir(self, snapshot: workdir_mod.Snapshot | None = None) -> Iterator[Path]:
        """A fresh, isolated match workdir — hydrated from ``snapshot`` if
        given, always removed on exit. Every league CLI call in this class
        runs inside one of these (the "hydrate before, persist after every
        call" contract from ``docs/game-integration.md``)."""
        tmp = tempfile.mkdtemp(prefix="league-site-game-", dir=self._workdir_root)
        try:
            path = Path(tmp)
            if snapshot:
                workdir_mod.hydrate(path, snapshot)
            yield path
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def _scenario_roles(self, workdir: Path) -> list[str]:
        payload = self._runner.run(["arena", "show", self._mode.scenario_id, "--json"], cwd=workdir)
        return sorted(payload["roles"])

    def _register_team(
        self,
        workdir: Path,
        team: TeamSpec,
        roles: Sequence[str],
        participants: Sequence[Participant],
        participant_teams: Mapping[str, str],
    ) -> None:
        label = _team_label(team, participants, participant_teams)
        args = ["team", "register", team.team_id, "--name", team.team_id]
        for role in roles:
            args += ["--agent", f"{team.team_id}-{role}:{label}:{role}"]
        args += ["--apply", "--json"]
        self._runner.run(args, cwd=workdir)

    def _match_score(self, state: Mapping[str, Any]) -> dict[str, Any]:
        with self._workdir(state["snapshot"]) as workdir:
            return self._runner.run(["match", "score", state["match_id"], "--json"], cwd=workdir)

    def _match_probe(self, state: Mapping[str, Any]) -> dict[str, Any]:
        with self._workdir(state["snapshot"]) as workdir:
            return self._runner.run(["match", "probe", state["match_id"], "--json"], cwd=workdir)

    def _state_from_show(
        self,
        *,
        base: Mapping[str, Any],
        show: Mapping[str, Any],
        snapshot: workdir_mod.Snapshot,
        platform_rejections: list[Rejection],
    ) -> dict[str, Any]:
        game_state = show["state"]
        state = dict(base)
        state.update(
            {
                "snapshot": dict(snapshot),
                "status": game_state["status"],
                "turn": game_state["turn"],
                "turn_limit": game_state["turn_limit"],
                "winner": game_state["winner"],
                "legal_actions": show.get("legal_actions", {}),
                "last_turn_rejections": list(show.get("last_turn_rejections", [])),
                "last_turn_platform_rejections": [r.to_dict() for r in platform_rejections],
                "staged_teams": list(show.get("staged_teams", [])),
            }
        )
        return state


def _invert_participant_teams(participant_teams: Mapping[str, str]) -> dict[str, list[str]]:
    team_participants: dict[str, list[str]] = {}
    for participant_id, team_id in participant_teams.items():
        team_participants.setdefault(team_id, []).append(participant_id)
    return team_participants


def _team_label(
    team: TeamSpec, participants: Sequence[Participant], participant_teams: Mapping[str, str]
) -> str:
    """A descriptive, deterministic roster model label for ``team``.

    Purely cosmetic/audit metadata (the game's own ``AgentSlot.model``
    field, echoed straight into ``state.teams[*].agents[*].model`` on every
    ``match show``) — never consumed by engine logic. A bot team is labeled
    with the game bot policy that plays it (``bot_policy``, e.g.
    ``bot:greedy`` — the game's ``--agent id:model:role`` spec keeps the
    model's own colons), or the fixed :data:`_BOT_MODEL_LABEL` when it has
    none; a participant-controlled team's label joins its participant(s)'
    model (agents) or display name (humans), "+"-joined for a shared team
    (coop-2).
    """
    if team.is_bot:
        return team.bot_policy or _BOT_MODEL_LABEL
    labels = [
        _participant_label(p)
        for p in participants
        if participant_teams.get(p.participant_id) == team.team_id
    ]
    return "+".join(labels) or "unknown"


def _participant_label(participant: Participant) -> str:
    if participant.kind is ParticipantKind.AGENT and isinstance(
        participant.agent_identity, AgentIdentity
    ):
        return participant.agent_identity.model
    return (participant.display_name or "human").replace(" ", "_")


__all__ = ["GridLaneEngine", "GAME_ID"]
