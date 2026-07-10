"""Tests for :mod:`league_site.api.wsgi` — the ``/api/v1`` match API.

Exercises :func:`with_api` directly (mounted on a trivial passthrough app),
injecting explicit store/registry instances so each test controls its own
persistence and identities. Human identity is simulated by setting
``environ[SESSION_ENVIRON_KEY]`` directly (exactly what
:func:`league_site.auth.wsgi.with_auth` does upstream in the real
composition — see :mod:`league_site.web.http`'s ``site_app`` and
``tests/test_api_site_composition.py`` for the full, wired-through-
``with_auth`` version of this same flow); agent identity goes through a
real issued :class:`~league_site.auth.token_store.InMemoryTokenStore`
token, since :mod:`league_site.auth.tokens` is cheap to exercise directly.
"""

from __future__ import annotations

import copy
import io
from collections.abc import Mapping, Sequence
from typing import Any

from league_site.api.engines import DEFAULT_MODE
from league_site.api.wsgi import with_api
from league_site.auth import sessions, tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.auth.wsgi import SESSION_ENVIRON_KEY
from league_site.capacity.config import CapacityConfig
from league_site.matches import GameEngine, InMemoryMatchStore, Participant
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from tests._api_support import bearer, call


class _MalformedActionEngine(GameEngine):
    """Mirrors ``GridLaneEngine.apply_turn``'s own contract: a turn's
    ``action`` must be a JSON-object-shaped mapping, or ``apply_turn`` raises
    :class:`TypeError` — see ``league_site.game.adapter``. A body missing the
    ``"action"`` wrapper (e.g. ``{"actions": [...]}``) resolves to
    ``action=None`` in :func:`league_site.api.wsgi._handle_take_turn`, which
    is exactly what reproduces the live crash this engine exists to test."""

    def __init__(self, *, game_id: str = "malformed-action-demo") -> None:
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Sequence[Participant]) -> dict[str, Any]:
        return {"turns_taken": 0}

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        if not isinstance(action, Mapping):
            raise TypeError(
                "apply_turn(action=...) must be a JSON-object-shaped mapping of league "
                "orders, e.g. {'actions': [...]}"
            )
        return {"turns_taken": state["turns_taken"] + 1}

    def is_over(self, state: dict[str, Any]) -> bool:
        return False

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        return {}


class _ExplodingEngine(GameEngine):
    """A toy engine whose ``apply_turn`` always raises an exception no
    handler in :mod:`league_site.api.wsgi` maps to an :class:`~league_site.
    api.errors.ApiError` — proves the API's dispatch guard still renders a
    JSON 500 envelope (never a bare WSGI error page) for a genuinely
    unexpected failure."""

    def __init__(self, *, game_id: str = "exploding-demo") -> None:
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Sequence[Participant]) -> dict[str, Any]:
        return {}

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        raise RuntimeError("engine exploded unexpectedly")

    def is_over(self, state: dict[str, Any]) -> bool:
        return False

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        return {}


class _DrawEngine(GameEngine):
    """Ends the instant every participant has taken exactly one turn, always
    scoring every participant ``0.0`` — a deterministic tie, used to prove a
    two-participant equal-score finish records a draw (``winner_participant_id``
    is ``None``) end to end through the API."""

    def __init__(self, *, game_id: str = "draw-demo") -> None:
        self._game_id = game_id

    @property
    def game_id(self) -> str:
        return self._game_id

    def initial_state(self, participants: Sequence[Participant]) -> dict[str, Any]:
        return {
            "participant_ids": [participant.participant_id for participant in participants],
            "turns_taken": 0,
        }

    def apply_turn(self, state: dict[str, Any], participant_id: str, action: Any) -> dict[str, Any]:
        return {**state, "turns_taken": state["turns_taken"] + 1}

    def is_over(self, state: dict[str, Any]) -> bool:
        return state["turns_taken"] >= len(state["participant_ids"])

    def score(self, state: dict[str, Any]) -> dict[str, float]:
        return {participant_id: 0.0 for participant_id in state["participant_ids"]}


def _passthrough(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [f"not an api route: {environ.get('PATH_INFO')}".encode("utf-8")]


def _build(
    *,
    match_store: InMemoryMatchStore | None = None,
    token_store: InMemoryTokenStore | None = None,
    ledger_store: InMemoryRatingLedgerStore | None = None,
    engine_registry: Any = None,
    capacity_config: CapacityConfig | None = None,
) -> tuple[Any, InMemoryMatchStore, InMemoryTokenStore, InMemoryRatingLedgerStore]:
    match_store = match_store if match_store is not None else InMemoryMatchStore()
    token_store = token_store if token_store is not None else InMemoryTokenStore()
    ledger_store = ledger_store if ledger_store is not None else InMemoryRatingLedgerStore()
    app = with_api(
        _passthrough,
        match_store=match_store,
        token_store=token_store,
        ledger_store=ledger_store,
        engine_registry=engine_registry,
        capacity_config=capacity_config,
    )
    return app, match_store, token_store, ledger_store


def _human_environ(
    subject: str = "human-1", provider: str = "github", display: str = "Ada"
) -> dict:
    session = sessions.Session(
        subject=subject, provider=provider, display=display, issued_at=0, expiry=2**31
    )
    return {"environ_extra": {SESSION_ENVIRON_KEY: session}}


def _issue_agent(
    token_store: InMemoryTokenStore,
    *,
    agent_name: str = "Sonnet",
    model: str = "claude-sonnet-5",
    provider: str = "anthropic",
) -> dict:
    issued = tokens.issue(token_store, agent_name=agent_name, model=model, provider=provider)
    return {"headers": bearer(issued.token)}


def _play_out(app: Any, match_id: str, actors: dict[str, dict]) -> dict:
    """Alternate turns (always ``{"points": 3}``) as whichever participant is
    due, per ``state.participant_order``/``turn_index``, until the match
    completes. ``actors`` maps ``participant_id -> call() kwargs`` (either
    ``environ_extra`` for a human session or ``headers`` for an agent
    token)."""
    for _ in range(50):
        _, _, payload = call(app, "GET", f"/api/v1/matches/{match_id}")
        if payload["status"] == "completed":
            return payload
        state = payload["state"]
        order = state["participant_order"]
        turn_participant = order[state["turn_index"] % len(order)]
        status, _, payload = call(
            app,
            "POST",
            f"/api/v1/matches/{match_id}/turns",
            body={"action": {"points": 3}},
            **actors[turn_participant],
        )
        assert status == "200 OK", payload
    raise AssertionError("match did not complete within 50 turns")


# --- passthrough for non-API paths ------------------------------------------


def test_paths_outside_the_api_prefix_pass_through_unchanged() -> None:
    app, *_ = _build()
    status, headers, body = call(app, "GET", "/some/other/page")
    assert status == "404 Not Found"
    assert body == b"not an api route: /some/other/page"


# --- create match ------------------------------------------------------------


def test_create_match_requires_an_identity() -> None:
    app, *_ = _build()
    status, _, payload = call(app, "POST", "/api/v1/matches", body={})
    assert status == "401 Unauthorized"
    assert payload["code"] == "unauthorized"


def test_create_match_by_human_session_defaults_to_a_solo_practice_match() -> None:
    app, *_ = _build()
    status, _, payload = call(
        app, "POST", "/api/v1/matches", body=None, **_human_environ(display="Ada")
    )
    assert status == "201 Created"
    assert payload["status"] == "active"
    assert payload["game_id"] == DEFAULT_MODE
    assert len(payload["participants"]) == 1
    assert payload["participants"][0]["display_name"] == "Ada"
    assert payload["legal_actions"] == [1, 2, 3]


def test_create_match_by_agent_token() -> None:
    app, _, token_store, _ = _build()
    status, _, payload = call(
        app, "POST", "/api/v1/matches", body={}, **_issue_agent(token_store, agent_name="Sonnet")
    )
    assert status == "201 Created"
    assert payload["participants"][0]["kind"] == "agent"
    assert payload["participants"][0]["display_name"] == "Sonnet"


# --- create match: capacity guard --------------------------------------------


def _tight_capacity_config(*, max_concurrent: int = 1, max_stored: int = 100) -> CapacityConfig:
    return CapacityConfig(
        max_concurrent_matches=max_concurrent,
        max_stored_matches=max_stored,
        max_match_age_days_hot=3,
        max_archive_age_days=180,
    )


def test_create_match_is_refused_with_a_structured_429_once_at_the_concurrent_cap() -> None:
    app, *_ = _build(capacity_config=_tight_capacity_config(max_concurrent=1))
    status, _, first = call(
        app, "POST", "/api/v1/matches", body={}, **_human_environ(subject="human-1")
    )
    assert status == "201 Created"
    assert first["status"] == "active"  # counts toward max_concurrent_matches immediately

    status, _, refused = call(
        app, "POST", "/api/v1/matches", body={}, **_human_environ(subject="human-2")
    )

    assert status == "429 Too Many Requests"
    assert refused["code"] == "capacity_exceeded"
    assert refused["reason"] == "max_concurrent_matches"
    assert refused["current"] == 1
    assert refused["limit"] == 1


def test_create_match_identity_requirement_is_checked_before_the_capacity_gate() -> None:
    """An anonymous request gets 401, not a capacity 429, even when the
    store is already at the configured cap — identity is required before
    this endpoint does anything else, capacity gate included."""
    app, *_ = _build(capacity_config=_tight_capacity_config(max_concurrent=1))
    status, _, first = call(
        app, "POST", "/api/v1/matches", body={}, **_human_environ(subject="human-1")
    )
    assert status == "201 Created"  # store is now at the concurrent cap

    status, _, payload = call(app, "POST", "/api/v1/matches", body={})

    assert status == "401 Unauthorized"
    assert payload["code"] == "unauthorized"


def test_create_match_succeeds_again_once_the_blocking_match_completes() -> None:
    app, *_ = _build(capacity_config=_tight_capacity_config(max_concurrent=1))
    human_a = _human_environ(subject="human-1")
    status, _, first = call(app, "POST", "/api/v1/matches", body={}, **human_a)
    assert status == "201 Created"
    match_id = first["match_id"]

    status, _, refused = call(
        app, "POST", "/api/v1/matches", body={}, **_human_environ(subject="human-2")
    )
    assert status == "429 Too Many Requests"

    # drive the first (solo) match to completion — StubDuelEngine's default
    # target is 10, so four {"points": 3} turns clears it (12 total).
    payload = first
    for _ in range(10):
        if payload["status"] == "completed":
            break
        status, _, payload = call(
            app,
            "POST",
            f"/api/v1/matches/{match_id}/turns",
            body={"action": {"points": 3}},
            **human_a,
        )
        assert status == "200 OK", payload
    assert payload["status"] == "completed"

    status, _, second = call(
        app, "POST", "/api/v1/matches", body={}, **_human_environ(subject="human-2")
    )
    assert status == "201 Created", second


def test_create_match_unknown_mode_is_bad_request() -> None:
    app, *_ = _build()
    status, _, payload = call(
        app, "POST", "/api/v1/matches", body={"mode": "no-such-game"}, **_human_environ()
    )
    assert status == "400 Bad Request"
    assert payload["code"] == "unknown_mode"


def test_create_match_rejects_a_non_string_mode() -> None:
    app, *_ = _build()
    status, _, payload = call(app, "POST", "/api/v1/matches", body={"mode": ""}, **_human_environ())
    assert status == "400 Bad Request"
    assert payload["code"] == "bad_request"


def test_create_match_with_opponent_has_two_participants() -> None:
    app, *_ = _build()
    body = {
        "opponent": {
            "kind": "agent",
            "display_name": "Rival",
            "agent_name": "Rival",
            "model": "gpt-5",
            "provider": "openai",
        }
    }
    status, _, payload = call(app, "POST", "/api/v1/matches", body=body, **_human_environ())
    assert status == "201 Created"
    assert len(payload["participants"]) == 2
    kinds = {p["kind"] for p in payload["participants"]}
    assert kinds == {"human", "agent"}


def test_create_match_rejects_a_malformed_opponent() -> None:
    app, *_ = _build()
    status, _, payload = call(
        app, "POST", "/api/v1/matches", body={"opponent": {"kind": "human"}}, **_human_environ()
    )
    assert status == "400 Bad Request"


def test_create_match_rejects_opponent_matching_the_creator() -> None:
    app, *_ = _build()
    body = {
        "opponent": {
            "kind": "human",
            "display_name": "Ada again",
            "provider": "github",
            "subject": "human-1",
        }
    }
    status, _, payload = call(app, "POST", "/api/v1/matches", body=body, **_human_environ())
    assert status == "400 Bad Request"


def test_create_match_rejects_a_non_object_body() -> None:
    app, *_ = _build()
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api/v1/matches",
        "QUERY_STRING": "",
        "CONTENT_LENGTH": "2",
        "wsgi.input": io.BytesIO(b"[]"),
        **_human_environ()["environ_extra"],
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status

    app(environ, start_response)
    assert captured["status"] == "400 Bad Request"


def test_create_match_rejects_invalid_json() -> None:
    app, *_ = _build()
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api/v1/matches",
        "QUERY_STRING": "",
        "CONTENT_LENGTH": "9",
        "wsgi.input": io.BytesIO(b"{not-json"),
        **_human_environ()["environ_extra"],
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status

    app(environ, start_response)
    assert captured["status"] == "400 Bad Request"


def test_create_match_with_a_malformed_content_length_is_treated_as_no_body() -> None:
    """A non-numeric ``CONTENT_LENGTH`` degrades to "no body" (empty JSON
    object) rather than raising a server error — the whole body is then
    just defaults, same as an actual empty POST."""
    app, *_ = _build()
    environ = {
        "REQUEST_METHOD": "POST",
        "PATH_INFO": "/api/v1/matches",
        "QUERY_STRING": "",
        "CONTENT_LENGTH": "not-a-number",
        "wsgi.input": io.BytesIO(b""),
        **_human_environ()["environ_extra"],
    }
    captured = {}

    def start_response(status, headers, exc_info=None):
        captured["status"] = status

    app(environ, start_response)
    assert captured["status"] == "201 Created"


# --- get match (public spectate) --------------------------------------------


def test_get_match_is_public() -> None:
    app, *_ = _build()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **_human_environ())
    status, _, payload = call(app, "GET", f"/api/v1/matches/{created['match_id']}")
    assert status == "200 OK"
    assert payload["match_id"] == created["match_id"]


def test_get_unknown_match_is_404() -> None:
    app, *_ = _build()
    status, _, payload = call(app, "GET", "/api/v1/matches/does-not-exist")
    assert status == "404 Not Found"
    assert payload["code"] == "not_found"


# --- full match play-throughs (h16 / h8) ------------------------------------


def test_human_session_plays_a_full_match_through_the_public_api() -> None:
    app, _, _, ledger_store = _build()
    human = _human_environ(subject="human-full", display="Ada")

    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    match_id = created["match_id"]
    participant_id = created["participants"][0]["participant_id"]

    final = _play_out(app, match_id, {participant_id: human})

    assert final["status"] == "completed"
    assert final["result"]["completed"] is True

    status, _, score = call(app, "GET", f"/api/v1/matches/{match_id}/score")
    assert status == "200 OK"
    assert score["result"]["completed"] is True
    # A solo practice match has < 2 scored participants -> never rated.
    assert ledger_store.all_identities() == []


def test_agent_token_plays_a_full_match_through_the_public_api() -> None:
    app, _, token_store, _ = _build()
    agent = _issue_agent(token_store, agent_name="Sonnet")

    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **agent)
    match_id = created["match_id"]
    participant_id = created["participants"][0]["participant_id"]

    final = _play_out(app, match_id, {participant_id: agent})

    assert final["status"] == "completed"
    status, _, score = call(app, "GET", f"/api/v1/matches/{match_id}/score")
    assert status == "200 OK"
    assert score["result"]["completed"] is True


def test_human_vs_agent_duel_plays_to_completion_and_rates_both() -> None:
    app, _, token_store, ledger_store = _build()
    human = _human_environ(subject="human-duel", display="Ada")
    body = {
        "opponent": {
            "kind": "agent",
            "display_name": "Rival",
            "agent_name": "Rival",
            "model": "gpt-5",
            "provider": "openai",
        }
    }
    _, _, created = call(app, "POST", "/api/v1/matches", body=body, **human)
    match_id = created["match_id"]
    human_pid, agent_pid = (p["participant_id"] for p in created["participants"])

    agent = _issue_agent(token_store, agent_name="Rival", model="gpt-5", provider="openai")
    final = _play_out(app, match_id, {human_pid: human, agent_pid: agent})

    assert final["status"] == "completed"
    assert len(final["result"]["scores"]) == 2
    assert len(ledger_store.all_identities()) == 2


# --- turns: ownership + validation ------------------------------------------


def test_take_turn_by_non_participant_agent_is_forbidden() -> None:
    app, _, token_store, _ = _build()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **_human_environ())
    other = _issue_agent(token_store, agent_name="Intruder")
    status, _, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{created['match_id']}/turns",
        body={"action": {"points": 1}},
        **other,
    )
    assert status == "403 Forbidden"
    assert payload["code"] == "forbidden"


def test_take_turn_by_non_participant_human_is_forbidden() -> None:
    app, *_ = _build()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **_human_environ(subject="owner"))
    other_human = _human_environ(subject="someone-else")
    status, _, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{created['match_id']}/turns",
        body={"action": {"points": 1}},
        **other_human,
    )
    assert status == "403 Forbidden"


def test_take_turn_anonymous_is_forbidden() -> None:
    app, *_ = _build()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **_human_environ())
    status, _, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{created['match_id']}/turns",
        body={"action": {"points": 1}},
    )
    assert status == "403 Forbidden"
    assert payload["code"] == "forbidden"


def test_take_turn_illegal_action_is_bad_request() -> None:
    app, *_ = _build()
    human = _human_environ()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    status, _, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{created['match_id']}/turns",
        body={"action": {"points": 999}},
        **human,
    )
    assert status == "400 Bad Request"
    assert payload["code"] == "illegal_action"


def test_take_turn_with_a_malformed_action_shape_is_bad_request_not_a_500() -> None:
    """A turn body missing the ``"action"`` wrapper (e.g. ``{"actions": [...]}``)
    used to crash all the way through to a raw ``TypeError`` -> unhandled
    500 (see ``GridLaneEngine.apply_turn``); it must instead be a structured
    400 whose message carries the engine's own descriptive explanation."""
    app, *_ = _build(engine_registry={"malformed-action-demo": _MalformedActionEngine})
    human = _human_environ()
    _, _, created = call(
        app, "POST", "/api/v1/matches", body={"mode": "malformed-action-demo"}, **human
    )
    status, _, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{created['match_id']}/turns",
        body={"actions": [{"unit": "u1", "action": "move"}]},
        **human,
    )
    assert status == "400 Bad Request"
    assert payload["code"] == "malformed_action"
    assert "JSON-object-shaped mapping" in payload["message"]


def test_take_turn_with_an_unexpected_engine_error_is_a_json_500_envelope() -> None:
    """Genuinely unexpected exceptions stay ``500``s, but must still render
    the same ``{"code": ..., "message": ...}`` JSON envelope as every other
    API failure -- never a bare WSGI error page."""
    app, *_ = _build(engine_registry={"exploding-demo": _ExplodingEngine})
    human = _human_environ()
    _, _, created = call(app, "POST", "/api/v1/matches", body={"mode": "exploding-demo"}, **human)
    status, headers, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{created['match_id']}/turns",
        body={"action": {"points": 1}},
        **human,
    )
    assert status == "500 Internal Server Error"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload["code"] == "internal_error"
    assert "message" in payload


def test_take_turn_completing_with_equal_scores_records_a_draw() -> None:
    """A completed match where every participant scores equally (e.g. a
    0.0-0.0 finish) must record ``winner_participant_id: None`` -- the same
    conclusion :class:`~league_site.ratings.system.IntegerEloRatingSystem`
    already reaches for a tie, not whichever participant a plain
    ``max(scores, key=scores.get)`` happened to pick first."""
    app, *_ = _build(engine_registry={"draw-demo": _DrawEngine})
    owner = _human_environ(subject="owner")
    rival = _human_environ(subject="rival")
    body = {
        "mode": "draw-demo",
        "opponent": {
            "kind": "human",
            "display_name": "Rival",
            "provider": "github",
            "subject": "rival",
        },
    }
    _, _, created = call(app, "POST", "/api/v1/matches", body=body, **owner)
    match_id = created["match_id"]
    order = [participant["participant_id"] for participant in created["participants"]]
    actors = {order[0]: owner, order[1]: rival}

    payload: dict[str, Any] = {}
    for participant_id in order:
        status, _, payload = call(
            app,
            "POST",
            f"/api/v1/matches/{match_id}/turns",
            body={"action": {}},
            **actors[participant_id],
        )
        assert status == "200 OK"

    assert payload["status"] == "completed"
    assert payload["result"]["winner_participant_id"] is None
    assert payload["result"]["scores"] == {participant_id: 0.0 for participant_id in order}


def test_take_turn_completing_with_unequal_scores_still_records_a_winner() -> None:
    """Unequal-score completion is unaffected by the draw fix above."""
    app, *_ = _build()
    human = _human_environ()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    match_id = created["match_id"]

    final = _play_out(app, match_id, {created["participants"][0]["participant_id"]: human})
    assert final["result"]["winner_participant_id"] == created["participants"][0]["participant_id"]


def test_take_turn_on_missing_match_is_404() -> None:
    app, *_ = _build()
    status, _, payload = call(
        app,
        "POST",
        "/api/v1/matches/does-not-exist/turns",
        body={"action": {"points": 1}},
        **_human_environ(),
    )
    assert status == "404 Not Found"


def test_take_turn_on_a_paused_match_is_conflict() -> None:
    app, *_ = _build()
    human = _human_environ()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    match_id = created["match_id"]
    call(app, "POST", f"/api/v1/matches/{match_id}/pause", **human)

    status, _, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{match_id}/turns",
        body={"action": {"points": 1}},
        **human,
    )
    assert status == "409 Conflict"
    assert payload["code"] == "invalid_transition"


# --- pause / resume ----------------------------------------------------------


def test_pause_and_resume_by_the_participant() -> None:
    app, *_ = _build()
    human = _human_environ()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    match_id = created["match_id"]

    status, _, paused = call(app, "POST", f"/api/v1/matches/{match_id}/pause", **human)
    assert status == "200 OK"
    assert paused["status"] == "paused"

    status, _, resumed = call(app, "POST", f"/api/v1/matches/{match_id}/resume", **human)
    assert status == "200 OK"
    assert resumed["status"] == "active"


def test_pause_by_non_participant_is_forbidden() -> None:
    app, *_ = _build()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **_human_environ(subject="owner"))
    status, _, payload = call(
        app,
        "POST",
        f"/api/v1/matches/{created['match_id']}/pause",
    )
    assert status == "403 Forbidden"


def test_resume_by_non_participant_is_forbidden() -> None:
    app, *_ = _build()
    human = _human_environ(subject="owner")
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    match_id = created["match_id"]
    call(app, "POST", f"/api/v1/matches/{match_id}/pause", **human)

    other = _human_environ(subject="someone-else")
    status, _, payload = call(app, "POST", f"/api/v1/matches/{match_id}/resume", **other)
    assert status == "403 Forbidden"


def test_pause_when_not_active_is_conflict() -> None:
    app, *_ = _build()
    human = _human_environ()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    match_id = created["match_id"]
    call(app, "POST", f"/api/v1/matches/{match_id}/pause", **human)

    status, _, payload = call(app, "POST", f"/api/v1/matches/{match_id}/pause", **human)
    assert status == "409 Conflict"
    assert payload["code"] == "invalid_transition"


def test_resume_when_not_paused_is_conflict() -> None:
    app, *_ = _build()
    human = _human_environ()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    match_id = created["match_id"]

    status, _, payload = call(app, "POST", f"/api/v1/matches/{match_id}/resume", **human)
    assert status == "409 Conflict"
    assert payload["code"] == "invalid_transition"


# --- pause -> simulated restart -> resume (h6) ------------------------------


def test_pause_then_restart_then_resume_restores_identical_state() -> None:
    app, match_store, token_store, ledger_store = _build()
    human = _human_environ(subject="restart-human")

    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    match_id = created["match_id"]
    call(
        app,
        "POST",
        f"/api/v1/matches/{match_id}/turns",
        body={"action": {"points": 2}},
        **human,
    )
    status, _, paused = call(app, "POST", f"/api/v1/matches/{match_id}/pause", **human)
    assert status == "200 OK"
    assert paused["status"] == "paused"

    # Simulate a process restart: a brand-new MatchStore instance,
    # rehydrated only from the first store's serialized items.
    restarted_store = InMemoryMatchStore()
    restarted_store._items = copy.deepcopy(match_store._items)
    restarted_app = with_api(
        _passthrough,
        match_store=restarted_store,
        token_store=token_store,
        ledger_store=ledger_store,
    )

    status, _, reloaded = call(restarted_app, "GET", f"/api/v1/matches/{match_id}")
    assert status == "200 OK"
    assert reloaded == paused

    status, _, resumed = call(restarted_app, "POST", f"/api/v1/matches/{match_id}/resume", **human)
    assert status == "200 OK"
    assert resumed["status"] == "active"
    assert resumed["state"] == paused["state"]
    assert resumed["turns"] == paused["turns"]


# --- score -------------------------------------------------------------------


def test_score_before_completion_is_conflict() -> None:
    app, *_ = _build()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **_human_environ())
    status, _, payload = call(app, "GET", f"/api/v1/matches/{created['match_id']}/score")
    assert status == "409 Conflict"
    assert payload["code"] == "not_completed"


def test_score_after_completion_is_public_and_anonymous_can_read_it() -> None:
    app, *_ = _build()
    human = _human_environ()
    _, _, created = call(app, "POST", "/api/v1/matches", body={}, **human)
    match_id = created["match_id"]
    participant_id = created["participants"][0]["participant_id"]
    _play_out(app, match_id, {participant_id: human})

    status, _, payload = call(app, "GET", f"/api/v1/matches/{match_id}/score")
    assert status == "200 OK"
    assert payload["result"]["completed"] is True


# --- leaderboard (h7) ---------------------------------------------------------


def test_leaderboard_is_public_and_empty_by_default() -> None:
    app, *_ = _build()
    status, _, payload = call(app, "GET", "/api/v1/leaderboard")
    assert status == "200 OK"
    assert payload["leaderboard"] == []


def test_leaderboard_reflects_a_completed_rated_match_on_the_next_get() -> None:
    app, _, token_store, _ = _build()
    human = _human_environ(subject="rated-human", display="Ada")
    body = {
        "opponent": {
            "kind": "agent",
            "display_name": "Rival",
            "agent_name": "Rival",
            "model": "gpt-5",
            "provider": "openai",
        }
    }
    _, _, created = call(app, "POST", "/api/v1/matches", body=body, **human)
    match_id = created["match_id"]
    human_pid, agent_pid = (p["participant_id"] for p in created["participants"])
    agent = _issue_agent(token_store, agent_name="Rival", model="gpt-5", provider="openai")

    status, _, before = call(app, "GET", "/api/v1/leaderboard")
    assert status == "200 OK"
    assert before["leaderboard"] == []

    _play_out(app, match_id, {human_pid: human, agent_pid: agent})

    status, _, after = call(app, "GET", "/api/v1/leaderboard")
    assert status == "200 OK"
    assert len(after["leaderboard"]) == 2
    names = {row["display_name"] for row in after["leaderboard"]}
    assert names == {"Ada", "Rival"}


def test_leaderboard_limit_query_param() -> None:
    app, _, token_store, ledger_store = _build()
    human = _human_environ(subject="limit-human", display="Ada")
    body = {
        "opponent": {
            "kind": "agent",
            "display_name": "Rival",
            "agent_name": "Rival",
            "model": "gpt-5",
            "provider": "openai",
        }
    }
    _, _, created = call(app, "POST", "/api/v1/matches", body=body, **human)
    human_pid, agent_pid = (p["participant_id"] for p in created["participants"])
    agent = _issue_agent(token_store, agent_name="Rival", model="gpt-5", provider="openai")
    _play_out(app, created["match_id"], {human_pid: human, agent_pid: agent})

    status, _, payload = call(app, "GET", "/api/v1/leaderboard", query="limit=1")
    assert status == "200 OK"
    assert len(payload["leaderboard"]) == 1


def test_leaderboard_rejects_a_non_integer_limit() -> None:
    app, *_ = _build()
    status, _, payload = call(app, "GET", "/api/v1/leaderboard", query="limit=nope")
    assert status == "400 Bad Request"


def test_leaderboard_rejects_a_negative_limit() -> None:
    app, *_ = _build()
    status, _, payload = call(app, "GET", "/api/v1/leaderboard", query="limit=-1")
    assert status == "400 Bad Request"


# --- routing edge cases --------------------------------------------------


def test_unknown_api_route_is_404() -> None:
    app, *_ = _build()
    status, _, payload = call(app, "GET", "/api/v1/nope")
    assert status == "404 Not Found"
    assert payload["code"] == "not_found"


def test_wrong_method_on_a_known_route_is_405() -> None:
    app, *_ = _build()
    status, _, payload = call(app, "GET", "/api/v1/matches")
    assert status == "405 Method Not Allowed"
    assert payload["code"] == "method_not_allowed"


def test_wrong_method_on_leaderboard_is_405() -> None:
    app, *_ = _build()
    status, _, payload = call(app, "POST", "/api/v1/leaderboard", body={})
    assert status == "405 Method Not Allowed"
