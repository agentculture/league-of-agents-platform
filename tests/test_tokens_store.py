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
) -> TokenRecord:
    return TokenRecord(
        token_id=token_id,
        token_hash=token_hash,
        agent_name="probe-bot",
        model="claude-sonnet-5",
        provider="anthropic",
        created_at=datetime.now(timezone.utc),
        revoked=revoked,
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
