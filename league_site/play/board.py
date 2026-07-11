"""Legal actions → board overlay: the play surface's interaction layer.

:func:`build_overlay` bridges the play surface's submittable
:class:`~league_site.play.actions.ActionChoice` set (already narrowed to the
signed-in participant's own units — see
:func:`~league_site.play.actions.action_choices`) onto the shared board
renderer's :class:`~league_site.viewer.board.BoardOverlay`, so the board
itself becomes the move interface:

* **Step 1 — pick a unit.** Every unit with at least one legal action gets a
  selection href (``GET <base_path>?unit=<id>`` — selection is idempotent
  and changes nothing server-side, so a plain link is the correct control).
* **Step 2 — pick a target.** With a unit selected, each of its choices
  becomes one :class:`~league_site.viewer.board.CellControl`: a ``move``
  lands on its destination cell, verbs without a destination (``gather`` /
  ``deliver`` / ``hold``) land on the unit's *own* cell — so two verbs can
  share a cell, which the renderer disambiguates as stacked, verb-labeled
  buttons. Every control's ``value`` is the choice's canonical JSON
  (:attr:`~league_site.play.actions.ActionChoice.value`), the exact string
  the turn handler re-validates against the *current* legal actions before
  any engine sees it — the overlay is a convenience layer, never an
  authority.

Only grid-shaped choices (the ``{"actions": [<order>]}`` envelope whose
single order carries a ``unit_id``) can be anchored to cells; any other
choice shape (e.g. the stub family's whole-action mappings) yields ``None``
and the play view falls back to the select-based form alone. A selected
unit that is no longer selectable (the turn moved on under a stale URL)
degrades to no selection rather than erroring — the fresh board is the
answer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import quote

from league_site.play.actions import ActionChoice
from league_site.viewer.board import BoardModel, BoardOverlay, CellControl

__all__ = ["build_overlay"]


def build_overlay(
    model: BoardModel,
    choices: Sequence[ActionChoice],
    *,
    selected_unit: str | None,
    base_path: str,
    form_action: str,
) -> BoardOverlay | None:
    """The :class:`BoardOverlay` for *choices* on *model*, or ``None``.

    ``None`` means the board cannot host the interaction (no choices, a
    non-grid choice shape, or no choice naming a unit that is actually on
    the board) — the caller then renders the static board plus the fallback
    form, exactly like a spectate view with a move list.
    """
    if not choices:
        return None
    unit_cells = {unit.unit_id: (unit.x, unit.y) for unit in model.units}

    per_unit: dict[str, list[tuple[str, tuple[int, int], str]]] = {}
    for choice in choices:
        order = _grid_order(choice.action)
        if order is None:
            return None
        unit_id, verb, destination = order
        if unit_id not in unit_cells:
            continue  # e.g. the unit just died; the fallback form still lists it
        cell = destination if destination is not None else unit_cells[unit_id]
        per_unit.setdefault(unit_id, []).append((verb, cell, choice.value))
    if not per_unit:
        return None

    select_hrefs = {unit_id: f"{base_path}?unit={quote(unit_id, safe='')}" for unit_id in per_unit}

    selected = selected_unit if selected_unit in per_unit else None
    controls: tuple[CellControl, ...] = ()
    if selected is not None:
        controls = tuple(
            CellControl(
                x=cell[0],
                y=cell[1],
                verb=verb,
                label=_control_label(selected, verb, cell),
                value=value,
            )
            for verb, cell, value in per_unit[selected]
        )
    return BoardOverlay(
        form_action=form_action,
        select_hrefs=select_hrefs,
        selected_unit=selected,
        controls=controls,
    )


def _grid_order(action: Any) -> tuple[str, str, tuple[int, int] | None] | None:
    """``(unit_id, verb, destination)`` for a grid-shaped choice, else ``None``.

    Grid-shaped is exactly what :func:`league_site.play.actions.action_choices`
    builds from a grid state: an ``{"actions": [<order>]}`` envelope whose
    single order is a mapping with a string ``unit_id`` and ``action``, plus
    an optional two-integer ``to`` destination.
    """
    if not isinstance(action, Mapping):
        return None
    orders = action.get("actions")
    if not isinstance(orders, Sequence) or isinstance(orders, (str, bytes)) or len(orders) != 1:
        return None
    order = orders[0]
    if not isinstance(order, Mapping):
        return None
    unit_id = order.get("unit_id")
    verb = order.get("action")
    if not isinstance(unit_id, str) or not unit_id or not isinstance(verb, str) or not verb:
        return None
    destination: tuple[int, int] | None = None
    if "to" in order:
        to = order["to"]
        if (
            not isinstance(to, Sequence)
            or isinstance(to, (str, bytes))
            or len(to) != 2
            or not all(isinstance(coord, int) and not isinstance(coord, bool) for coord in to)
        ):
            return None
        destination = (to[0], to[1])
    return unit_id, verb, destination


def _control_label(unit_id: str, verb: str, cell: tuple[int, int]) -> str:
    if verb == "move":
        return f"Move {unit_id} to ({cell[0]}, {cell[1]})"
    return f"{verb.capitalize()} — {unit_id} at ({cell[0]}, {cell[1]})"
