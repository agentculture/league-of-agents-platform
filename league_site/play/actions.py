"""Turn interpretation for the play surface: legal actions → form choices.

The match API passes each engine's ``legal_actions`` through opaquely
(``GET /api/v1/matches/<id>`` — see :mod:`league_site.api.wsgi`), which is
fine for agents but not for a browser form: the play page must render one
*submittable* choice per legal action. Two published shapes are understood:

* **Grid-shaped mapping** (:class:`~league_site.game.adapter.GridLaneEngine`
  — ``unit_id -> per-unit legality summary``): expanded into one
  :class:`OrderChoice` per legal ``(unit, action)`` pair via
  :func:`league_site.game.normalize.legal_actions_to_pairs` (the same
  expansion BYOK validates hosted-agent orders against), narrowed to the
  participant's own team's units. Each choice carries a *single unit
  order*; the play surface stages one order per unit and composes the
  whole turn with :func:`compose_turn` — the ``{"actions": [...]}``
  envelope ``GridLaneEngine.apply_turn`` accepts, every side's units in
  one resolved turn (2026-07-11 feedback round: the old one-order-per-POST
  flow left the human playing one unit against the bot's whole roster).
* **Sequence of mappings**: each entry *is* a whole submittable action and
  becomes one :class:`ActionChoice`, submitted verbatim. Non-mapping
  entries (e.g. the built-in stub engine's bare point values, legal
  *parameters* rather than actions) are not submittable and yield no
  choice.

Anything else — no ``legal_actions``, an unrecognized shape — yields no
choices, and the play view renders without a form rather than offering
buttons that could only ever 400.

:func:`match_choice` / :func:`match_order` are the server-side gates a
submitted form value must pass: the strings a browser sends are *never*
trusted — each must parse as JSON and equal one of the choices computed
fresh from the match's current state, or the submission is refused before
any engine sees it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ActionChoice:
    """One submittable legal action: a human-readable label + the action payload."""

    label: str
    action: Any

    @property
    def value(self) -> str:
        """The form value for this choice — canonical JSON of the action."""
        return json.dumps(self.action, sort_keys=True, ensure_ascii=False)


@dataclass(frozen=True)
class OrderChoice:
    """One legal *single-unit* order on a grid board.

    ``order`` is the raw order mapping (``{"unit_id", "action"[, "to"]}``)
    exactly as one entry of the game's ``{"actions": [...]}`` envelope;
    ``target`` is its destination cell for a ``move``, ``None`` for the
    in-place verbs (gather / deliver / hold). The play surface stages at
    most one of these per unit, then :func:`compose_turn` builds the whole
    turn.
    """

    unit_id: str
    verb: str
    target: tuple[int, int] | None
    order: Mapping[str, Any]
    label: str

    @property
    def value(self) -> str:
        """The form/URL value for this order — canonical JSON of the order."""
        return json.dumps(self.order, sort_keys=True, ensure_ascii=False)


def action_choices(state: Any, participant_id: str) -> tuple[ActionChoice, ...]:
    """The submittable choices *participant_id* has in *state*, possibly empty.

    See the module docstring for the two understood ``legal_actions``
    shapes; anything else yields ``()``.
    """
    if not isinstance(state, Mapping):
        return ()
    legal_actions = state.get("legal_actions")
    if isinstance(legal_actions, Mapping):
        return _grid_choices(state, legal_actions, participant_id)
    if isinstance(legal_actions, Sequence) and not isinstance(legal_actions, (str, bytes)):
        return tuple(
            ActionChoice(label=_verbatim_label(entry), action=entry)
            for entry in legal_actions
            if isinstance(entry, Mapping)
        )
    return ()


def unit_orders(state: Any, participant_id: str) -> tuple[OrderChoice, ...]:
    """The single-unit :class:`OrderChoice`\\ s *participant_id* has in *state*.

    Non-empty exactly when the state is grid-shaped (a mapping
    ``legal_actions`` plus a team for this participant); every other shape
    yields ``()`` and the caller falls back to :func:`action_choices`'s
    whole-action flow.
    """
    if not isinstance(state, Mapping):
        return ()
    legal_actions = state.get("legal_actions")
    if not isinstance(legal_actions, Mapping):
        return ()
    teams = state.get("participant_teams")
    team_id = teams.get(participant_id) if isinstance(teams, Mapping) else None
    if not isinstance(team_id, str) or not team_id:
        return ()

    from league_site.game.normalize import legal_actions_to_pairs

    prefix = f"{team_id}-"
    orders = []
    for pair in legal_actions_to_pairs(legal_actions):
        unit_id = str(pair["unit"])
        if not unit_id.startswith(prefix):
            continue
        order = pair["action"]
        if not isinstance(order, Mapping):
            continue
        verb = str(order.get("action") or "act")
        target: tuple[int, int] | None = None
        destination = order.get("to")
        if isinstance(destination, Sequence) and len(destination) == 2:
            target = (destination[0], destination[1])
        orders.append(
            OrderChoice(
                unit_id=unit_id,
                verb=verb,
                target=target,
                order=order,
                label=_order_label(unit_id, order),
            )
        )
    return tuple(orders)


def match_order(orders: Sequence[OrderChoice], submitted: Any) -> OrderChoice | None:
    """The :class:`OrderChoice` whose order equals the *submitted* value, or ``None``.

    Same refusal contract as :func:`match_choice`: a missing value, a value
    that isn't JSON, and a well-formed order that simply isn't among the
    current *orders* all come back ``None`` — refused before any engine
    runs. Comparison is by parsed-value equality, so key order never
    matters.
    """
    if not isinstance(submitted, str):
        return None
    try:
        order = json.loads(submitted)
    except json.JSONDecodeError:
        return None
    for choice in orders:
        if choice.order == order:
            return choice
    return None


def compose_turn(orders: Sequence[OrderChoice]) -> dict[str, Any]:
    """The full-turn ``{"actions": [...]}`` envelope for *orders* — exactly
    what ``GridLaneEngine.apply_turn`` accepts (and what an agent submits
    over the JSON API)."""
    return {"actions": [dict(choice.order) for choice in orders]}


def is_waiting(state: Any, participant_id: str) -> bool:
    """True when the live match is waiting on someone *other* than *participant_id*.

    Two state-shape heuristics, mirroring :func:`action_choices`:

    * turn-order shape (``participant_order`` + ``turn_index`` — the stub
      family): waiting unless the order says it's this participant's turn;
    * grid shape (``participant_teams`` + ``staged_teams``): waiting once
      this participant's team has already staged its orders for the turn
      being resolved.

    Unknown shapes are never "waiting" — the play view then offers whatever
    choices exist and lets the engine's own out-of-turn validation rule.
    """
    if not isinstance(state, Mapping):
        return False
    order = state.get("participant_order")
    if isinstance(order, Sequence) and not isinstance(order, (str, bytes)) and order:
        turn_index = state.get("turn_index")
        if isinstance(turn_index, int):
            return order[turn_index % len(order)] != participant_id
    teams = state.get("participant_teams")
    if isinstance(teams, Mapping):
        team_id = teams.get(participant_id)
        staged = state.get("staged_teams")
        if team_id is not None and isinstance(staged, Iterable) and not isinstance(staged, str):
            return team_id in staged
    return False


def match_choice(choices: Sequence[ActionChoice], submitted: Any) -> ActionChoice | None:
    """The choice whose action equals the *submitted* form value, or ``None``.

    ``None`` uniformly covers a missing value, a value that isn't JSON, and
    a well-formed action that simply isn't among the current *choices* —
    the caller refuses all three the same way, before any engine runs.
    Comparison is by parsed-value equality, so key order never matters.
    """
    if not isinstance(submitted, str):
        return None
    try:
        action = json.loads(submitted)
    except json.JSONDecodeError:
        return None
    for choice in choices:
        if choice.action == action:
            return choice
    return None


def _grid_choices(
    state: Mapping[str, Any], legal_actions: Mapping[str, Any], participant_id: str
) -> tuple[ActionChoice, ...]:
    """Expand a grid-shaped ``legal_actions`` into this participant's choices.

    Imports :mod:`league_site.game.normalize` lazily — same cold-start
    discipline as :func:`league_site.api.wsgi._score_extras_view` and
    :mod:`league_site.api.registry`: importing any :mod:`league_site.game`
    submodule pulls in the whole package, a cost only worth paying when a
    grid match is actually being rendered (at which point the engine
    registry's own lazy adapter import has been paid anyway).
    """
    teams = state.get("participant_teams")
    team_id = teams.get(participant_id) if isinstance(teams, Mapping) else None
    if not isinstance(team_id, str) or not team_id:
        return ()

    from league_site.game.normalize import legal_actions_to_pairs

    prefix = f"{team_id}-"
    choices = []
    for pair in legal_actions_to_pairs(legal_actions):
        if not str(pair["unit"]).startswith(prefix):
            continue
        order = pair["action"]
        choices.append(
            ActionChoice(label=_order_label(pair["unit"], order), action={"actions": [order]})
        )
    return tuple(choices)


def _order_label(unit: Any, order: Mapping[str, Any]) -> str:
    verb = str(order.get("action", "act"))
    destination = order.get("to")
    if isinstance(destination, Sequence) and len(destination) == 2:
        return f"{unit} · {verb} → ({destination[0]}, {destination[1]})"
    return f"{unit} · {verb}"


def _verbatim_label(action: Mapping[str, Any]) -> str:
    if all(not isinstance(value, (Mapping, list, tuple)) for value in action.values()):
        return ", ".join(f"{key}: {value}" for key, value in action.items())
    return json.dumps(action, sort_keys=True, ensure_ascii=False)


__all__ = [
    "ActionChoice",
    "OrderChoice",
    "action_choices",
    "compose_turn",
    "is_waiting",
    "match_choice",
    "match_order",
    "unit_orders",
]
