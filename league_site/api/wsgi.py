"""The match API: a pure-WSGI JSON surface mounted under ``/api/v1``.

:func:`with_api` is middleware in the same style as
:func:`league_site.auth.wsgi.with_auth` and
:func:`league_site.web.shell.with_shell`: it wraps an existing WSGI app,
handles every path starting with :data:`API_PREFIX` itself, and passes
everything else straight through to the wrapped app unchanged.

Composition order (see :func:`league_site.web.http.site_app`)::

    with_shell(with_auth(with_api(http_app())))

``with_api`` must be mounted *inside* ``with_auth`` — closer to the
innermost app — so that by the time a request reaches here,
``environ[league_site.auth.wsgi.SESSION_ENVIRON_KEY]`` has already been
populated with the request's verified human session (or ``None``); see
:mod:`league_site.api.identity` for how that, plus an
``Authorization: Bearer`` header, resolve to one
:class:`~league_site.api.identity.RequestIdentity`. ``with_shell`` stays
outermost and leaves every ``/api/v1/*`` response alone: it only ever
shells responses whose ``Content-Type`` is ``text/markdown`` (see that
module's docstring), and every response from here is
``application/json``.

Routes
------
* ``POST /api/v1/matches`` — create a match (identity required). Body:
  ``{"mode": "<game_id>", "opponent": {...}}``, both optional. ``mode``
  selects a :class:`~league_site.matches.engine.GameEngine` factory from
  the injected ``engine_registry`` (default:
  :data:`league_site.api.engines.DEFAULT_ENGINE_REGISTRY`, a single
  built-in stub engine — the real grid adapter registers into a registry
  of this same shape post-merge). The requester becomes the first
  participant; ``opponent`` (see
  :func:`league_site.api.identity.participant_for_opponent_spec`) adds a
  second. Omitting ``opponent`` creates a solo "practice" match against
  the engine itself — not rated on completion, since rating a match needs
  at least two participants.
* ``GET /api/v1/matches/<id>`` — full match state (participants, turn
  history, current game state, ``legal_actions`` passed through from the
  engine's state when present, and the result once completed). Public:
  anyone, including anonymous requests, may spectate.
* ``POST /api/v1/matches/<id>/turns`` — submit ``{"action": ...}``.
  Participant-only. Auto-completes the match (and records a rating
  update, for a two-or-more-participant match) the instant the engine
  reports ``is_over`` — there is no separate "complete" endpoint.
* ``POST /api/v1/matches/<id>/pause`` / ``.../resume`` — participant-only.
* ``GET /api/v1/matches/<id>/score`` — public once the match is
  ``completed``; ``409`` before that.
* ``GET /api/v1/leaderboard?limit=N`` — public. Reads the injected
  :class:`~league_site.ratings.ledger.RatingLedgerStore` via
  :func:`league_site.ratings.leaderboard.leaderboard`.

Every failure — auth, ownership, validation, not-found, wrong match state
— raises :class:`~league_site.api.errors.ApiError`, caught once by
:func:`with_api` and rendered as ``{"code": ..., "message": ...}`` JSON at
the error's status line. Non-participant write attempts (another human,
another agent, or anonymous) and unauthenticated write attempts on an
already-loaded match both render as ``403`` uniformly (see
:func:`league_site.api.errors.forbidden`) — creating a match is the one
endpoint that instead needs *some* identity to exist at all, so it alone
raises ``401`` (:func:`league_site.api.errors.unauthorized`) for an
anonymous request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import parse_qs

from league_site.api import errors
from league_site.api.engines import DEFAULT_ENGINE_REGISTRY, DEFAULT_MODE
from league_site.api.identity import (
    RequestIdentity,
    participant_for_identity,
    participant_for_opponent_spec,
    resolve_identity,
)
from league_site.auth.token_store import InMemoryTokenStore, TokenStore
from league_site.matches import (
    GameEngine,
    InMemoryMatchStore,
    InvalidTransitionError,
    Match,
    MatchNotFoundError,
    MatchResult,
    MatchStatus,
    MatchStore,
    Participant,
    TurnRecord,
)
from league_site.ratings.leaderboard import LeaderboardRow, leaderboard
from league_site.ratings.ledger import InMemoryRatingLedgerStore, RatingLedgerStore
from league_site.ratings.system import IntegerEloRatingSystem, RatingSystem, outcome_from_match

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]
EngineRegistry = Mapping[str, Callable[[], GameEngine]]

#: The path prefix :func:`with_api` claims; anything else is passed through
#: to the wrapped app untouched.
API_PREFIX = "/api/v1"

_MATCHES_PATH = re.compile(r"^/api/v1/matches/?$")
_MATCH_ITEM_PATH = re.compile(r"^/api/v1/matches/(?P<match_id>[^/]+)$")
_MATCH_TURNS_PATH = re.compile(r"^/api/v1/matches/(?P<match_id>[^/]+)/turns$")
_MATCH_PAUSE_PATH = re.compile(r"^/api/v1/matches/(?P<match_id>[^/]+)/pause$")
_MATCH_RESUME_PATH = re.compile(r"^/api/v1/matches/(?P<match_id>[^/]+)/resume$")
_MATCH_SCORE_PATH = re.compile(r"^/api/v1/matches/(?P<match_id>[^/]+)/score$")
_LEADERBOARD_PATH = "/api/v1/leaderboard"


def with_api(
    app: WSGIApp,
    *,
    match_store: MatchStore | None = None,
    token_store: TokenStore | None = None,
    ledger_store: RatingLedgerStore | None = None,
    engine_registry: EngineRegistry | None = None,
    rating_system: RatingSystem | None = None,
) -> WSGIApp:
    """Wrap *app* with the ``/api/v1/*`` routes described in the module docstring.

    Every store/registry is injectable and defaults to a fresh in-memory
    reference implementation, so a bare ``with_api(app)`` is a complete,
    self-contained match API (what :func:`league_site.web.http.site_app`
    uses by default) while tests can inject their own instances — e.g. to
    pre-issue agent tokens on the ``token_store``, or to simulate a
    process restart by handing a *new* :class:`~league_site.matches.
    store.InMemoryMatchStore` a copy of a previous one's serialized items.
    """
    matches = match_store if match_store is not None else InMemoryMatchStore()
    agent_tokens = token_store if token_store is not None else InMemoryTokenStore()
    ledger = ledger_store if ledger_store is not None else InMemoryRatingLedgerStore()
    registry: EngineRegistry = (
        dict(engine_registry) if engine_registry is not None else dict(DEFAULT_ENGINE_REGISTRY)
    )
    ratings = rating_system if rating_system is not None else IntegerEloRatingSystem()

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        path = environ.get("PATH_INFO", "/")
        if not path.startswith(API_PREFIX):
            return app(environ, start_response)

        method = environ.get("REQUEST_METHOD", "GET").upper()
        try:
            status, payload = _dispatch(
                method,
                path,
                environ,
                matches=matches,
                agent_tokens=agent_tokens,
                ledger=ledger,
                registry=registry,
                ratings=ratings,
            )
        except errors.ApiError as exc:
            return _json_response(
                start_response, exc.status, {"code": exc.code, "message": str(exc)}
            )
        return _json_response(start_response, status, payload)

    return application


# --- routing -----------------------------------------------------------------


def _dispatch(
    method: str,
    path: str,
    environ: dict[str, Any],
    *,
    matches: MatchStore,
    agent_tokens: TokenStore,
    ledger: RatingLedgerStore,
    registry: EngineRegistry,
    ratings: RatingSystem,
) -> tuple[str, Any]:
    if path == _LEADERBOARD_PATH:
        _require_method(method, "GET")
        return _handle_leaderboard(environ, ledger)

    if _MATCHES_PATH.match(path):
        _require_method(method, "POST")
        return _handle_create_match(environ, matches, agent_tokens, registry)

    turns_match = _MATCH_TURNS_PATH.match(path)
    if turns_match:
        _require_method(method, "POST")
        return _handle_take_turn(
            turns_match.group("match_id"), environ, matches, agent_tokens, registry, ledger, ratings
        )

    pause_match = _MATCH_PAUSE_PATH.match(path)
    if pause_match:
        _require_method(method, "POST")
        return _handle_pause(pause_match.group("match_id"), environ, matches, agent_tokens)

    resume_match = _MATCH_RESUME_PATH.match(path)
    if resume_match:
        _require_method(method, "POST")
        return _handle_resume(resume_match.group("match_id"), environ, matches, agent_tokens)

    score_match = _MATCH_SCORE_PATH.match(path)
    if score_match:
        _require_method(method, "GET")
        return _handle_score(score_match.group("match_id"), matches)

    item_match = _MATCH_ITEM_PATH.match(path)
    if item_match:
        _require_method(method, "GET")
        return _handle_get_match(item_match.group("match_id"), matches)

    raise errors.not_found(f"no API route for {path!r}")


def _require_method(method: str, expected: str) -> None:
    if method != expected:
        raise errors.method_not_allowed(f"expected {expected}, got {method}")


# --- handlers ------------------------------------------------------------


def _handle_create_match(
    environ: dict[str, Any],
    matches: MatchStore,
    agent_tokens: TokenStore,
    registry: EngineRegistry,
) -> tuple[str, Any]:
    identity = _require_identity(environ, agent_tokens)
    body = _read_json_body(environ)

    mode = body.get("mode", DEFAULT_MODE)
    if not isinstance(mode, str) or not mode:
        raise errors.bad_request("mode must be a non-empty string")
    engine = _engine_for(registry, mode)

    creator = participant_for_identity(identity)
    participants = [creator]
    opponent_spec = body.get("opponent")
    if opponent_spec is not None:
        try:
            opponent = participant_for_opponent_spec(opponent_spec)
        except ValueError as exc:
            raise errors.bad_request(str(exc)) from exc
        if opponent.participant_id == creator.participant_id:
            raise errors.bad_request("opponent must be a different identity than the creator")
        participants.append(opponent)

    match = Match.create(game_id=mode, participants=participants)
    match.start(engine)
    matches.save(match)
    return "201 Created", _match_view(match)


def _handle_get_match(match_id: str, matches: MatchStore) -> tuple[str, Any]:
    match = _load_match(matches, match_id)
    return "200 OK", _match_view(match)


def _handle_take_turn(
    match_id: str,
    environ: dict[str, Any],
    matches: MatchStore,
    agent_tokens: TokenStore,
    registry: EngineRegistry,
    ledger: RatingLedgerStore,
    ratings: RatingSystem,
) -> tuple[str, Any]:
    match = _load_match(matches, match_id)
    identity = _require_participant(environ, agent_tokens, match)

    body = _read_json_body(environ)
    action = body.get("action")
    engine = _engine_for(registry, match.game_id)

    try:
        match.take_turn(engine, identity.key, action)
    except InvalidTransitionError as exc:
        raise errors.conflict(str(exc), code="invalid_transition") from exc
    except ValueError as exc:
        raise errors.bad_request(str(exc), code="illegal_action") from exc

    if engine.is_over(match.game_state):
        match.complete(engine)
        _record_rating(match, ledger, ratings)

    matches.save(match)
    return "200 OK", _match_view(match)


def _handle_pause(
    match_id: str, environ: dict[str, Any], matches: MatchStore, agent_tokens: TokenStore
) -> tuple[str, Any]:
    match = _load_match(matches, match_id)
    _require_participant(environ, agent_tokens, match)
    try:
        match.pause()
    except InvalidTransitionError as exc:
        raise errors.conflict(str(exc), code="invalid_transition") from exc
    matches.save(match)
    return "200 OK", _match_view(match)


def _handle_resume(
    match_id: str, environ: dict[str, Any], matches: MatchStore, agent_tokens: TokenStore
) -> tuple[str, Any]:
    match = _load_match(matches, match_id)
    _require_participant(environ, agent_tokens, match)
    try:
        match.resume()
    except InvalidTransitionError as exc:
        raise errors.conflict(str(exc), code="invalid_transition") from exc
    matches.save(match)
    return "200 OK", _match_view(match)


def _handle_score(match_id: str, matches: MatchStore) -> tuple[str, Any]:
    match = _load_match(matches, match_id)
    if match.status is not MatchStatus.COMPLETED:
        raise errors.conflict("match is not completed yet", code="not_completed")
    return "200 OK", {
        "match_id": match.match_id,
        "status": match.status.value,
        "result": _result_view(match.result),
    }


def _handle_leaderboard(environ: dict[str, Any], ledger: RatingLedgerStore) -> tuple[str, Any]:
    limit = _parse_limit(environ.get("QUERY_STRING", ""))
    rows = leaderboard(ledger, limit=limit)
    return "200 OK", {"leaderboard": [_leaderboard_row_view(row) for row in rows]}


# --- shared handler helpers ------------------------------------------------


def _load_match(matches: MatchStore, match_id: str) -> Match:
    try:
        return matches.load(match_id)
    except MatchNotFoundError as exc:
        raise errors.not_found(str(exc)) from exc


def _require_identity(environ: dict[str, Any], agent_tokens: TokenStore) -> RequestIdentity:
    """Resolve the request's identity, raising ``401`` if there is none at all."""
    identity = resolve_identity(environ, agent_tokens)
    if identity is None:
        raise errors.unauthorized()
    return identity


def _require_participant(
    environ: dict[str, Any], agent_tokens: TokenStore, match: Match
) -> RequestIdentity:
    """Resolve the request's identity and require it own a participant of *match*.

    Anonymous requests and requests from a real-but-uninvolved identity
    both raise the same ``403`` (see :func:`league_site.api.errors.forbidden`)
    — a participant-only endpoint never distinguishes "no identity" from
    "wrong identity".
    """
    identity = resolve_identity(environ, agent_tokens)
    participant_ids = {participant.participant_id for participant in match.participants}
    if identity is None or identity.key not in participant_ids:
        raise errors.forbidden()
    return identity


def _engine_for(registry: EngineRegistry, mode: str) -> GameEngine:
    factory = registry.get(mode)
    if factory is None:
        raise errors.bad_request(f"unknown game mode {mode!r}", code="unknown_mode")
    return factory()


def _record_rating(match: Match, ledger: RatingLedgerStore, ratings: RatingSystem) -> None:
    """Apply a rating update for a just-completed *match*, if it can be rated.

    A match needs at least two scored participants to be rated (see
    :meth:`~league_site.ratings.system.IntegerEloRatingSystem.compute_deltas`);
    a solo "practice" match (opponent omitted at creation) never reaches
    that bar and is silently left off the leaderboard.
    """
    outcome = outcome_from_match(match)
    if len(outcome.entries) < 2:
        return
    ledger.record_match(outcome, ratings)


def _parse_limit(query_string: str) -> int | None:
    values = parse_qs(query_string).get("limit")
    if not values:
        return None
    try:
        limit = int(values[0])
    except ValueError as exc:
        raise errors.bad_request("limit must be an integer") from exc
    if limit < 0:
        raise errors.bad_request("limit must be non-negative")
    return limit


def _read_json_body(environ: dict[str, Any]) -> dict[str, Any]:
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    raw = environ["wsgi.input"].read(length) if length > 0 else b""
    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise errors.bad_request(f"invalid JSON body: {exc}") from exc
    if not isinstance(data, dict):
        raise errors.bad_request("request body must be a JSON object")
    return data


# --- response shaping --------------------------------------------------------


def _participant_view(participant: Participant) -> dict[str, Any]:
    view: dict[str, Any] = {
        "participant_id": participant.participant_id,
        "display_name": participant.display_name,
        "kind": participant.kind.value,
    }
    if participant.agent_identity is not None:
        view["model"] = participant.agent_identity.model
        view["provider"] = participant.agent_identity.provider
    return view


def _turn_view(turn: TurnRecord) -> dict[str, Any]:
    return {
        "turn_number": turn.turn_number,
        "participant_id": turn.participant_id,
        "action": turn.action,
        "timestamp": turn.timestamp.isoformat(),
    }


def _result_view(result: MatchResult | None) -> dict[str, Any] | None:
    if result is None:
        return None
    return {
        "completed": result.completed,
        "winner_participant_id": result.winner_participant_id,
        "scores": dict(result.scores),
        "summary": result.summary,
    }


def _match_view(match: Match) -> dict[str, Any]:
    state = match.game_state
    legal_actions = state.get("legal_actions") if isinstance(state, dict) else None
    return {
        "match_id": match.match_id,
        "game_id": match.game_id,
        "status": match.status.value,
        "participants": [_participant_view(participant) for participant in match.participants],
        "turns": [_turn_view(turn) for turn in match.turns],
        "state": state,
        "legal_actions": legal_actions,
        "result": _result_view(match.result),
        "created_at": match.created_at.isoformat(),
        "updated_at": match.updated_at.isoformat(),
    }


def _leaderboard_row_view(row: LeaderboardRow) -> dict[str, Any]:
    identity = row.identity
    return {
        "rank": row.rank,
        "kind": identity.kind.value,
        "display_name": identity.display_name,
        "model": identity.model,
        "provider": identity.provider,
        "rating": row.rating,
        "match_count": row.match_count,
    }


def _json_response(start_response: Any, status: str, payload: Any) -> list[bytes]:
    body = json.dumps(payload).encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]
