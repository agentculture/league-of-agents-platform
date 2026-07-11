"""``with_play`` — the ``/play*`` routes: the browser play surface.

Middleware in the same style as :func:`league_site.api.wsgi.with_api`: wraps
an existing WSGI app, handles every path equal to or under ``/play``
itself, and passes everything else straight through unchanged.

Composition order (see :func:`league_site.web.http.site_app`)::

    with_shell(with_auth(with_play(with_api(http_app()))))

``with_play`` MUST be mounted *inside* ``with_auth`` — this surface is the
whole reason GitHub sign-in exists for humans, and it reads the verified
session straight from ``environ[league_site.auth.wsgi.SESSION_ENVIRON_KEY]``
(populated by ``with_auth`` on every request) rather than re-implementing
cookie verification. It sits *outside*/alongside ``with_api`` only in the
nesting sense; the two claim disjoint path prefixes. ``with_shell`` stays
outermost and leaves every ``/play*`` response alone: these pages render
their own self-contained HTML (the same standalone shell the viewer uses —
:func:`league_site.viewer.wsgi.page_shell` — here with the session passed
through, so the header carries the signed-in chip), and ``with_shell`` only
ever shells ``text/markdown`` responses.

Route contract, error rendering (always HTML here, never JSON), and the
CSRF stance are documented on the package
(:mod:`league_site.play`); the create/turn flows are the shared ones the
JSON API drives (:mod:`league_site.api.matchops`) — capacity gate, mode
validation, engine error translation, auto-completion, and rating included.

Non-participants (signed-in-but-uninvolved humans and anonymous visitors
alike) who ``GET`` a play view are redirected to the public spectate page —
``/matches/<id>/watch`` exists precisely for them; a play view without your
own move to make *is* the watch page. Writes are never redirected: a
signed-out POST is a ``401`` naming the sign-in path, a non-participant
POST a ``403`` linking the spectate page, so no state-changing request ever
silently turns into a page view.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Mapping
from typing import Any, Callable
from urllib.parse import parse_qs

from league_site.api import errors, matchops
from league_site.api.identity import identity_for_session
from league_site.api.matchops import EngineRegistry
from league_site.api.registry import default_engine_registry
from league_site.auth import sessions
from league_site.auth.wsgi import SESSION_ENVIRON_KEY
from league_site.capacity.config import CapacityConfig
from league_site.matches import (
    InMemoryMatchStore,
    Match,
    MatchNotFoundError,
    MatchStatus,
    MatchStore,
)
from league_site.play.actions import ActionChoice, action_choices, is_waiting, match_choice
from league_site.play.board import build_overlay
from league_site.play.render import (
    render_board_lead,
    render_error_body,
    render_hub_body,
    render_play_panel,
    render_signed_out_hub_body,
)
from league_site.ratings.ledger import InMemoryRatingLedgerStore, RatingLedgerStore
from league_site.ratings.system import IntegerEloRatingSystem, RatingSystem
from league_site.viewer.board import BoardOverlay, build_board_model, render_board
from league_site.viewer.render import build_page_model, render_page_body
from league_site.viewer.wsgi import page_shell

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

logger = logging.getLogger(__name__)

#: The launch modes the play surface offers a human (filtered against the
#: injected engine registry at construction). Deliberately narrower than
#: the registry itself: every other registered mode either has no bot side
#: for a lone human to play against (``team-vs-team``, ``coop-2`` need more
#: participants than a solo browser session brings) or publishes
#: ``legal_actions`` the browser form cannot submit (the built-in stub —
#: see :mod:`league_site.play.actions`).
PLAY_MODES: tuple[str, ...] = ("solo-vs-bot",)

_HUB_PATH = "/play"
_CREATE_PATH = "/play/matches"
_MATCH_PATH_RE = re.compile(r"^/play/matches/(?P<match_id>[^/]+)$")
_TURNS_PATH_RE = re.compile(r"^/play/matches/(?P<match_id>[^/]+)/turns$")

_SITE_TITLE = "League of Agents"
#: Mirrors the viewer's live-page refresh cadence exactly (see
#: :mod:`league_site.viewer.wsgi`): one meta tag, no JS.
_REFRESH_SECONDS = 5
_REFRESH_META = f'<meta http-equiv="refresh" content="{_REFRESH_SECONDS}">\n'

_SIGN_IN_LINKS: tuple[tuple[str, str], ...] = (("/auth/login/github", "Sign in with GitHub"),)

#: Match statuses the hub lists as "yours to resume".
_LIVE_STATUSES = (MatchStatus.ACTIVE, MatchStatus.PAUSED)


def with_play(
    app: WSGIApp,
    *,
    match_store: MatchStore | None = None,
    ledger_store: RatingLedgerStore | None = None,
    engine_registry: EngineRegistry | None = None,
    rating_system: RatingSystem | None = None,
    capacity_config: CapacityConfig | None = None,
) -> WSGIApp:
    """Wrap *app* with the ``/play*`` routes described in the module docstring.

    Every keyword mirrors :func:`league_site.api.wsgi.with_api` — same
    defaults, same construction-time ``CapacityConfig.from_env()`` read —
    and :func:`league_site.web.http.site_app` passes the *same*
    ``match_store``/``ledger_store``/``engine_registry`` instances to both,
    so a match started in the browser is immediately visible to the JSON
    API, the viewer, and the profiles pages, and vice versa.
    """
    matches = match_store if match_store is not None else InMemoryMatchStore()
    ledger = ledger_store if ledger_store is not None else InMemoryRatingLedgerStore()
    registry: EngineRegistry = (
        dict(engine_registry) if engine_registry is not None else default_engine_registry()
    )
    ratings = rating_system if rating_system is not None else IntegerEloRatingSystem()
    capacity = capacity_config if capacity_config is not None else CapacityConfig.from_env()
    offered_modes = tuple(mode for mode in PLAY_MODES if mode in registry)

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        path = environ.get("PATH_INFO", "/")
        if path != _HUB_PATH and not path.startswith(_HUB_PATH + "/"):
            return app(environ, start_response)

        method = environ.get("REQUEST_METHOD", "GET").upper()
        session = environ.get(SESSION_ENVIRON_KEY)
        try:
            return _dispatch(
                start_response,
                method,
                path,
                environ,
                session,
                matches=matches,
                ledger=ledger,
                registry=registry,
                ratings=ratings,
                capacity=capacity,
                offered_modes=offered_modes,
            )
        except errors.ApiError as exc:
            # The shared flows (league_site.api.matchops) raise the same
            # structured errors the JSON API renders as its envelope; this
            # surface renders them as an honest HTML page instead.
            return _error_page(start_response, exc.status, str(exc), session)
        except Exception:
            logger.exception("unhandled exception dispatching %s %s", method, path)
            return _error_page(
                start_response,
                "500 Internal Server Error",
                "something went wrong on our side — the failure has been logged",
                session,
            )

    return application


# --- routing -----------------------------------------------------------------


def _dispatch(
    start_response: Any,
    method: str,
    path: str,
    environ: dict[str, Any],
    session: sessions.Session | None,
    *,
    matches: MatchStore,
    ledger: RatingLedgerStore,
    registry: EngineRegistry,
    ratings: RatingSystem,
    capacity: CapacityConfig,
    offered_modes: tuple[str, ...],
) -> list[bytes]:
    if path == _HUB_PATH:
        _require_method(method, "GET")
        return _hub(start_response, session, matches, offered_modes)

    if path == _CREATE_PATH:
        _require_method(method, "POST")
        return _create(start_response, session, environ, matches, registry, capacity, offered_modes)

    turns_match = _TURNS_PATH_RE.match(path)
    if turns_match:
        _require_method(method, "POST")
        return _submit_turn(
            start_response,
            session,
            turns_match.group("match_id"),
            environ,
            matches,
            registry,
            ledger,
            ratings,
        )

    view_match = _MATCH_PATH_RE.match(path)
    if view_match:
        _require_method(method, "GET")
        return _play_view(
            start_response, session, view_match.group("match_id"), environ, matches, ledger
        )

    raise errors.not_found(f"no play page at {path!r}")


def _require_method(method: str, expected: str) -> None:
    if method != expected:
        raise errors.method_not_allowed(f"expected {expected}, got {method}")


# --- handlers ------------------------------------------------------------------


def _hub(
    start_response: Any,
    session: sessions.Session | None,
    matches: MatchStore,
    offered_modes: tuple[str, ...],
) -> list[bytes]:
    if session is None:
        body = render_signed_out_hub_body()
        description = "Sign in with GitHub to play League of Agents in the browser."
    else:
        identity = identity_for_session(session)
        live = _live_matches_for(matches, identity.key)
        body = render_hub_body(offered_modes, live)
        description = "Start a match or resume one of yours — League of Agents."
    page = page_shell(
        title=f"Play — {_SITE_TITLE}",
        description=description,
        body_html=body,
        session=session,
    )
    return _html_response(start_response, "200 OK", page)


def _create(
    start_response: Any,
    session: sessions.Session | None,
    environ: dict[str, Any],
    matches: MatchStore,
    registry: EngineRegistry,
    capacity: CapacityConfig,
    offered_modes: tuple[str, ...],
) -> list[bytes]:
    if session is None:
        return _error_page(
            start_response,
            "401 Unauthorized",
            "starting a match requires signing in with GitHub first",
            None,
        )
    matchops.check_create_capacity(matches, capacity)
    form = _read_form(environ)
    mode = form.get("mode", "")
    if mode not in offered_modes:
        offered = ", ".join(offered_modes) or "none right now"
        raise errors.bad_request(
            f"mode must be one of the browser-playable modes ({offered}); got {mode!r}",
            code="unknown_mode",
        )
    match = matchops.create_match(
        identity_for_session(session),
        matches=matches,
        registry=registry,
        mode=mode,
        opponent_spec=None,
    )
    return _redirect(start_response, "303 See Other", f"/play/matches/{match.match_id}")


def _play_view(
    start_response: Any,
    session: sessions.Session | None,
    match_id: str,
    environ: dict[str, Any],
    matches: MatchStore,
    ledger: RatingLedgerStore,
) -> list[bytes]:
    match = _load_match(matches, match_id)
    watch_href = f"/matches/{match.match_id}/watch"
    identity = identity_for_session(session) if session is not None else None
    if identity is None or not _is_participant(match, identity.key):
        # Nothing here is theirs to act on; the public spectate page is the
        # same board without the move form (see the module docstring).
        return _redirect(start_response, "302 Found", watch_href)

    choices: tuple[ActionChoice, ...] = ()
    waiting = False
    if match.status is MatchStatus.ACTIVE:
        choices = action_choices(match.game_state, identity.key)
        waiting = is_waiting(match.game_state, identity.key)
    show_form = match.status is MatchStatus.ACTIVE and bool(choices) and not waiting

    model = build_page_model(match, ledger)
    play_path = f"/play/matches/{match.match_id}"
    board_html, overlay = _board_section(
        match, identity.key, choices, show_form, environ, play_path
    )
    panel = render_play_panel(
        match,
        choices=choices,
        show_form=show_form,
        waiting=waiting,
        watch_href=watch_href,
        collapse_form=overlay is not None,
    )
    slot = f"{board_html}\n{panel}" if board_html else panel
    body = render_page_body(model, board_html=slot)

    # Refresh exactly when someone else's move could change the page under
    # us: live match, no form on screen. When it's the human's turn the
    # page holds still under their hands; a finished match never refreshes.
    refresh_meta = _REFRESH_META if model.is_live and not show_form else ""
    match_id_html = html.escape(match.match_id)
    page = page_shell(
        title=f"Play match {match_id_html} — {_SITE_TITLE}",
        description=f"Play match {match_id_html} on League of Agents.",
        body_html=body,
        refresh_meta=refresh_meta,
        session=session,
    )
    return _html_response(start_response, "200 OK", page)


def _board_section(
    match: Match,
    identity_key: str,
    choices: tuple[ActionChoice, ...],
    show_form: bool,
    environ: dict[str, Any],
    play_path: str,
) -> tuple[str | None, BoardOverlay | None]:
    """The play view's board fragment (hint + board), and its overlay if any.

    The board renders whenever the match's game state is board-shaped
    (:func:`league_site.viewer.board.build_board_model`) — waiting turns and
    final positions included — but the *interaction* overlay only exists
    while it's the human's move (*show_form*) and the current choices anchor
    to board cells (:func:`league_site.play.board.build_overlay`); otherwise
    the same static board a spectator sees renders here. The signed-in
    human's own team always wears the accent treatment, so "your pieces"
    reads at a glance. ``?unit=`` selects a unit — a safe, idempotent GET,
    which is why selection is a link rather than a form.
    """
    board_model = build_board_model(match.game_state)
    if board_model is None:
        return None, None
    overlay: BoardOverlay | None = None
    if show_form:
        overlay = build_overlay(
            board_model,
            choices,
            selected_unit=_selected_unit(environ),
            base_path=play_path,
            form_action=f"{play_path}/turns",
        )
    parts = []
    if overlay is not None:
        role = next(
            (unit.role for unit in board_model.units if unit.unit_id == overlay.selected_unit),
            None,
        )
        parts.append(
            render_board_lead(
                selected_unit=overlay.selected_unit, selected_role=role, clear_href=play_path
            )
        )
    parts.append(
        render_board(board_model, overlay=overlay, accent_team=_team_for(match, identity_key))
    )
    return "\n".join(parts), overlay


def _selected_unit(environ: dict[str, Any]) -> str | None:
    values = parse_qs(environ.get("QUERY_STRING", "")).get("unit")
    return values[0] if values else None


def _team_for(match: Match, identity_key: str) -> str | None:
    state = match.game_state
    teams = state.get("participant_teams") if isinstance(state, Mapping) else None
    team = teams.get(identity_key) if isinstance(teams, Mapping) else None
    return team if isinstance(team, str) and team else None


def _submit_turn(
    start_response: Any,
    session: sessions.Session | None,
    match_id: str,
    environ: dict[str, Any],
    matches: MatchStore,
    registry: EngineRegistry,
    ledger: RatingLedgerStore,
    ratings: RatingSystem,
) -> list[bytes]:
    match = _load_match(matches, match_id)
    if session is None:
        return _error_page(
            start_response,
            "401 Unauthorized",
            "submitting a move requires signing in with GitHub first",
            None,
        )
    identity = identity_for_session(session)
    if not _is_participant(match, identity.key):
        body = render_error_body(
            "403 Forbidden",
            "you are not a participant of this match — you can watch it, though",
            links=((f"/matches/{match.match_id}/watch", "Watch this match"),),
        )
        return _html_response(
            start_response, "403 Forbidden", _error_shell("403 Forbidden", body, session)
        )
    if match.status is not MatchStatus.ACTIVE:
        raise errors.conflict(
            f"cannot take_turn a match in status {match.status.value!r}",
            code="invalid_transition",
        )

    form = _read_form(environ)
    choices = action_choices(match.game_state, identity.key)
    choice = match_choice(choices, form.get("action"))
    if choice is None:
        # Never trust the submitted string: whatever arrived is not one of
        # the current legal actions, so no engine ever sees it.
        raise errors.bad_request(
            "the submitted action is not one of the current legal actions",
            code="illegal_action",
        )

    matchops.take_turn(
        match,
        identity.key,
        choice.action,
        matches=matches,
        registry=registry,
        ledger=ledger,
        ratings=ratings,
    )
    # POST-redirect-GET: a refresh of the landing page can never re-submit.
    return _redirect(start_response, "303 See Other", f"/play/matches/{match.match_id}")


# --- shared handler helpers ------------------------------------------------


def _load_match(matches: MatchStore, match_id: str) -> Match:
    try:
        return matches.load(match_id)
    except MatchNotFoundError as exc:
        raise errors.not_found(str(exc)) from exc


def _is_participant(match: Match, identity_key: str) -> bool:
    return any(p.participant_id == identity_key for p in match.participants)


def _live_matches_for(matches: MatchStore, identity_key: str) -> tuple[Match, ...]:
    """The human's own live (active/paused) matches, most recently touched first.

    An O(n) scan over ``list_ids`` + ``load`` — the same accepted
    launch-scale tradeoff :func:`league_site.capacity.guard.check_capacity`
    documents (n is bounded by ``max_stored_matches``, so the scan stays
    cheap by construction).
    """
    live: list[Match] = []
    for match_id in matches.list_ids():
        try:
            match = matches.load(match_id)
        except MatchNotFoundError:  # pragma: no cover - racing a delete
            continue
        if match.status in _LIVE_STATUSES and _is_participant(match, identity_key):
            live.append(match)
    live.sort(key=lambda match: match.updated_at, reverse=True)
    return tuple(live)


def _read_form(environ: dict[str, Any]) -> dict[str, str]:
    """The urlencoded form body of *environ*, first value per field."""
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except (TypeError, ValueError):
        length = 0
    raw = environ["wsgi.input"].read(length) if length > 0 else b""
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise errors.bad_request(f"undecodable form body: {exc}") from exc
    return {key: values[0] for key, values in parse_qs(text, keep_blank_values=True).items()}


# --- response shaping --------------------------------------------------------


def _html_response(start_response: Any, status: str, page: str) -> list[bytes]:
    body = page.encode("utf-8")
    start_response(
        status,
        [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


def _redirect(start_response: Any, status: str, location: str) -> list[bytes]:
    start_response(status, [("Location", location)])
    return [b""]


def _error_shell(status: str, body_html: str, session: sessions.Session | None) -> str:
    return page_shell(
        title=f"{status} — {_SITE_TITLE}",
        description="League of Agents play surface.",
        body_html=body_html,
        session=session,
    )


def _error_page(
    start_response: Any, status: str, message: str, session: sessions.Session | None
) -> list[bytes]:
    links = _SIGN_IN_LINKS if status.startswith("401") else None
    body = (
        render_error_body(status, message, links=links)
        if links is not None
        else render_error_body(status, message)
    )
    return _html_response(start_response, status, _error_shell(status, body, session))


__all__ = ["PLAY_MODES", "with_play"]
