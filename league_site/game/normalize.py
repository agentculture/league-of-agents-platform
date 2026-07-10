"""Bridge between :class:`~league_site.game.adapter.GridLaneEngine` state and BYOK.

:mod:`league_site.byok.runner` validates a hosted agent's proposed orders
against a flat list of ``{"unit": <unit id>, "action": <opaque>}`` pairs —
see that module's docstring. The real grid-lane game's own
``legal_actions`` (mirrored onto :class:`~league_site.game.adapter.
GridLaneEngine` state verbatim from ``league match show <id> --json`` —
see ``docs/game-integration.md``) is shaped differently: a dict keyed by
unit id, each value a per-unit legality summary::

    {
        "<unit_id>": {
            "move": [[x, y], ...],   # legal destination cells
            "gather": bool,          # gather is a legal action right now
            "deliver": bool,         # deliver is a legal action right now
            "hold": true,            # hold is always legal
            "can_gather": bool,      # unit-type capability (not per-turn legality)
            "can_capture": bool,     # unit-type capability (not per-turn legality)
        },
        ...
    }

:func:`legal_actions_to_pairs` expands that per-unit summary into the flat
pair list :func:`~league_site.byok.runner.run_turn` needs, one pair per
legal (unit, action) combination — one pair per legal ``move`` destination,
plus one pair each for ``gather``/``deliver``/``hold`` when the unit's
summary marks it legal. ``can_gather``/``can_capture`` are unit-type
capability flags, not this-turn legality, and are deliberately not expanded
into pairs (a unit that ``can_gather`` but isn't currently standing on a
resource has ``"gather": false`` this turn — the flag this function reads).

Every pair's ``action`` value is shaped exactly like one entry of the
game's own ``orders-json`` ``"actions"`` list (``{"unit_id", "action",
"to"}`` — see :mod:`league_site.game.modes`'s ``enforce_action_cap`` and
``docs/game-integration.md``), so a validated
:class:`~league_site.byok.runner.TurnDecision`'s ``orders`` entries' ``
["action"]`` values can be collected directly into
``{"actions": [...]}`` and handed to
:meth:`~league_site.game.adapter.GridLaneEngine.apply_turn` unchanged.

:func:`build_match_view` builds the whole
:class:`~league_site.byok.runner.MatchView` from one
:class:`~league_site.game.adapter.GridLaneEngine` state dict, so a caller
driving a hosted agent's turn only ever touches this one function plus
:func:`~league_site.byok.runner.run_turn`.

Score-endpoint bridge (platform issue #10)
-------------------------------------------
The second half of this module bridges ``GridLaneEngine`` state the other
direction: toward the score API (:mod:`league_site.api.wsgi`'s
``_handle_score``), rather than toward BYOK. ``GridLaneEngine.score()``
(:mod:`league_site.game.adapter`) only ever returns
``participant_id -> outcome.total`` — the one number
:class:`~league_site.matches.match.Match` needs to rank a winner — and
never persists the game's own richer ``league match score --json`` report
anywhere, so recomputing it later (e.g. for a completed match's ``GET
.../score`` response) means re-running the CLI fresh against that match's
persisted snapshot. :func:`fetch_score_report` does exactly that, using the
same public :class:`~league_site.game.runner.LeagueRunner` and
:mod:`league_site.game.workdir` building blocks
:class:`~league_site.game.adapter.GridLaneEngine` itself uses internally —
this module never reaches into that class's private methods.
:func:`normalize_outcome` and :func:`normalize_quality_axes` turn that raw
report (plus ``GridLaneEngine.quality_axes()``'s own already-public return
value) into the score API's stable, additive response keys — sorted keys,
explicit ``int``/``float`` coercion, so two calls against the same
finished match always render byte-identical JSON. :func:`score_breakdown`
is the one entry point the score handler calls: it duck-types *any*
:class:`~league_site.matches.engine.GameEngine` (only a
``GridLaneEngine``-shaped engine exposes ``quality_axes`` at all — the
built-in stub engine does not), and degrades to ``None`` — never raising —
for a non-grid engine, a non-grid-shaped state, or a ``league`` CLI that
can't be reached right now, so the score endpoint's pre-existing fields
stay available even when this additive data can't be computed.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from league_site.byok.runner import MatchView
from league_site.game.runner import LeagueCliError, LeagueRunner, LeagueRunnerError
from league_site.game.workdir import hydrate as hydrate_workdir

__all__ = [
    "legal_actions_to_pairs",
    "build_match_view",
    "fetch_score_report",
    "normalize_outcome",
    "normalize_quality_axes",
    "score_breakdown",
]

#: Fields :func:`normalize_outcome` extracts from each team's
#: ``report["outcome"][team_id]`` entry — the game's own per-team hard-score
#: breakdown (``docs/game-integration.md``: "outcome (per-team integer
#: points: missions + control + resources)"). Fixed, sorted order so the
#: response is deterministic regardless of what the CLI's own dict ordering
#: happens to be.
_OUTCOME_FIELDS: tuple[str, ...] = ("total", "missions", "control", "resources")


def legal_actions_to_pairs(
    legal_actions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Expand a ``GridLaneEngine``-shaped ``legal_actions`` dict into BYOK's flat pair list.

    Returns ``[{"unit": <unit id>, "action": {...}}, ...]`` — see the module
    docstring for the exact expansion rules. Deterministic: units are
    visited in sorted order, ``move`` destinations in sorted ``(x, y)``
    order, then ``gather``/``deliver``/``hold`` (in that fixed order,
    whichever are legal) — so calling this twice on the same input always
    produces byte-identical output, and diffing two calls' output is a
    meaningful way to see what changed.
    """
    pairs: list[dict[str, Any]] = []
    for unit_id in sorted(legal_actions):
        spec = legal_actions[unit_id]
        for cell in sorted((list(c) for c in (spec.get("move") or [])), key=tuple):
            x, y = cell
            pairs.append(
                {
                    "unit": unit_id,
                    "action": {"unit_id": unit_id, "action": "move", "to": [x, y]},
                }
            )
        for action_name in ("gather", "deliver", "hold"):
            if spec.get(action_name):
                pairs.append(
                    {"unit": unit_id, "action": {"unit_id": unit_id, "action": action_name}}
                )
    return pairs


def build_match_view(state: Mapping[str, Any], *, team: str | None = None) -> MatchView:
    """Build a :class:`~league_site.byok.runner.MatchView` from a ``GridLaneEngine`` state dict.

    ``state`` is passed through as :attr:`MatchView.state` verbatim (opaque,
    per that class's docstring); ``legal_actions`` is
    :func:`legal_actions_to_pairs` applied to ``state["legal_actions"]``;
    ``last_turn_rejections`` combines the game's own refusals
    (``state["last_turn_rejections"]``) with the platform's own mode-fairness
    refusals (``state["last_turn_platform_rejections"]`` — see
    :mod:`league_site.game.modes`), game-first, so a hosted agent sees both
    reasons its previous turn's orders may have been trimmed or rejected.

    ``team``, if given, is set on the returned view (see
    :attr:`MatchView.team`) and also narrows ``legal_actions`` to units whose
    id starts with ``f"{team}-"`` — the engine-generated unit id convention
    documented in :mod:`league_site.game.adapter`
    (``<team_id>-u<N>``) — so a caller driving one team's hosted agent can
    build that team's own view without hand-filtering. Omitting ``team``
    (the default) keeps every unit's legal actions in view, e.g. for an
    observer/audit use.
    """
    legal_actions = state.get("legal_actions") or {}
    pairs = legal_actions_to_pairs(legal_actions)
    if team is not None:
        prefix = f"{team}-"
        pairs = [pair for pair in pairs if str(pair["unit"]).startswith(prefix)]

    last_turn_rejections = [
        *state.get("last_turn_rejections", []),
        *state.get("last_turn_platform_rejections", []),
    ]

    return MatchView(
        state=dict(state),
        legal_actions=pairs,
        last_turn_rejections=last_turn_rejections,
        team=team,
    )


# --- score-endpoint bridge (platform issue #10) ------------------------------


def fetch_score_report(
    state: Mapping[str, Any],
    *,
    runner: Any | None = None,
    workdir_root: str | None = None,
) -> dict[str, Any] | None:
    """Run ``league match score <id> --json`` fresh against ``state``'s snapshot.

    Returns ``None`` without touching a subprocess if ``state`` doesn't
    carry the ``GridLaneEngine`` state shape this needs (a ``"snapshot"``
    and a truthy ``"match_id"`` — see :mod:`league_site.game.adapter`'s
    module docstring for that state contract), e.g. a non-grid engine's
    opaque state. An empty-but-present ``snapshot`` (``{}``, a legal
    starting point per :func:`~league_site.game.workdir.hydrate`'s own
    docstring) is not treated as missing — only an absent key is.

    Otherwise, hydrates a fresh, isolated scratch workdir from
    ``state["snapshot"]`` (:func:`~league_site.game.workdir.hydrate`), runs
    ``match score --json`` there via ``runner`` (a real
    :class:`~league_site.game.runner.LeagueRunner` by default; tests inject
    a scripted fake — see ``tests/test_game_adapter_fake.py``'s
    ``ScriptedRunner``), and always tears the scratch dir down again before
    returning, mirroring ``GridLaneEngine``'s own hydrate/run/teardown cycle
    (:mod:`league_site.game.adapter`) without reaching into that class's
    private methods.
    """
    if not isinstance(state, Mapping) or "snapshot" not in state:
        return None
    snapshot = state["snapshot"]
    match_id = state.get("match_id")
    if not match_id:
        return None

    active_runner = runner if runner is not None else LeagueRunner()
    scratch = tempfile.mkdtemp(prefix="league-site-score-", dir=workdir_root)
    try:
        workdir = Path(scratch)
        hydrate_workdir(workdir, snapshot)
        return active_runner.run(["match", "score", str(match_id), "--json"], cwd=workdir)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def normalize_outcome(report: Mapping[str, Any]) -> dict[str, dict[str, int]]:
    """``team_id -> {"total", "missions", "control", "resources"}`` (all
    ``int``), from a raw ``league match score --json`` report's
    ``"outcome"`` section (see :data:`_OUTCOME_FIELDS`). Team ids are sorted
    for determinism; a team missing one of the four fields defaults it to
    ``0`` rather than dropping the key, so every team entry always has the
    same fixed shape.
    """
    outcome = report.get("outcome") or {}
    return {
        team_id: {field: int((breakdown or {}).get(field, 0) or 0) for field in _OUTCOME_FIELDS}
        for team_id, breakdown in sorted(outcome.items())
    }


def normalize_quality_axes(
    axes: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float]]:
    """``participant_id -> {axis_name: float grade}``, sorted and float-coerced.

    ``axes`` is :meth:`~league_site.game.adapter.GridLaneEngine.quality_axes`'s
    own return value, passed through unchanged except for stable key
    ordering (sorted participant ids, sorted axis names) and an explicit
    ``float()`` on every grade — defensive against a caller handing this
    JSON-decoded numbers (``int`` where a whole grade happens to land, e.g.
    ``"mvp": 1``) rather than already-``float`` values.
    """
    return {
        participant_id: {axis: float(value) for axis, value in sorted(grades.items())}
        for participant_id, grades in sorted(axes.items())
    }


def score_breakdown(
    engine: Any,
    state: Any,
    *,
    runner: Any | None = None,
    workdir_root: str | None = None,
) -> dict[str, Any] | None:
    """``{"outcome": ..., "quality_axes": ...}`` for a grid-lane-backed match.

    Duck-types ``engine`` for a ``quality_axes`` method (only
    :class:`~league_site.game.adapter.GridLaneEngine` has one; the built-in
    stub engine and any other plain :class:`~league_site.matches.engine.
    GameEngine` do not) — returns ``None`` immediately if it's absent, the
    score endpoint's "stub-engine matches simply omit the extra keys"
    contract (platform issue #10). Also returns ``None`` (rather than
    raising) if ``state`` isn't grid-shaped (see :func:`fetch_score_report`)
    or if the ``league`` CLI can't be reached right now
    (:class:`~league_site.game.runner.LeagueRunnerError` /
    :class:`~league_site.game.runner.LeagueCliError`) — either way, the
    score endpoint's pre-existing fields stay available even when this
    additive data can't be computed.
    """
    quality_axes_fn = getattr(engine, "quality_axes", None)
    if quality_axes_fn is None or not isinstance(state, Mapping):
        return None
    try:
        report = fetch_score_report(state, runner=runner, workdir_root=workdir_root)
        if report is None:
            return None
        axes = quality_axes_fn(state)
    except (LeagueRunnerError, LeagueCliError):
        return None
    return {
        "outcome": normalize_outcome(report),
        "quality_axes": normalize_quality_axes(axes),
    }
