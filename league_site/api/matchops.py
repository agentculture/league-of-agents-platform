"""Create-match and take-turn flows shared by the JSON API and the play surface.

Factored out of :mod:`league_site.api.wsgi`'s route handlers (task t9) so the
browser play surface (:mod:`league_site.play`) drives the *exact same* path a
``POST /api/v1/matches`` / ``POST /api/v1/matches/<id>/turns`` request does —
capacity gate, mode validation, participant construction, engine error
translation, auto-completion, and rating — rather than a parallel
reimplementation that could drift. The JSON API's route behavior is
unchanged: its handlers now call these functions and keep doing everything
request-shaped themselves (identity resolution, body reading, response
shaping).

Every failure raises :class:`~league_site.api.errors.ApiError` with the same
(status, code, message) the API always used; the JSON surface renders it as
the usual ``{"code", "message"}`` envelope, the play surface as an HTML
error page.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from league_site.api import errors
from league_site.api.identity import (
    RequestIdentity,
    participant_for_identity,
    participant_for_opponent_spec,
)
from league_site.capacity.config import CapacityConfig
from league_site.capacity.guard import Refusal, check_capacity
from league_site.matches import (
    GameEngine,
    InvalidTransitionError,
    Match,
    MatchStore,
)
from league_site.ratings.ledger import RatingLedgerStore
from league_site.ratings.system import RatingSystem, outcome_from_match

#: ``mode -> GameEngine factory`` — the registry shape both ``with_api`` and
#: ``with_play`` take (see :func:`league_site.api.registry.default_engine_registry`).
EngineRegistry = Mapping[str, Callable[[], GameEngine]]


def engine_for(registry: EngineRegistry, mode: str) -> GameEngine:
    """The engine *mode* plays on, or a ``400 unknown_mode`` if unregistered."""
    factory = registry.get(mode)
    if factory is None:
        raise errors.bad_request(f"unknown game mode {mode!r}", code="unknown_mode")
    return factory()


def check_create_capacity(matches: MatchStore, capacity: CapacityConfig) -> None:
    """Raise ``429 capacity_exceeded`` if a new match may not be created right now.

    Kept separate from :func:`create_match` so both surfaces preserve the
    documented ordering — the capacity gate runs before anything about the
    request *body* is even read (see :mod:`league_site.api.wsgi`'s docstring).
    """
    decision = check_capacity(matches, capacity)
    if isinstance(decision, Refusal):
        raise errors.capacity_exceeded(
            f"cannot create match: {decision.reason} limit reached "
            f"({decision.current}/{decision.limit})",
            reason=decision.reason,
            current=decision.current,
            limit=decision.limit,
        )


def create_match(
    identity: RequestIdentity,
    *,
    matches: MatchStore,
    registry: EngineRegistry,
    mode: Any,
    opponent_spec: Any = None,
) -> Match:
    """Create, start, and save a match for *identity* playing *mode*.

    *identity* becomes the first participant; *opponent_spec* (the create
    request's optional ``opponent`` object — see
    :func:`~league_site.api.identity.participant_for_opponent_spec`) adds a
    second. Validation order matches the pre-factoring handler exactly:
    mode string, engine lookup, then opponent. Callers gate capacity first
    via :func:`check_create_capacity`.
    """
    if not isinstance(mode, str) or not mode:
        raise errors.bad_request("mode must be a non-empty string")
    engine = engine_for(registry, mode)

    creator = participant_for_identity(identity)
    participants = [creator]
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
    return match


def take_turn(
    match: Match,
    participant_id: str,
    action: Any,
    *,
    matches: MatchStore,
    registry: EngineRegistry,
    ledger: RatingLedgerStore,
    ratings: RatingSystem,
) -> Match:
    """Apply one already-authorized turn to *match*, then save it.

    The caller has loaded *match* and established that *participant_id*
    belongs to one of its participants (each surface renders its own
    401/403 for that). Engine failures translate exactly as the API always
    did: :class:`~league_site.matches.errors.InvalidTransitionError` →
    ``409 invalid_transition``, ``ValueError`` (well-shaped but illegal) →
    ``400 illegal_action``, ``TypeError`` (not shaped like an order at all)
    → ``400 malformed_action`` — the engine's own descriptive message kept
    verbatim. Auto-completes the match (and records a rating update, for a
    two-or-more-participant match) the instant the engine reports
    ``is_over``.
    """
    engine = engine_for(registry, match.game_id)
    try:
        match.take_turn(engine, participant_id, action)
    except InvalidTransitionError as exc:
        raise errors.conflict(str(exc), code="invalid_transition") from exc
    except ValueError as exc:
        raise errors.bad_request(str(exc), code="illegal_action") from exc
    except TypeError as exc:
        raise errors.bad_request(str(exc), code="malformed_action") from exc

    if engine.is_over(match.game_state):
        match.complete(engine)
        record_rating(match, ledger, ratings)

    matches.save(match)
    return match


def record_rating(match: Match, ledger: RatingLedgerStore, ratings: RatingSystem) -> None:
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


__all__ = [
    "EngineRegistry",
    "engine_for",
    "check_create_capacity",
    "create_match",
    "take_turn",
    "record_rating",
]
