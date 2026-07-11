"""Shared test-only helpers for the browser play surface (``league_site.play``).

Not collected by pytest (module name doesn't match ``test_*``); imported by
``test_play_*`` modules.
"""

from __future__ import annotations

import copy
import io
import time
from collections.abc import Mapping
from typing import Any, Callable
from urllib.parse import urlencode

from league_site.auth import sessions
from league_site.auth.wsgi import SESSION_ENVIRON_KEY
from league_site.matches import GameEngine, Participant

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

#: The full, submittable actions :class:`PlayableEngine` publishes as its
#: ``legal_actions`` — dict-shaped, so the play surface renders each one as
#: a form choice and submits it verbatim.
LEGAL_PLAY_ACTIONS: tuple[dict[str, int], ...] = (
    {"points": 1},
    {"points": 2},
    {"points": 3},
)


class PlayableEngine(GameEngine):
    """``StubDuelEngine``'s turn-taking with browser-submittable ``legal_actions``.

    The built-in stub publishes bare point values (``[1, 2, 3]``) as its
    ``legal_actions`` — legal *parameters*, not whole submittable actions —
    which the play surface (by documented contract; see
    :mod:`league_site.play.actions`) does not render as choices. This engine
    publishes the full ``{"points": n}`` action dicts instead: exactly the
    sequence-of-mappings shape the play surface renders as form options and
    submits verbatim, so these tests drive the whole browser loop in-process
    with no subprocess-backed game.
    """

    def __init__(self, *, target: int = 6, game_id: str = "playable-duel") -> None:
        self._target = target
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Any) -> dict[str, Any]:
        order = [participant.participant_id for participant in participants]
        return {
            "participant_order": order,
            "scores": {participant_id: 0 for participant_id in order},
            "turn_index": 0,
            "turns_taken": 0,
            "target": self._target,
            "legal_actions": [dict(action) for action in LEGAL_PLAY_ACTIONS],
        }

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        order = state["participant_order"]
        expected = order[state["turn_index"] % len(order)]
        if participant_id != expected:
            raise ValueError(f"it is not participant {participant_id!r}'s turn")
        points = action.get("points") if isinstance(action, dict) else None
        if not isinstance(points, int) or {"points": points} not in LEGAL_PLAY_ACTIONS:
            raise ValueError(f"illegal action {action!r}")
        scores = dict(state["scores"])
        scores[participant_id] = scores.get(participant_id, 0) + points
        return {
            **state,
            "scores": scores,
            "turn_index": state["turn_index"] + 1,
            "turns_taken": state["turns_taken"] + 1,
        }

    def is_over(self, state: dict[str, Any]) -> bool:
        return any(score >= state["target"] for score in state["scores"].values())

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        return {participant_id: float(total) for participant_id, total in state["scores"].items()}


class GridBoardEngine(GameEngine):
    """A ``GridLaneEngine``-shaped engine with a real board, no subprocess.

    Publishes the same state shape the real adapter mirrors from ``league
    match show --json`` (``docs/game-integration.md``): the board projections
    (``grid_width``/``grid_height``/``units``/``control_points``/
    ``resource_nodes``/``missions``) plus the per-unit ``legal_actions``
    summary — so board-interaction tests drive the whole two-step play flow
    (select a unit, submit a target cell) in-process. ``apply_turn`` accepts
    the game's own ``{"actions": [...]}`` envelope and refuses anything not
    in the current ``legal_actions``, exactly like the real engine's CLI
    would.
    """

    #: The 6x5 opening scene ``initial_state`` deals: two solo units (a scout
    #: and a harvester standing on a resource node) versus one house scout.
    _OPENING: dict[str, Any] = {
        "game_id": "league-of-agents-grid",
        "mode": "solo-vs-bot",
        "status": "active",
        "turn": 0,
        "turn_limit": 8,
        "winner": None,
        "staged_teams": [],
        "grid_width": 6,
        "grid_height": 5,
        "units": [
            {
                "id": "solo-u1",
                "team_id": "solo",
                "agent_id": "solo-scout",
                "role": "scout",
                "pos": [1, 1],
                "carrying": 0,
                "alive": True,
            },
            {
                "id": "solo-u2",
                "team_id": "solo",
                "agent_id": "solo-harvester",
                "role": "harvester",
                "pos": [2, 3],
                "carrying": 1,
                "alive": True,
            },
            {
                "id": "house-u1",
                "team_id": "house",
                "agent_id": "house-scout",
                "role": "scout",
                "pos": [5, 4],
                "carrying": 0,
                "alive": True,
            },
        ],
        "control_points": [{"id": "cp-mid", "pos": [3, 2], "owner": None, "hold": []}],
        "resource_nodes": [{"id": "rn-a", "pos": [2, 3], "remaining": 5}],
        "missions": [{"id": "ms-x", "kind": "deliver", "pos": [4, 0], "status": "open"}],
    }

    def __init__(self, *, game_id: str = "grid-board-duel") -> None:
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Any) -> dict[str, Any]:
        (participant,) = list(participants)
        state = copy.deepcopy(self._OPENING)
        state["participant_teams"] = {participant.participant_id: "solo"}
        state["team_participants"] = {"solo": [participant.participant_id]}
        state["legal_actions"] = {unit["id"]: self._summary(state, unit) for unit in state["units"]}
        return state

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        if participant_id not in state["participant_teams"]:
            raise ValueError(f"participant {participant_id!r} controls no team")
        if not isinstance(action, Mapping) or not isinstance(action.get("actions"), list):
            raise ValueError(f"expected an {{'actions': [...]}} envelope, got {action!r}")
        new = copy.deepcopy(state)
        for order in action["actions"]:
            self._apply_order(state, new, order)
        new["turn"] += 1
        new["legal_actions"] = {unit["id"]: self._summary(new, unit) for unit in new["units"]}
        return new

    def _apply_order(self, state: dict[str, Any], new: dict[str, Any], order: Any) -> None:
        unit = next((u for u in new["units"] if u["id"] == order.get("unit_id")), None)
        if unit is None:
            raise ValueError(f"unknown unit in order {order!r}")
        legal = state["legal_actions"].get(unit["id"]) or {}
        verb = order.get("action")
        if verb == "move":
            to = list(order.get("to") or ())
            if to not in [list(cell) for cell in legal.get("move", [])]:
                raise ValueError(f"illegal move for {unit['id']}: {order!r}")
            unit["pos"] = to
        elif verb in ("gather", "deliver", "hold"):
            if not legal.get(verb):
                raise ValueError(f"illegal {verb} for {unit['id']}")
            if verb == "gather":
                unit["carrying"] += 1
        else:
            raise ValueError(f"unknown verb in order {order!r}")

    def _summary(self, state: dict[str, Any], unit: dict[str, Any]) -> dict[str, Any]:
        x, y = unit["pos"]
        moves = [
            [nx, ny]
            for nx, ny in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            if 0 <= nx < state["grid_width"] and 0 <= ny < state["grid_height"]
        ]
        on_node = any(
            list(node["pos"]) == [x, y] and node.get("remaining", 0) > 0
            for node in state["resource_nodes"]
        )
        return {
            "move": sorted(moves),
            "gather": on_node,
            "deliver": False,
            "hold": True,
            "can_gather": True,
            "can_capture": True,
        }

    def is_over(self, state: dict[str, Any]) -> bool:
        return state["turn"] >= state["turn_limit"]

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        totals = {team: 0.0 for team in state["team_participants"]}
        for unit in state["units"]:
            if unit["team_id"] in totals:
                totals[unit["team_id"]] += float(unit["carrying"])
        return {pid: totals.get(team, 0.0) for pid, team in state["participant_teams"].items()}


def session_for(subject: str = "42", display: str = "Ada") -> sessions.Session:
    """A verified-shaped :class:`~league_site.auth.sessions.Session` for tests.

    Constructed directly (no signing secret involved) — exactly what
    ``with_auth`` stores in ``environ[SESSION_ENVIRON_KEY]`` after verifying
    a cookie, which is all ``with_play`` ever reads.
    """
    now = int(time.time())
    return sessions.Session(
        subject=subject,
        provider="github",
        display=display,
        issued_at=now,
        expiry=now + 3600,
    )


def human_participant(session: sessions.Session) -> Participant:
    """The :class:`Participant` *session* plays as (same key the play surface derives)."""
    from league_site.api.identity import identity_for_session, participant_for_identity

    return participant_for_identity(identity_for_session(session))


def call_page(
    app: WSGIApp,
    method: str,
    path: str,
    *,
    form: dict[str, str] | None = None,
    session: sessions.Session | None = None,
    session_key_present: bool = True,
    headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, str], str]:
    """Minimal WSGI test client for HTML page surfaces.

    ``form`` is sent urlencoded (the browser's default form encoding);
    ``session`` (or ``None`` for anonymous) is stored under
    ``SESSION_ENVIRON_KEY`` the way ``with_auth`` would. A ``?query`` suffix
    on *path* becomes ``QUERY_STRING``, the way a WSGI server would split a
    request target. Returns ``(status, headers, body_text)``.
    """
    captured: dict[str, Any] = {}

    def start_response(
        status: str, response_headers: list[tuple[str, str]], exc_info: Any = None
    ) -> None:
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    raw_body = urlencode(form).encode("utf-8") if form is not None else b""
    path, _, query_string = path.partition("?")
    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": query_string,
        "CONTENT_LENGTH": str(len(raw_body)),
        "CONTENT_TYPE": "application/x-www-form-urlencoded",
        "wsgi.input": io.BytesIO(raw_body),
        "wsgi.url_scheme": "https",
        "HTTP_HOST": "league-of-agents.ai",
    }
    if session_key_present:
        environ[SESSION_ENVIRON_KEY] = session
    for name, value in (headers or {}).items():
        environ[f"HTTP_{name.upper().replace('-', '_')}"] = value

    raw = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], raw.decode("utf-8")
