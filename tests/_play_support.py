"""Shared test-only helpers for the browser play surface (``league_site.play``).

Not collected by pytest (module name doesn't match ``test_*``); imported by
``test_play_*`` modules.
"""

from __future__ import annotations

import io
import time
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
    ``SESSION_ENVIRON_KEY`` the way ``with_auth`` would. Returns
    ``(status, headers, body_text)``.
    """
    captured: dict[str, Any] = {}

    def start_response(
        status: str, response_headers: list[tuple[str, str]], exc_info: Any = None
    ) -> None:
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    raw_body = urlencode(form).encode("utf-8") if form is not None else b""
    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
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
