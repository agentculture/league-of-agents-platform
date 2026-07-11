"""Tests for league_site.auth.tokens: issue / verify / revoke / parse_bearer_token.

Covers the task's acceptance criteria:

* An issued token verifies and yields the agent identity (name, model,
  provider); a revoked token verifies as None.
* A parsed Authorization header helper accepts a valid Bearer token and
  rejects malformed/absent ones, returning None and never raising.
* The store never contains plaintext tokens, and verification is
  constant-time on the hash comparison (hmac.compare_digest).
"""

from __future__ import annotations

import hmac
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from league_site.auth.token_store import InMemoryTokenStore, TokenNotFoundError, TokenRecord
from league_site.auth.tokens import (
    ONBOARDING_URL,
    TOKEN_PREFIX,
    AgentTokenIdentity,
    AnonymousTokenError,
    IssuedToken,
    issue,
    parse_bearer_token,
    revoke,
    verify,
)


def _issue(store: InMemoryTokenStore, **overrides: str) -> IssuedToken:
    fields = {
        "agent_name": "probe-bot",
        "model": "claude-sonnet-5",
        "provider": "anthropic",
        "owner_account_id": "github:owner",
    }
    fields.update(overrides)
    return issue(store, **fields)


# --- issue -------------------------------------------------------------------


def test_issue_returns_a_token_with_the_loa_prefix() -> None:
    store = InMemoryTokenStore()
    issued = _issue(store)
    assert issued.token.startswith(TOKEN_PREFIX)
    assert len(issued.token) > len(TOKEN_PREFIX)


def test_issue_returns_identity_matching_the_requested_agent_name_model_provider() -> None:
    store = InMemoryTokenStore()
    issued = _issue(store, agent_name="probe-bot", model="gpt-5", provider="openai")

    assert issued.identity.agent_name == "probe-bot"
    assert issued.identity.model == "gpt-5"
    assert issued.identity.provider == "openai"
    assert issued.identity.revoked is False


def test_two_issued_tokens_have_distinct_plaintext_and_distinct_token_ids() -> None:
    store = InMemoryTokenStore()
    first = _issue(store)
    second = _issue(store)

    assert first.token != second.token
    assert first.identity.token_id != second.identity.token_id


# --- verify --------------------------------------------------------------


def test_issued_token_verifies_to_the_same_identity() -> None:
    store = InMemoryTokenStore()
    issued = _issue(store, agent_name="probe-bot", model="claude-sonnet-5", provider="anthropic")

    identity = verify(store, issued.token)

    assert identity == AgentTokenIdentity(
        token_id=issued.identity.token_id,
        agent_name="probe-bot",
        model="claude-sonnet-5",
        provider="anthropic",
        created_at=issued.identity.created_at,
        revoked=False,
        owner_account_id="github:owner",
    )


def test_unknown_token_verifies_as_none() -> None:
    store = InMemoryTokenStore()
    assert verify(store, f"{TOKEN_PREFIX}not-a-real-token") is None


@pytest.mark.parametrize("bogus", [None, ""])
def test_empty_or_none_token_verifies_as_none(bogus: str | None) -> None:
    store = InMemoryTokenStore()
    assert verify(store, bogus) is None


def test_revoked_token_verifies_as_none() -> None:
    store = InMemoryTokenStore()
    issued = _issue(store)
    assert verify(store, issued.token) is not None

    revoke(store, issued.identity.token_id)

    assert verify(store, issued.token) is None


def test_verify_exposes_the_owner_account_id_on_the_identity() -> None:
    """Downstream (t4 block enforcement, audit) reads the owner off the
    resolved identity — verify surfaces it from the record."""
    store = InMemoryTokenStore()
    issued = _issue(store, owner_account_id="github:4242")

    identity = verify(store, issued.token)

    assert identity is not None
    assert identity.owner_account_id == "github:4242"


def test_verify_hard_cuts_off_an_anonymous_token() -> None:
    """Task t6: a record with ``owner_account_id is None`` (a token minted
    before agent tokens were anchored to a human account) no longer
    authenticates — verify raises the distinguishable
    :class:`AnonymousTokenError`, and its message names the onboarding path."""
    store = InMemoryTokenStore()
    issued = issue(store, agent_name="legacy-bot", model="m", provider="p")  # owner defaults None

    with pytest.raises(AnonymousTokenError) as excinfo:
        verify(store, issued.token)

    assert ONBOARDING_URL in str(excinfo.value)


def test_verify_owned_token_is_unaffected_by_the_anonymous_cutoff() -> None:
    """The cutoff refuses only owner-less records; an account-owned token
    verifies exactly as before."""
    store = InMemoryTokenStore()
    issued = issue(
        store, agent_name="owned-bot", model="m", provider="p", owner_account_id="github:7"
    )

    identity = verify(store, issued.token)

    assert identity is not None
    assert identity.agent_name == "owned-bot"


def test_verify_revoked_anonymous_token_is_still_the_uniform_none() -> None:
    """Revoked wins over the anonymous cutoff: a revoked owner-less token is
    the silent ``None`` (it is gone regardless of who owned it), not a raise."""
    store = InMemoryTokenStore()
    issued = issue(store, agent_name="legacy-bot", model="m", provider="p")  # owner defaults None
    revoke(store, issued.identity.token_id)

    assert verify(store, issued.token) is None


def test_verify_uses_hmac_compare_digest_for_the_hash_comparison() -> None:
    store = InMemoryTokenStore()
    issued = _issue(store)

    with patch("league_site.auth.tokens.hmac.compare_digest", wraps=hmac.compare_digest) as spy:
        identity = verify(store, issued.token)

    assert identity is not None
    spy.assert_called_once()
    called_args = spy.call_args.args
    assert called_args[0] == called_args[1]  # same hash on both sides for a valid token


# --- revoke --------------------------------------------------------------


class _MismatchedHashStore:
    """A store whose get_by_hash returns a record whose token_hash doesn't
    actually match the queried hash - simulates a non-exact-match backend
    (e.g. a fuzzy or GSI-based lookup) so verify()'s explicit
    hmac.compare_digest guard, not just the store's own lookup, is what
    rejects the mismatch."""

    def __init__(self, record: TokenRecord) -> None:
        self._record = record

    def save(self, record: TokenRecord) -> None:
        raise NotImplementedError

    def get_by_hash(self, token_hash: str) -> TokenRecord | None:
        return self._record

    def revoke(self, token_id: str) -> None:
        raise NotImplementedError


def test_verify_rejects_a_record_whose_hash_does_not_match_the_candidate() -> None:
    mismatched_record = TokenRecord(
        token_id="tok-mismatch",
        token_hash="0" * 64,
        agent_name="probe-bot",
        model="claude-sonnet-5",
        provider="anthropic",
        created_at=datetime.now(timezone.utc),
    )
    store = _MismatchedHashStore(mismatched_record)

    assert verify(store, f"{TOKEN_PREFIX}some-token") is None


def test_revoke_unknown_token_id_raises_token_not_found_error() -> None:
    store = InMemoryTokenStore()
    with pytest.raises(TokenNotFoundError):
        revoke(store, "does-not-exist")


# --- store never holds plaintext -----------------------------------------


def test_store_never_contains_the_plaintext_token() -> None:
    store = InMemoryTokenStore()
    issued = _issue(store)

    for record in store._records.values():  # whitebox store inspection
        assert record.token_hash != issued.token
        assert issued.token not in repr(record)
        assert issued.token not in str(record)
        # The hash is a 64-char sha256 hex digest, structurally distinct from
        # the "loa_"-prefixed, urlsafe-base64 plaintext token.
        assert len(record.token_hash) == 64
        assert all(c in "0123456789abcdef" for c in record.token_hash)


# --- parse_bearer_token ----------------------------------------------------


def test_parse_bearer_token_accepts_a_valid_header() -> None:
    assert parse_bearer_token("Bearer loa_abc123") == "loa_abc123"


def test_parse_bearer_token_is_case_insensitive_on_the_scheme() -> None:
    assert parse_bearer_token("bearer loa_abc123") == "loa_abc123"
    assert parse_bearer_token("BEARER loa_abc123") == "loa_abc123"


def test_parse_bearer_token_tolerates_extra_whitespace_around_the_token() -> None:
    assert parse_bearer_token("Bearer   loa_abc123  ") == "loa_abc123"


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Bearer",
        "Bearer ",
        "Bearer    ",
        "Basic loa_abc123",
        "loa_abc123",
        "BearerXloa_abc123",
    ],
)
def test_parse_bearer_token_rejects_malformed_or_absent_headers(header: str | None) -> None:
    assert parse_bearer_token(header) is None


def test_parse_bearer_token_never_raises_on_a_non_string_value() -> None:
    assert parse_bearer_token(12345) is None  # type: ignore[arg-type]
    assert parse_bearer_token(["Bearer", "loa_abc123"]) is None  # type: ignore[arg-type]
