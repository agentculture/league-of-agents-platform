"""Tests for :mod:`league_site.api.identity` — per-request identity resolution."""

from __future__ import annotations

import pytest

from league_site.api.identity import (
    RequestIdentity,
    participant_for_identity,
    participant_for_opponent_spec,
    resolve_identity,
)
from league_site.auth import sessions, tokens
from league_site.auth.token_store import InMemoryTokenStore
from league_site.auth.wsgi import SESSION_ENVIRON_KEY
from league_site.matches import ParticipantKind


def _session(
    subject: str = "42", provider: str = "github", display: str = "Ada"
) -> sessions.Session:
    return sessions.Session(
        subject=subject, provider=provider, display=display, issued_at=0, expiry=2**31
    )


# --- resolve_identity ----------------------------------------------------


def test_resolve_identity_from_human_session() -> None:
    store = InMemoryTokenStore()
    identity = resolve_identity({SESSION_ENVIRON_KEY: _session()}, store)
    assert identity == RequestIdentity(
        kind=ParticipantKind.HUMAN, key="human:github:42", display_name="Ada"
    )


def test_resolve_identity_from_agent_bearer_token() -> None:
    store = InMemoryTokenStore()
    issued = tokens.issue(
        store,
        agent_name="Sonnet",
        model="claude-sonnet-5",
        provider="anthropic",
        owner_account_id="github:agent-owner",
    )
    environ = {"HTTP_AUTHORIZATION": f"Bearer {issued.token}"}
    identity = resolve_identity(environ, store)
    assert identity == RequestIdentity(
        kind=ParticipantKind.AGENT,
        key="agent:Sonnet:claude-sonnet-5:anthropic",
        display_name="Sonnet",
        model="claude-sonnet-5",
        provider="anthropic",
    )


def test_resolve_identity_human_session_takes_priority_over_a_bearer_token() -> None:
    store = InMemoryTokenStore()
    issued = tokens.issue(
        store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:owner"
    )
    environ = {
        SESSION_ENVIRON_KEY: _session(),
        "HTTP_AUTHORIZATION": f"Bearer {issued.token}",
    }
    identity = resolve_identity(environ, store)
    assert identity is not None
    assert identity.kind is ParticipantKind.HUMAN


def test_resolve_identity_anonymous_request_returns_none() -> None:
    assert resolve_identity({}, InMemoryTokenStore()) is None


def test_resolve_identity_unknown_bearer_token_returns_none() -> None:
    environ = {"HTTP_AUTHORIZATION": "Bearer loa_not-a-real-token"}
    assert resolve_identity(environ, InMemoryTokenStore()) is None


def test_resolve_identity_revoked_bearer_token_returns_none() -> None:
    store = InMemoryTokenStore()
    issued = tokens.issue(
        store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:owner"
    )
    tokens.revoke(store, issued.identity.token_id)
    environ = {"HTTP_AUTHORIZATION": f"Bearer {issued.token}"}
    assert resolve_identity(environ, store) is None


def test_resolve_identity_anonymous_era_token_propagates_the_cutoff() -> None:
    """A pre-account (owner-less) token is task t6's hard cutoff: bearer
    resolution surfaces it as a distinguishable :class:`AnonymousTokenError`
    (which ``with_api`` renders as a 401 naming onboarding) rather than
    flattening it to the anonymous ``None`` of an absent/invalid token."""
    store = InMemoryTokenStore()
    issued = tokens.issue(
        store, agent_name="Legacy", model="m", provider="p"
    )  # owner defaults None
    environ = {"HTTP_AUTHORIZATION": f"Bearer {issued.token}"}
    with pytest.raises(tokens.AnonymousTokenError):
        resolve_identity(environ, store)


# --- participant_for_identity ----------------------------------------------


def test_participant_for_identity_human() -> None:
    identity = RequestIdentity(
        kind=ParticipantKind.HUMAN, key="human:github:42", display_name="Ada"
    )
    participant = participant_for_identity(identity)
    assert participant.participant_id == "human:github:42"
    assert participant.kind is ParticipantKind.HUMAN
    assert participant.agent_identity is None


def test_participant_for_identity_agent() -> None:
    identity = RequestIdentity(
        kind=ParticipantKind.AGENT,
        key="agent:Sonnet:claude-sonnet-5:anthropic",
        display_name="Sonnet",
        model="claude-sonnet-5",
        provider="anthropic",
    )
    participant = participant_for_identity(identity)
    assert participant.participant_id == "agent:Sonnet:claude-sonnet-5:anthropic"
    assert participant.kind is ParticipantKind.AGENT
    assert participant.agent_identity is not None
    assert participant.agent_identity.model == "claude-sonnet-5"
    assert participant.agent_identity.provider == "anthropic"


def test_participant_for_identity_rejects_an_agent_identity_missing_model_or_provider() -> None:
    """Defensive: :func:`resolve_identity` never produces this shape (an
    agent identity always carries both), but the guard exists in case a
    caller builds a :class:`RequestIdentity` by hand."""
    identity = RequestIdentity(
        kind=ParticipantKind.AGENT, key="agent:x", display_name="Broken", model=None, provider=None
    )
    with pytest.raises(ValueError, match="model and provider"):
        participant_for_identity(identity)


def test_participant_for_identity_same_credentials_always_yield_the_same_participant_id() -> None:
    """The point of the deterministic key: resolving the same credentials
    twice (e.g. across two separate requests) always yields the same
    participant_id, with no separate ownership table needed to check
    "does this request own this match participant" after a store restart."""
    store = InMemoryTokenStore()
    issued = tokens.issue(
        store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:owner"
    )
    environ = {"HTTP_AUTHORIZATION": f"Bearer {issued.token}"}

    first_identity = resolve_identity(environ, store)
    second_identity = resolve_identity(environ, store)
    assert first_identity is not None and second_identity is not None
    first = participant_for_identity(first_identity)
    second = participant_for_identity(second_identity)
    assert first.participant_id == second.participant_id


# --- participant_for_opponent_spec ------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        "not-a-dict",
        None,
        {},
        {"display_name": ""},
        {"display_name": "Bob", "kind": "human"},
        {"display_name": "Bob", "kind": "human", "provider": "github"},
        {"display_name": "Bob", "kind": "human", "subject": "99"},
        {"display_name": "Bob", "kind": "agent"},
        {"display_name": "Bob", "kind": "agent", "model": "m"},
        {"display_name": "Bob", "kind": "agent", "agent_name": "Bob"},
        {"display_name": "Bob", "kind": "agent", "agent_name": "Bob", "model": "m"},
        {"display_name": "Bob", "kind": "martian"},
        {"display_name": "Bob"},
    ],
)
def test_participant_for_opponent_spec_rejects_malformed_specs(spec: object) -> None:
    with pytest.raises(ValueError):
        participant_for_opponent_spec(spec)


def test_participant_for_opponent_spec_human() -> None:
    participant = participant_for_opponent_spec(
        {"kind": "human", "display_name": "Bob", "provider": "github", "subject": "99"}
    )
    assert participant.participant_id == "human:github:99"
    assert participant.kind is ParticipantKind.HUMAN
    assert participant.display_name == "Bob"


def test_participant_for_opponent_spec_agent() -> None:
    participant = participant_for_opponent_spec(
        {
            "kind": "agent",
            "display_name": "Rival",
            "agent_name": "Rival",
            "model": "gpt-5",
            "provider": "openai",
        }
    )
    assert participant.participant_id == "agent:Rival:gpt-5:openai"
    assert participant.kind is ParticipantKind.AGENT
    assert participant.agent_identity is not None
    assert participant.agent_identity.provider == "openai"


def test_participant_for_opponent_spec_matches_a_real_identity_resolution() -> None:
    """An opponent named at creation time by (agent_name, model, provider)
    resolves to the exact same participant_id a real agent token with that
    identity would resolve to later — this is what lets a *different*,
    independently-authenticated agent actually play as that opponent."""
    store = InMemoryTokenStore()
    issued = tokens.issue(
        store, agent_name="Rival", model="gpt-5", provider="openai", owner_account_id="github:rival"
    )
    environ = {"HTTP_AUTHORIZATION": f"Bearer {issued.token}"}
    real_identity = resolve_identity(environ, store)
    assert real_identity is not None
    real_participant = participant_for_identity(real_identity)

    named_participant = participant_for_opponent_spec(
        {
            "kind": "agent",
            "display_name": "Rival",
            "agent_name": "Rival",
            "model": "gpt-5",
            "provider": "openai",
        }
    )
    assert named_participant.participant_id == real_participant.participant_id
