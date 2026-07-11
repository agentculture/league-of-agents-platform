"""Tests for task t4 blocking enforcement inside ``league_site.auth.tokens.verify``.

Two denial shapes converge on one distinguishable failure — a
:class:`~league_site.auth.tokens.BlockedTokenError` (rendered as a clean 403
at the API boundary, see ``tests/test_api_blocking.py``):

* **Token-level** — the presented token's own record carries
  ``blocked=True`` (an operator kill-switch flip on that one credential).
* **Account-level** — the token is fine, but its ``owner_account_id``
  resolves, in an injected :class:`~league_site.accounts.store.AccountStore`,
  to an account with ``blocked=True`` (blocking a human blocks *every* token
  they minted, with a single account-store write).

Both are read live on the very next :func:`verify` call — no caching — and
the account-store consultation costs at most one extra ``get`` per verify,
and only for a token that already resolved.
"""

from __future__ import annotations

import pytest

from league_site.accounts.store import (
    AccountRecord,
    InMemoryAccountStore,
    account_id_for,
)
from league_site.auth.token_store import InMemoryTokenStore, TokenNotFoundError
from league_site.auth.tokens import (
    AnonymousTokenError,
    BlockedTokenError,
    issue,
    verify,
)


def _account(store: InMemoryAccountStore, account_id: str, *, blocked: bool = False) -> str:
    provider, _, subject = account_id.partition(":")
    store.upsert(
        AccountRecord(
            account_id=account_id,
            provider=provider,
            provider_user_id=subject,
            display_name="Owner",
            email=None,
        )
    )
    if blocked:
        store.set_blocked(account_id, True)
    return account_id


# --- token-level blocking ----------------------------------------------------


def test_verify_raises_blocked_token_error_for_a_blocked_token() -> None:
    store = InMemoryTokenStore()
    issued = issue(store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:1")
    store.set_blocked(issued.identity.token_id, True)

    with pytest.raises(BlockedTokenError):
        verify(store, issued.token)


def test_blocked_token_error_message_does_not_leak_beyond_credential_is_blocked() -> None:
    store = InMemoryTokenStore()
    issued = issue(store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:1")
    store.set_blocked(issued.identity.token_id, True)

    with pytest.raises(BlockedTokenError) as excinfo:
        verify(store, issued.token)
    message = str(excinfo.value)
    assert "blocked" in message
    # No agent name, token id, account id, or hash leaks in the refusal.
    assert issued.identity.token_id not in message
    assert "Sonnet" not in message
    assert issued.token not in message


def test_unblocking_a_token_restores_verification_on_the_next_call() -> None:
    store = InMemoryTokenStore()
    issued = issue(store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:1")
    store.set_blocked(issued.identity.token_id, True)
    with pytest.raises(BlockedTokenError):
        verify(store, issued.token)

    store.set_blocked(issued.identity.token_id, False)

    identity = verify(store, issued.token)
    assert identity is not None
    assert identity.agent_name == "Sonnet"


def test_a_revoked_blocked_token_is_still_the_uniform_none() -> None:
    """Revoked is the most primal outcome: a revoked token resolves to ``None``
    regardless of its block flag, never surfacing a distinguishable error."""
    store = InMemoryTokenStore()
    issued = issue(store, agent_name="Sonnet", model="m", provider="p", owner_account_id="github:1")
    store.set_blocked(issued.identity.token_id, True)
    store.revoke(issued.identity.token_id)

    assert verify(store, issued.token) is None


# --- account-level blocking --------------------------------------------------


def test_verify_raises_blocked_token_error_when_the_owning_account_is_blocked() -> None:
    tokens_store = InMemoryTokenStore()
    accounts = InMemoryAccountStore()
    owner = _account(accounts, account_id_for("github", "42"), blocked=True)
    issued = issue(
        tokens_store, agent_name="Sonnet", model="m", provider="p", owner_account_id=owner
    )

    with pytest.raises(BlockedTokenError):
        verify(tokens_store, issued.token, account_store=accounts)


def test_account_block_denies_every_token_that_account_minted() -> None:
    tokens_store = InMemoryTokenStore()
    accounts = InMemoryAccountStore()
    owner = _account(accounts, account_id_for("github", "42"), blocked=True)
    first = issue(tokens_store, agent_name="A", model="m", provider="p", owner_account_id=owner)
    second = issue(tokens_store, agent_name="B", model="m", provider="p", owner_account_id=owner)

    with pytest.raises(BlockedTokenError):
        verify(tokens_store, first.token, account_store=accounts)
    with pytest.raises(BlockedTokenError):
        verify(tokens_store, second.token, account_store=accounts)


def test_another_accounts_token_still_verifies_while_one_account_is_blocked() -> None:
    tokens_store = InMemoryTokenStore()
    accounts = InMemoryAccountStore()
    blocked_owner = _account(accounts, account_id_for("github", "42"), blocked=True)
    ok_owner = _account(accounts, account_id_for("github", "99"), blocked=False)
    blocked_tok = issue(
        tokens_store, agent_name="A", model="m", provider="p", owner_account_id=blocked_owner
    )
    ok_tok = issue(tokens_store, agent_name="B", model="m", provider="p", owner_account_id=ok_owner)

    with pytest.raises(BlockedTokenError):
        verify(tokens_store, blocked_tok.token, account_store=accounts)
    identity = verify(tokens_store, ok_tok.token, account_store=accounts)
    assert identity is not None
    assert identity.agent_name == "B"


def test_flipping_the_account_block_takes_effect_on_the_next_verify() -> None:
    tokens_store = InMemoryTokenStore()
    accounts = InMemoryAccountStore()
    owner = _account(accounts, account_id_for("github", "42"), blocked=False)
    issued = issue(
        tokens_store, agent_name="Sonnet", model="m", provider="p", owner_account_id=owner
    )
    assert verify(tokens_store, issued.token, account_store=accounts) is not None

    accounts.set_blocked(owner, True)  # a single account-store write

    with pytest.raises(BlockedTokenError):
        verify(tokens_store, issued.token, account_store=accounts)

    accounts.set_blocked(owner, False)
    assert verify(tokens_store, issued.token, account_store=accounts) is not None


def test_verify_without_an_account_store_skips_account_level_enforcement() -> None:
    """Account-level blocking is enforced only when an account store is passed;
    the token-level path is unchanged for callers that pass none."""
    tokens_store = InMemoryTokenStore()
    accounts = InMemoryAccountStore()
    owner = _account(accounts, account_id_for("github", "42"), blocked=True)
    issued = issue(
        tokens_store, agent_name="Sonnet", model="m", provider="p", owner_account_id=owner
    )

    # No account_store -> the blocked account is never consulted.
    identity = verify(tokens_store, issued.token)
    assert identity is not None


def test_verify_tolerates_a_missing_account_record() -> None:
    """A token whose owner has no account record (never blocked) still
    verifies — the account lookup fails open, not closed."""
    tokens_store = InMemoryTokenStore()
    accounts = InMemoryAccountStore()  # empty: no record for the owner
    issued = issue(
        tokens_store,
        agent_name="Sonnet",
        model="m",
        provider="p",
        owner_account_id="github:missing",
    )

    identity = verify(tokens_store, issued.token, account_store=accounts)
    assert identity is not None


def test_anonymous_token_still_cuts_off_even_with_an_account_store() -> None:
    tokens_store = InMemoryTokenStore()
    accounts = InMemoryAccountStore()
    issued = issue(tokens_store, agent_name="Legacy", model="m", provider="p")  # owner None

    with pytest.raises(AnonymousTokenError):
        verify(tokens_store, issued.token, account_store=accounts)


# --- store-level set_blocked (InMemory) --------------------------------------


def test_in_memory_set_blocked_flips_only_the_targeted_token() -> None:
    store = InMemoryTokenStore()
    first = issue(store, agent_name="A", model="m", provider="p", owner_account_id="github:1")
    second = issue(store, agent_name="B", model="m", provider="p", owner_account_id="github:1")

    store.set_blocked(first.identity.token_id, True)

    assert store.get_by_hash(store.list_all()[0].token_hash) is not None
    blocked_by_id = {r.token_id: r.blocked for r in store.list_all()}
    assert blocked_by_id[first.identity.token_id] is True
    assert blocked_by_id[second.identity.token_id] is False


def test_in_memory_set_blocked_unknown_token_id_raises_token_not_found() -> None:
    store = InMemoryTokenStore()
    with pytest.raises(TokenNotFoundError):
        store.set_blocked("does-not-exist", True)


def test_in_memory_set_blocked_preserves_every_other_field() -> None:
    store = InMemoryTokenStore()
    issued = issue(store, agent_name="A", model="m", provider="p", owner_account_id="github:1")

    store.set_blocked(issued.identity.token_id, True)

    (record,) = store.list_all()
    assert record.blocked is True
    assert record.revoked is False
    assert record.owner_account_id == "github:1"
    assert record.agent_name == "A"
