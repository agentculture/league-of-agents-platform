"""Tests for TokenStore / InMemoryTokenStore.

Exercises the store interface directly, with hand-built ``TokenRecord``
values (as opposed to the ``tokens.issue``/``verify``/``revoke`` flow, which
is covered end-to-end in test_tokens_core.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from league_site.auth.token_store import (
    InMemoryTokenStore,
    TokenNotFoundError,
    TokenRecord,
    TokenStore,
)


def _record(
    token_id: str = "tok-1",
    token_hash: str = "a" * 64,
    *,
    revoked: bool = False,
    owner_account_id: str | None = None,
    blocked: bool = False,
) -> TokenRecord:
    return TokenRecord(
        token_id=token_id,
        token_hash=token_hash,
        agent_name="probe-bot",
        model="claude-sonnet-5",
        provider="anthropic",
        created_at=datetime.now(timezone.utc),
        revoked=revoked,
        owner_account_id=owner_account_id,
        blocked=blocked,
    )


def test_tokenstore_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        TokenStore()  # type: ignore[abstract]


def test_save_then_get_by_hash_round_trips() -> None:
    store = InMemoryTokenStore()
    record = _record()

    store.save(record)

    assert store.get_by_hash(record.token_hash) == record


def test_get_by_hash_missing_returns_none() -> None:
    store = InMemoryTokenStore()
    assert store.get_by_hash("does-not-exist") is None


def test_save_overwrites_existing_record_with_same_hash() -> None:
    store = InMemoryTokenStore()
    original = _record()
    store.save(original)

    updated = _record(token_id=original.token_id, token_hash=original.token_hash, revoked=True)
    store.save(updated)

    assert store.get_by_hash(original.token_hash) == updated


def test_revoke_marks_the_matching_record_revoked() -> None:
    store = InMemoryTokenStore()
    record = _record()
    store.save(record)

    store.revoke(record.token_id)

    stored = store.get_by_hash(record.token_hash)
    assert stored is not None
    assert stored.revoked is True


def test_revoke_does_not_disturb_other_records() -> None:
    store = InMemoryTokenStore()
    first = _record(token_id="tok-1", token_hash="a" * 64)
    second = _record(token_id="tok-2", token_hash="b" * 64)
    store.save(first)
    store.save(second)

    store.revoke("tok-1")

    assert store.get_by_hash("a" * 64).revoked is True
    assert store.get_by_hash("b" * 64).revoked is False


def test_revoke_missing_token_id_raises_token_not_found_error() -> None:
    store = InMemoryTokenStore()
    with pytest.raises(TokenNotFoundError):
        store.revoke("does-not-exist")


def test_token_not_found_error_carries_the_offending_id() -> None:
    error = TokenNotFoundError("tok-missing")
    assert error.token_id == "tok-missing"
    assert "tok-missing" in str(error)


# --- owner_account_id + blocked ------------------------------------------


def test_token_record_defaults_owner_account_id_to_none_and_blocked_to_false() -> None:
    """A record built the legacy way — without the account-ownership fields —

    is the anonymous/legacy shape: no owning account, not blocked.
    """
    record = TokenRecord(
        token_id="tok-legacy",
        token_hash="a" * 64,
        agent_name="probe-bot",
        model="claude-sonnet-5",
        provider="anthropic",
        created_at=datetime.now(timezone.utc),
    )

    assert record.owner_account_id is None
    assert record.blocked is False


def test_save_then_get_by_hash_round_trips_owner_account_id_and_blocked() -> None:
    store = InMemoryTokenStore()
    record = _record(owner_account_id="github:4242", blocked=True)

    store.save(record)
    loaded = store.get_by_hash(record.token_hash)

    assert loaded is not None
    assert loaded.owner_account_id == "github:4242"
    assert loaded.blocked is True


def test_revoke_leaves_owner_account_id_and_blocked_untouched() -> None:
    """Revoking flips only ``revoked`` — the ownership/block fields ride through."""
    store = InMemoryTokenStore()
    record = _record(owner_account_id="github:4242", blocked=True)
    store.save(record)

    store.revoke(record.token_id)

    stored = store.get_by_hash(record.token_hash)
    assert stored is not None
    assert stored.revoked is True
    assert stored.owner_account_id == "github:4242"
    assert stored.blocked is True


# --- set_blocked (t4 operator kill-switch) -----------------------------------


def test_set_blocked_flips_only_the_targeted_record() -> None:
    store = InMemoryTokenStore()
    first = _record(token_id="tok-1", token_hash="a" * 64)
    second = _record(token_id="tok-2", token_hash="b" * 64)
    store.save(first)
    store.save(second)

    store.set_blocked("tok-1", True)

    assert store.get_by_hash("a" * 64).blocked is True
    assert store.get_by_hash("b" * 64).blocked is False


def test_set_blocked_can_unblock() -> None:
    store = InMemoryTokenStore()
    record = _record(blocked=True)
    store.save(record)

    store.set_blocked(record.token_id, False)

    assert store.get_by_hash(record.token_hash).blocked is False


def test_set_blocked_leaves_revoked_and_owner_untouched() -> None:
    store = InMemoryTokenStore()
    record = _record(owner_account_id="github:4242", revoked=False)
    store.save(record)

    store.set_blocked(record.token_id, True)

    stored = store.get_by_hash(record.token_hash)
    assert stored.blocked is True
    assert stored.revoked is False
    assert stored.owner_account_id == "github:4242"


def test_set_blocked_missing_token_id_raises_token_not_found_error() -> None:
    store = InMemoryTokenStore()
    with pytest.raises(TokenNotFoundError):
        store.set_blocked("does-not-exist", True)


def test_token_store_set_blocked_default_raises_not_implemented() -> None:
    """The base-class default documents the contract concrete stores grow
    (see aws_tokens) — same pattern as ``TokenStore.list_all``."""

    class _Bare(TokenStore):
        def save(self, record: TokenRecord) -> None:  # pragma: no cover - unused
            raise AssertionError

        def get_by_hash(self, token_hash: str) -> TokenRecord | None:  # pragma: no cover
            raise AssertionError

        def revoke(self, token_id: str) -> None:  # pragma: no cover - unused
            raise AssertionError

    with pytest.raises(NotImplementedError):
        _Bare().set_blocked("tok-1", True)
