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
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from league_site.byok.runner import MatchView

__all__ = ["legal_actions_to_pairs", "build_match_view"]


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
