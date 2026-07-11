"""Per-request identity resolution: human session or agent bearer token.

A request is identified exactly one of two ways, checked in this order:

1. A verified human session, read from ``environ[SESSION_ENVIRON_KEY]`` —
   populated by :func:`league_site.auth.wsgi.with_auth`, which MUST wrap
   :func:`~league_site.api.wsgi.with_api` (outside it) for this to be
   populated at all; see that module's docstring for the composition
   order.
2. A verified agent bearer token (``Authorization: Bearer loa_...``),
   resolved against an injected
   :class:`~league_site.auth.token_store.TokenStore` via
   :func:`league_site.auth.tokens.verify`.

Neither present -> anonymous (:func:`resolve_identity` returns ``None``),
which is allowed on every read-only endpoint and rejected on every
endpoint that requires an identity — see :mod:`league_site.api.wsgi`.

:attr:`RequestIdentity.key` is a deterministic string derived from the
*durable* identity — OAuth ``provider``+``subject`` for humans,
``agent_name``+``model``+``provider`` for agents — never from a
per-session or per-token transient id (a session's ``issued_at``, a
token's ``token_id``). Match participants are stored with
``participant_id`` set to this key (see :func:`participant_for_identity`),
so "does this request own this match participant" is a plain equality
check on data already persisted in the ``Match`` record: no separate
ownership table to keep in sync, and no ownership information lost across
a :class:`~league_site.matches.store.MatchStore` restart.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from league_site.auth import tokens
from league_site.auth.token_store import TokenStore
from league_site.auth.wsgi import SESSION_ENVIRON_KEY
from league_site.matches.models import AgentIdentity, Participant, ParticipantKind


@dataclass(frozen=True)
class RequestIdentity:
    """The resolved identity behind one API request. See the module docstring."""

    kind: ParticipantKind
    key: str
    display_name: str
    model: str | None = None
    provider: str | None = None


def _human_key(provider: str, subject: str) -> str:
    return f"human:{provider}:{subject}"


def _agent_key(agent_name: str, model: str, provider: str) -> str:
    return f"agent:{agent_name}:{model}:{provider}"


def resolve_identity(environ: dict[str, Any], token_store: TokenStore) -> RequestIdentity | None:
    """Resolve the identity behind *environ*, or ``None`` for an anonymous request.

    Checks the human session first (cheap: already resolved by
    ``with_auth``), then the ``Authorization`` bearer token against
    *token_store*.

    Propagates :class:`league_site.auth.tokens.AnonymousTokenError` unchanged
    if the presented bearer token resolves to a pre-account (anonymous-era)
    record — task t6's hard cutoff. It is *not* flattened to ``None`` here so
    the failure stays distinguishable; :func:`league_site.api.wsgi.with_api`
    catches it at the dispatch boundary and renders a ``401 anonymous_token``
    naming the onboarding path.
    """
    session = environ.get(SESSION_ENVIRON_KEY)
    if session is not None:
        return RequestIdentity(
            kind=ParticipantKind.HUMAN,
            key=_human_key(session.provider, session.subject),
            display_name=session.display,
        )
    token = tokens.parse_bearer_token(environ.get("HTTP_AUTHORIZATION"))
    identity = tokens.verify(token_store, token)
    if identity is not None:
        return RequestIdentity(
            kind=ParticipantKind.AGENT,
            key=_agent_key(identity.agent_name, identity.model, identity.provider),
            display_name=identity.agent_name,
            model=identity.model,
            provider=identity.provider,
        )
    return None


def participant_for_identity(identity: RequestIdentity) -> Participant:
    """Build the :class:`Participant` a request's identity plays a match as.

    ``participant_id`` is set to :attr:`RequestIdentity.key` (not the
    usual random default) precisely so a later request's re-resolved
    identity can be compared against it directly — see the module
    docstring.
    """
    if identity.kind is ParticipantKind.AGENT:
        if identity.model is None or identity.provider is None:
            raise ValueError("agent identities require both model and provider")
        return Participant(
            display_name=identity.display_name,
            kind=ParticipantKind.AGENT,
            agent_identity=AgentIdentity(model=identity.model, provider=identity.provider),
            participant_id=identity.key,
        )
    return Participant(
        display_name=identity.display_name,
        kind=ParticipantKind.HUMAN,
        participant_id=identity.key,
    )


def participant_for_opponent_spec(spec: Any) -> Participant:
    """Build the second participant from a create-match request's ``opponent`` object.

    Raises :class:`ValueError` (translated to a ``400 bad_request`` by
    :mod:`league_site.api.wsgi`) if *spec* is malformed. Accepted shape::

        # kind == "human"
        {"kind": "human", "display_name": "...", "provider": "...", "subject": "..."}

        # kind == "agent"
        {"kind": "agent", "display_name": "...", "agent_name": "...",
         "model": "...", "provider": "..."}

    This mirrors :class:`RequestIdentity` closely on purpose: the
    ``participant_id`` derived here is exactly what a real second
    human/agent resolves to once *they* authenticate for real (see
    :func:`resolve_identity` / :func:`participant_for_identity`), so an
    opponent named this way can actually take their own turns later.
    """
    if not isinstance(spec, dict):
        raise ValueError("opponent must be a JSON object")
    display_name = spec.get("display_name")
    if not isinstance(display_name, str) or not display_name:
        raise ValueError("opponent.display_name is required")
    kind = spec.get("kind")
    if kind == "human":
        return _human_opponent(spec, display_name)
    if kind == "agent":
        return _agent_opponent(spec, display_name)
    raise ValueError("opponent.kind must be 'human' or 'agent'")


def _human_opponent(spec: dict[str, Any], display_name: str) -> Participant:
    provider = spec.get("provider")
    subject = spec.get("subject")
    if not isinstance(provider, str) or not provider:
        raise ValueError("opponent.provider is required for a human opponent")
    if not isinstance(subject, str) or not subject:
        raise ValueError("opponent.subject is required for a human opponent")
    return Participant(
        display_name=display_name,
        kind=ParticipantKind.HUMAN,
        participant_id=_human_key(provider, subject),
    )


def _agent_opponent(spec: dict[str, Any], display_name: str) -> Participant:
    agent_name = spec.get("agent_name")
    model = spec.get("model")
    provider = spec.get("provider")
    if not isinstance(agent_name, str) or not agent_name:
        raise ValueError("opponent.agent_name is required for an agent opponent")
    if not isinstance(model, str) or not model:
        raise ValueError("opponent.model is required for an agent opponent")
    if not isinstance(provider, str) or not provider:
        raise ValueError("opponent.provider is required for an agent opponent")
    return Participant(
        display_name=display_name,
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model=model, provider=provider),
        participant_id=_agent_key(agent_name, model, provider),
    )
