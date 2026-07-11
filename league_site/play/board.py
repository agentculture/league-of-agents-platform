"""Legal orders + the staged plan → board overlay: the play interaction layer.

:func:`build_overlay` bridges the play surface's per-unit
:class:`~league_site.play.actions.OrderChoice` set (already narrowed to the
signed-in participant's own units — see
:func:`~league_site.play.actions.unit_orders`) onto the shared board
renderer's :class:`~league_site.viewer.board.BoardOverlay`, so the board
itself becomes the move-*planning* interface:

* **Pick a unit.** Every un-planned unit with at least one legal order gets
  a selection href (``GET <base_path>?...&unit=<id>``).
* **Pick its order.** With a unit selected, each of its orders becomes one
  :class:`~league_site.viewer.board.CellControl` — a ``move`` lands on its
  destination cell, verbs without a destination (``gather`` / ``deliver`` /
  ``hold``) land on the unit's *own* cell (two verbs can share a cell; the
  renderer disambiguates as stacked verb pills). Each control's ``href``
  *stages* the order: a GET back to the play view with the order appended
  to the URL-carried plan — and the next un-planned unit pre-selected, so
  planning a full turn is one tap per decision.
* **Change your mind.** A planned unit renders in the staged treatment and
  links to the same view with its order removed (and itself re-selected);
  its planned order ghosts onto the target cell as a
  :class:`~league_site.viewer.board.StagedMark`.

Every control is an idempotent GET — nothing on the board commits anything
(a double-tap re-stages the same order: harmless). The one commit is the
End-turn form the play panel renders (:mod:`league_site.play.render`),
which POSTs the whole plan; the server re-validates every order against
the *current* legal actions before any engine sees it
(:func:`league_site.play.actions.match_order`) — the URL plan is a
convenience layer, never an authority.

Only grid-shaped states produce orders at all
(:func:`~league_site.play.actions.unit_orders`); with no order naming a
unit that is actually on the board, :func:`build_overlay` yields ``None``
and the play view falls back to the select-based form alone. A selected or
staged unit that is no longer valid (the turn moved on under a stale URL)
degrades silently — the fresh board is the answer.
"""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import urlencode

from league_site.play.actions import OrderChoice
from league_site.viewer.board import BoardModel, BoardOverlay, CellControl, StagedMark

__all__ = ["build_overlay", "play_view_href"]

#: The fragment every board href carries so the reload lands back on the
#: board instead of the top of the page (the board wrap renders with this
#: id — see :func:`league_site.viewer.board.render_board`'s caller).
BOARD_FRAGMENT = "#board"


def play_view_href(
    base_path: str,
    staged: Sequence[OrderChoice],
    *,
    unit: str | None = None,
    fragment: bool = True,
) -> str:
    """The play-view URL carrying *staged* (and optionally a selected *unit*).

    The single builder every staging/selection/re-stage href goes through,
    so the URL contract (repeated ``staged=`` params, one optional
    ``unit=``, the ``#board`` fragment) lives in exactly one place.
    """
    params = [("staged", choice.value) for choice in staged]
    if unit is not None:
        params.append(("unit", unit))
    query = urlencode(params)
    suffix = BOARD_FRAGMENT if fragment else ""
    return f"{base_path}?{query}{suffix}" if query else f"{base_path}{suffix}"


def build_overlay(
    model: BoardModel,
    orders: Sequence[OrderChoice],
    *,
    staged: Sequence[OrderChoice],
    selected_unit: str | None,
    base_path: str,
) -> BoardOverlay | None:
    """The :class:`BoardOverlay` for *orders* + the *staged* plan, or ``None``.

    ``None`` means the board cannot host the interaction (no orders, or no
    order naming a unit that is actually on the board) — the caller then
    renders the static board plus the fallback form, exactly like a
    spectate view with a move list. *staged* must already be validated and
    deduped (one order per unit — :mod:`league_site.play.wsgi` does this
    from the raw query string).
    """
    unit_cells = {unit.unit_id: (unit.x, unit.y) for unit in model.units}
    per_unit: dict[str, list[OrderChoice]] = {}
    for choice in orders:
        if choice.unit_id in unit_cells:
            per_unit.setdefault(choice.unit_id, []).append(choice)
    if not per_unit:
        return None

    staged_by_unit = {choice.unit_id: choice for choice in staged if choice.unit_id in per_unit}
    plan = [choice for choice in staged if choice.unit_id in staged_by_unit]
    unstaged = [unit_id for unit_id in per_unit if unit_id not in staged_by_unit]

    select_hrefs = {unit_id: play_view_href(base_path, plan, unit=unit_id) for unit_id in unstaged}
    staged_units = {
        unit_id: play_view_href(
            base_path,
            [choice for choice in plan if choice.unit_id != unit_id],
            unit=unit_id,
        )
        for unit_id in staged_by_unit
    }
    staged_marks = tuple(
        StagedMark(x=cell[0], y=cell[1], verb=choice.verb, unit_id=choice.unit_id)
        for choice in plan
        for cell in (_target_cell(choice, unit_cells),)
        if cell is not None
    )

    selected = selected_unit if selected_unit in unstaged else None
    controls: tuple[CellControl, ...] = ()
    if selected is not None:
        controls = tuple(
            CellControl(
                x=cell[0],
                y=cell[1],
                verb=choice.verb,
                label=choice.label,
                href=play_view_href(
                    base_path,
                    plan + [choice],
                    unit=_next_unstaged(unstaged, selected),
                ),
            )
            for choice in per_unit[selected]
            for cell in (_target_cell(choice, unit_cells),)
            if cell is not None
        )
    return BoardOverlay(
        select_hrefs=select_hrefs,
        selected_unit=selected,
        controls=controls,
        staged_units=staged_units,
        staged_marks=staged_marks,
    )


def _target_cell(
    choice: OrderChoice, unit_cells: dict[str, tuple[int, int]]
) -> tuple[int, int] | None:
    if choice.target is not None:
        return choice.target
    return unit_cells.get(choice.unit_id)


def _next_unstaged(unstaged: Sequence[str], staging_now: str) -> str | None:
    """The unit to pre-select once *staging_now* is planned — the next one
    still needing an order, so a full turn is one tap per decision."""
    for unit_id in unstaged:
        if unit_id != staging_now:
            return unit_id
    return None
