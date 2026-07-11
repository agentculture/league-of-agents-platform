"""Structured JSON errors for the match API.

Every API failure returns ``{"code": "...", "message": "..."}`` as the
response body (see :mod:`league_site.api.wsgi`'s module docstring for the
full error-envelope contract). :class:`ApiError` is the one exception type
every handler in that module raises — the router catches it exactly once
and renders ``{"code": self.code, "message": str(self)}`` at
``self.status`` — so no handler builds its own JSON error body, and every
error on the surface has the same shape.

The module-level factories below (:func:`bad_request`, :func:`unauthorized`,
etc.) are the only place an HTTP status line is paired with a
:class:`ApiError` ``code``; handlers call these rather than constructing
:class:`ApiError` directly, so the (status, code) pairing for a given
failure kind is defined in exactly one place.
"""

from __future__ import annotations

from typing import Any, Mapping


class ApiError(Exception):
    """A structured API failure: an HTTP status line plus a ``{code, message}`` body.

    Raise (or return-then-``raise``, matching this module's factory
    functions) an :class:`ApiError` from anywhere in an
    :mod:`league_site.api.wsgi` handler. ``str(self)`` is the ``message``
    field of the rendered JSON body. ``extra`` fields (if any) are merged
    into the top-level JSON body alongside ``code``/``message`` — see
    :func:`capacity_exceeded` for the one factory that uses this today.
    """

    def __init__(
        self, status: str, code: str, message: str, *, extra: Mapping[str, Any] | None = None
    ) -> None:
        self.status = status
        self.code = code
        self.extra: dict[str, Any] = dict(extra) if extra else {}
        super().__init__(message)


def bad_request(message: str, *, code: str = "bad_request") -> ApiError:
    """``400 Bad Request`` — malformed input: bad JSON, an unknown mode, an illegal action."""
    return ApiError("400 Bad Request", code, message)


def unauthorized(message: str = "authentication required") -> ApiError:
    """``401 Unauthorized`` — no valid human session or agent bearer token on the request.

    Reserved for endpoints that require *some* identity to act at all (e.g.
    creating a match). An authenticated request that simply isn't a
    participant of the match it's trying to act on gets :func:`forbidden`
    instead, not this — see that function's docstring.
    """
    return ApiError("401 Unauthorized", "unauthorized", message)


def anonymous_token(message: str) -> ApiError:
    """``401 Unauthorized`` — a presented bearer token is an anonymous-era token.

    The request-side rendering of
    :class:`league_site.auth.tokens.AnonymousTokenError` (task t6's hard
    cutoff): a token minted before agent tokens were anchored to a human
    account no longer authenticates. Distinct ``code`` from
    :func:`unauthorized` on purpose — the agent's operator needs to know this
    isn't "you forgot to send a token" but "re-mint this one under an
    account"; ``message`` (passed through from the exception) names the
    onboarding path. Raised nowhere directly: :func:`league_site.api.wsgi.
    with_api` catches the exception and builds this at the dispatch boundary.
    """
    return ApiError("401 Unauthorized", "anonymous_token", message)


def forbidden(message: str = "not a participant of this match") -> ApiError:
    """``403 Forbidden`` — the request cannot act on this match.

    Used uniformly for every "non-participant" case on a participant-only
    endpoint (submit a turn, pause, resume): another human's session,
    another agent's token, and a fully anonymous request all render the
    exact same ``403`` here — the endpoint doesn't leak whether the
    rejection was "wrong identity" or "no identity at all".
    """
    return ApiError("403 Forbidden", "forbidden", message)


def not_found(message: str) -> ApiError:
    """``404 Not Found`` — no route matches, or no match has this id."""
    return ApiError("404 Not Found", "not_found", message)


def conflict(message: str, *, code: str = "conflict") -> ApiError:
    """``409 Conflict`` — the request is well-formed but the match's state disallows it."""
    return ApiError("409 Conflict", code, message)


def method_not_allowed(message: str = "method not allowed") -> ApiError:
    """``405 Method Not Allowed`` — the route exists but not for this HTTP method."""
    return ApiError("405 Method Not Allowed", "method_not_allowed", message)


def capacity_exceeded(message: str, *, reason: str, current: int, limit: int) -> ApiError:
    """``429 Too Many Requests`` — a configured hard cap on match creation was hit.

    Raised by :mod:`league_site.api.wsgi`'s create-match handler when
    :func:`league_site.capacity.guard.check_capacity` returns a
    :class:`~league_site.capacity.guard.Refusal`: new matches over a
    configured cap are refused outright, never degraded (see that module's
    docstring). ``reason``/``current``/``limit`` mirror
    :class:`~league_site.capacity.guard.Refusal`'s own fields exactly and
    are merged into the top-level JSON error body alongside the usual
    ``code``/``message``, so a caller sees which cap was hit and by how much
    without a second request.
    """
    return ApiError(
        "429 Too Many Requests",
        "capacity_exceeded",
        message,
        extra={"reason": reason, "current": current, "limit": limit},
    )
