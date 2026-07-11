"""The match board: shared model + HTML rendering for spectate and play.

The grid-lane engine's state (:mod:`league_site.game.adapter`) mirrors the
game's own board projections — ``grid_width``/``grid_height``, ``units``
(with ``pos``), ``control_points``, ``resource_nodes``, ``missions`` —
straight from ``league match show --json``. :func:`build_board_model` turns
that opaque state into a render-ready :class:`BoardModel` (returning
``None`` for any state that isn't board-shaped: the stub family, a pre-board
persisted match, a hostile shape — callers then simply render no board), and
:func:`render_board` renders the model as a CSS-grid board whose visual
vocabulary is the hero scene's (:mod:`league_site.web.hero`): role-distinct
glyphs — scout (triangle), harvester (circle), defender (square) — with the
accent team solid and the other team ink-outlined, diamond resource nodes,
ringed control posts (dashed when neutral), and mission flags.

The interaction layer is opt-in and play-only
----------------------------------------------
``render_board(model)`` — the spectate viewer's call — emits **no links, no
forms, no buttons**: a purely presentational board (each piece carries its
identity via ``role="img"``/``aria-label``). The play surface passes a
:class:`BoardOverlay` (built by :mod:`league_site.play.board` from the
current legal actions) and only then does the board grow its two-step
interaction:

* every unit named in :attr:`BoardOverlay.select_hrefs` renders as a
  selection **link** (``GET ?unit=<id>`` — selection is idempotent, so a
  link is the correct control);
* the selected unit renders distinctly, and every
  :class:`CellControl` renders as a tiny per-cell **POST form** whose hidden
  ``action`` field carries the exact submittable payload — one full-cell
  button when a cell has a single action, a stack of verb-labeled buttons
  when several actions share one cell (disambiguation, e.g. gather vs hold
  on the unit's own square). The server re-validates every submission
  against the *current* legal actions regardless
  (:func:`league_site.play.actions.match_choice`); the payload in the form
  is a convenience, never trusted.

Escaping discipline matches the rest of the viewer: every engine-derived
string (unit ids, roles, marker ids, hrefs, action payloads, labels) passes
through :func:`html.escape` before interpolation.
"""

from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "BoardUnit",
    "BoardPost",
    "BoardResource",
    "BoardMission",
    "BoardModel",
    "CellControl",
    "BoardOverlay",
    "build_board_model",
    "render_board",
]


@dataclass(frozen=True)
class BoardUnit:
    """One living unit on the board."""

    unit_id: str
    team: str
    role: str
    x: int
    y: int
    carrying: int = 0


@dataclass(frozen=True)
class BoardPost:
    """One capturable control post; ``owner`` is a team id or ``None``."""

    marker_id: str
    x: int
    y: int
    owner: str | None = None


@dataclass(frozen=True)
class BoardResource:
    """One resource node with stock remaining."""

    marker_id: str
    x: int
    y: int
    remaining: int = 0


@dataclass(frozen=True)
class BoardMission:
    """One open mission site."""

    marker_id: str
    x: int
    y: int
    kind: str = "mission"


@dataclass(frozen=True)
class BoardModel:
    """Everything :func:`render_board` needs for one board."""

    width: int
    height: int
    units: tuple[BoardUnit, ...] = ()
    posts: tuple[BoardPost, ...] = ()
    resources: tuple[BoardResource, ...] = ()
    missions: tuple[BoardMission, ...] = ()


@dataclass(frozen=True)
class CellControl:
    """One submittable action anchored to a target cell (play-only).

    ``value`` is the exact form payload
    (:attr:`league_site.play.actions.ActionChoice.value` — canonical JSON);
    ``label`` is the human-readable accessible name; ``verb`` is the short
    action word shown on stacked disambiguation buttons.
    """

    x: int
    y: int
    verb: str
    label: str
    value: str


@dataclass(frozen=True)
class BoardOverlay:
    """The play surface's interaction layer over a static board.

    ``select_hrefs`` maps each *selectable* unit id to its ``?unit=`` GET
    href; ``selected_unit`` (already validated by the builder —
    :func:`league_site.play.board.build_overlay`) names the unit whose
    ``controls`` are on the board; ``form_action`` is the turns endpoint
    every cell control POSTs to.
    """

    form_action: str
    select_hrefs: Mapping[str, str] = field(default_factory=dict)
    selected_unit: str | None = None
    controls: tuple[CellControl, ...] = ()


# --- model building -----------------------------------------------------------


def build_board_model(state: Any) -> BoardModel | None:
    """The :class:`BoardModel` for *state*, or ``None`` when it has no board.

    Board-shaped means: integer ``grid_width``/``grid_height`` > 0 and a
    ``units`` sequence. Individual entries that are malformed, dead
    (``alive`` falsy), out of bounds, exhausted (a resource node with
    nothing ``remaining``) or closed (a mission whose ``status`` isn't
    ``"open"``) are skipped rather than failing the whole board.
    """
    if not isinstance(state, Mapping):
        return None
    width = state.get("grid_width")
    height = state.get("grid_height")
    if not _is_dimension(width) or not _is_dimension(height):
        return None
    raw_units = state.get("units")
    if not _is_sequence(raw_units):
        return None

    units = tuple(
        unit
        for unit in (_build_unit(entry, width, height) for entry in raw_units)
        if unit is not None
    )
    posts = tuple(
        post
        for post in (
            _build_post(entry, width, height) for entry in _entries(state, "control_points")
        )
        if post is not None
    )
    resources = tuple(
        res
        for res in (
            _build_resource(entry, width, height) for entry in _entries(state, "resource_nodes")
        )
        if res is not None
    )
    missions = tuple(
        mission
        for mission in (
            _build_mission(entry, width, height) for entry in _entries(state, "missions")
        )
        if mission is not None
    )
    return BoardModel(
        width=width,
        height=height,
        units=units,
        posts=posts,
        resources=resources,
        missions=missions,
    )


def _is_dimension(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _entries(state: Mapping[str, Any], key: str) -> tuple[Any, ...]:
    value = state.get(key)
    return tuple(value) if _is_sequence(value) else ()


def _cell(value: Any, width: int, height: int) -> tuple[int, int] | None:
    if not _is_sequence(value) or len(value) != 2:
        return None
    x, y = value
    if (
        not isinstance(x, int)
        or not isinstance(y, int)
        or isinstance(x, bool)
        or isinstance(y, bool)
    ):
        return None
    if not (0 <= x < width and 0 <= y < height):
        return None
    return x, y


def _build_unit(entry: Any, width: int, height: int) -> BoardUnit | None:
    if not isinstance(entry, Mapping) or not entry.get("alive", True):
        return None
    unit_id = entry.get("id")
    cell = _cell(entry.get("pos"), width, height)
    if not isinstance(unit_id, str) or not unit_id or cell is None:
        return None
    carrying = entry.get("carrying")
    return BoardUnit(
        unit_id=unit_id,
        team=str(entry.get("team_id") or ""),
        role=str(entry.get("role") or "unit"),
        x=cell[0],
        y=cell[1],
        carrying=carrying if isinstance(carrying, int) and not isinstance(carrying, bool) else 0,
    )


def _build_post(entry: Any, width: int, height: int) -> BoardPost | None:
    if not isinstance(entry, Mapping):
        return None
    cell = _cell(entry.get("pos"), width, height)
    if cell is None:
        return None
    owner = entry.get("owner")
    return BoardPost(
        marker_id=str(entry.get("id") or "post"),
        x=cell[0],
        y=cell[1],
        owner=owner if isinstance(owner, str) and owner else None,
    )


def _build_resource(entry: Any, width: int, height: int) -> BoardResource | None:
    if not isinstance(entry, Mapping):
        return None
    cell = _cell(entry.get("pos"), width, height)
    remaining = entry.get("remaining")
    remaining = remaining if isinstance(remaining, int) and not isinstance(remaining, bool) else 0
    if cell is None or remaining <= 0:
        return None
    return BoardResource(
        marker_id=str(entry.get("id") or "resource"), x=cell[0], y=cell[1], remaining=remaining
    )


def _build_mission(entry: Any, width: int, height: int) -> BoardMission | None:
    if not isinstance(entry, Mapping) or entry.get("status", "open") != "open":
        return None
    cell = _cell(entry.get("pos"), width, height)
    if cell is None:
        return None
    return BoardMission(
        marker_id=str(entry.get("id") or "mission"),
        x=cell[0],
        y=cell[1],
        kind=str(entry.get("kind") or "mission"),
    )


# --- rendering ------------------------------------------------------------------

#: Role -> glyph shape, authored on a 24x24 viewBox (the hero's vocabulary:
#: scout triangle, harvester circle, defender square). Unknown roles fall
#: back to the circle.
_ROLE_SHAPES: dict[str, str] = {
    "scout": '<polygon points="12,2.5 22,21 2,21"></polygon>',
    "harvester": '<circle cx="12" cy="12" r="9"></circle>',
    "defender": '<rect x="3.5" y="3.5" width="17" height="17" rx="2.5"></rect>',
}
_DEFAULT_SHAPE = _ROLE_SHAPES["harvester"]

_POST_SVG = (
    '<svg class="board-mark" viewBox="0 0 24 24" aria-hidden="true">'
    '<circle class="board-post-ring" cx="12" cy="12" r="8.5"></circle>'
    '<rect class="board-post-base" x="9" y="9" width="6" height="6" rx="1"></rect></svg>'
)
_RESOURCE_SVG = (
    '<svg class="board-mark" viewBox="0 0 24 24" aria-hidden="true">'
    '<polygon points="12,3 21,12 12,21 3,12"></polygon></svg>'
)
_MISSION_SVG = (
    '<svg class="board-mark" viewBox="0 0 24 24" aria-hidden="true">'
    '<line x1="8" y1="21" x2="8" y2="4"></line>'
    '<polygon points="8,4 18,7 8,10"></polygon></svg>'
)


def render_board(
    model: BoardModel,
    *,
    overlay: BoardOverlay | None = None,
    accent_team: str | None = None,
) -> str:
    """Render *model* as HTML — static unless *overlay* adds the play layer.

    ``accent_team`` picks which team wears the solid accent treatment (the
    play surface passes the signed-in human's own team so *your* pieces are
    always the accent ones); it defaults to the first unit's team, keeping
    spectate rendering deterministic.
    """
    accent = accent_team if accent_team is not None else _default_accent(model)
    parts = [
        '<div class="board-wrap">',
        (
            f'<div class="board" style="--bw:{model.width};--bh:{model.height}" role="group" '
            f'aria-label="Match board, {model.width} by {model.height} cells">'
        ),
    ]
    for post in model.posts:
        parts.append(_render_post(post, accent))
    for resource in model.resources:
        parts.append(_render_resource(resource))
    for mission in model.missions:
        parts.append(_render_mission(mission))
    for unit in model.units:
        parts.append(_render_unit(unit, accent, overlay))
    if overlay is not None and overlay.selected_unit is not None:
        selected_cell = next(
            ((unit.x, unit.y) for unit in model.units if unit.unit_id == overlay.selected_unit),
            None,
        )
        parts.extend(_render_controls(overlay, selected_cell))
    parts.append("</div>")
    parts.append("</div>")
    return "\n".join(parts)


def _default_accent(model: BoardModel) -> str | None:
    return model.units[0].team if model.units else None


def _grid_area(x: int, y: int) -> str:
    return f"grid-area:{y + 1}/{x + 1}"


def _render_post(post: BoardPost, accent: str | None) -> str:
    if post.owner is None:
        owner = "none"
    elif post.owner == accent:
        owner = "accent"
    else:
        owner = "ink"
    label = html.escape(f"Control post {post.marker_id}")
    return (
        f'<span class="board-post" data-owner="{owner}" style="{_grid_area(post.x, post.y)}" '
        f'role="img" aria-label="{label}">{_POST_SVG}</span>'
    )


def _render_resource(resource: BoardResource) -> str:
    label = html.escape(f"Resource node {resource.marker_id} ({resource.remaining} left)")
    return (
        f'<span class="board-res" style="{_grid_area(resource.x, resource.y)}" '
        f'role="img" aria-label="{label}">{_RESOURCE_SVG}</span>'
    )


def _render_mission(mission: BoardMission) -> str:
    label = html.escape(f"Mission {mission.marker_id} ({mission.kind})")
    return (
        f'<span class="board-mission" style="{_grid_area(mission.x, mission.y)}" '
        f'role="img" aria-label="{label}">{_MISSION_SVG}</span>'
    )


def _render_unit(unit: BoardUnit, accent: str | None, overlay: BoardOverlay | None) -> str:
    team_class = "board-team-accent" if unit.team == accent else "board-team-ink"
    role_class = f"board-role-{html.escape(unit.role)}"
    shape = _ROLE_SHAPES.get(unit.role, _DEFAULT_SHAPE)
    glyph = f'<svg class="board-glyph" viewBox="0 0 24 24" aria-hidden="true">{shape}</svg>'
    carry = '<span class="board-carry" aria-hidden="true"></span>' if unit.carrying > 0 else ""
    at = f"({unit.x}, {unit.y})"
    area = _grid_area(unit.x, unit.y)

    if overlay is not None and unit.unit_id == overlay.selected_unit:
        label = html.escape(f"{unit.unit_id} ({unit.role}) — selected")
        return (
            f'<span class="board-unit {team_class} {role_class} board-unit-selected" '
            f'style="{area}" role="img" aria-label="{label}">{glyph}{carry}</span>'
        )
    if overlay is not None and unit.unit_id in overlay.select_hrefs:
        href = html.escape(overlay.select_hrefs[unit.unit_id])
        label = html.escape(f"Select {unit.unit_id} ({unit.role}) at {at}")
        return (
            f'<a class="board-unit {team_class} {role_class} board-unit-live" '
            f'style="{area}" href="{href}" aria-label="{label}">{glyph}{carry}</a>'
        )
    label = html.escape(f"{unit.unit_id} ({unit.role}) at {at}")
    return (
        f'<span class="board-unit {team_class} {role_class}" style="{area}" '
        f'role="img" aria-label="{label}">{glyph}{carry}</span>'
    )


def _render_controls(overlay: BoardOverlay, selected_cell: tuple[int, int] | None) -> list[str]:
    by_cell: dict[tuple[int, int], list[CellControl]] = {}
    for control in overlay.controls:
        by_cell.setdefault((control.x, control.y), []).append(control)

    rendered: list[str] = []
    for (x, y), controls in by_cell.items():
        area = _grid_area(x, y)
        if (x, y) == selected_cell:
            # The selected unit's own cell: its verbs (gather/deliver/hold)
            # render as verb-labeled pills anchored to the cell's foot, so
            # the unit stays visible and a stray tap on your own piece can
            # never fire an unnamed action — even when there is only one.
            classes = "board-target board-target-stack board-target-self"
        elif len(controls) == 1:
            rendered.append(_render_single_control(controls[0], overlay.form_action, area))
            continue
        else:
            classes = "board-target board-target-stack"
        stacked = "".join(
            _render_stacked_control(control, overlay.form_action) for control in controls
        )
        rendered.append(f'<div class="{classes}" style="{area}">{stacked}</div>')
    return rendered


def _render_single_control(control: CellControl, form_action: str, area: str) -> str:
    return (
        f'<form method="post" action="{html.escape(form_action)}" class="board-target" '
        f'style="{area}">'
        f'<input type="hidden" name="action" value="{html.escape(control.value)}">'
        f'<button type="submit" class="board-target-btn" '
        f'aria-label="{html.escape(control.label)}"></button></form>'
    )


def _render_stacked_control(control: CellControl, form_action: str) -> str:
    return (
        f'<form method="post" action="{html.escape(form_action)}">'
        f'<input type="hidden" name="action" value="{html.escape(control.value)}">'
        f'<button type="submit" class="board-target-btn board-verb-btn" '
        f'aria-label="{html.escape(control.label)}">{html.escape(control.verb)}</button></form>'
    )
