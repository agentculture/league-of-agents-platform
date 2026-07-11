"""Tests for AccountStore / InMemoryAccountStore.

Exercises the store interface directly, with hand-built ``AccountRecord``
values — mirrors tests/test_tokens_store.py's shape for the sibling
TokenStore, since both are the same "abstract interface + reference
in-memory implementation" pattern.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from league_site.accounts import (
    AccountNotFoundError,
    AccountRecord,
    AccountStore,
    InMemoryAccountStore,
    account_id_for,
)


def _record(
    provider: str = "github",
    provider_user_id: str = "12345",
    *,
    display_name: str = "octocat",
    email: str | None = "octocat@example.com",
    blocked: bool = False,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
) -> AccountRecord:
    now = datetime.now(timezone.utc)
    return AccountRecord(
        account_id=account_id_for(provider, provider_user_id),
        provider=provider,
        provider_user_id=provider_user_id,
        display_name=display_name,
        email=email,
        blocked=blocked,
        created_at=created_at or now,
        updated_at=updated_at or now,
    )


def test_account_id_for_builds_the_provider_prefixed_id() -> None:
    assert account_id_for("github", "12345") == "github:12345"


def test_account_record_defaults_blocked_to_false() -> None:
    record = AccountRecord(
        account_id="github:1",
        provider="github",
        provider_user_id="1",
        display_name="octocat",
        email=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert record.blocked is False


def test_account_record_allows_an_absent_email() -> None:
    record = _record(email=None)
    assert record.email is None


def test_accountstore_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        AccountStore()  # type: ignore[abstract]


def test_get_missing_returns_none() -> None:
    store = InMemoryAccountStore()
    assert store.get("github:does-not-exist") is None


def test_upsert_then_get_round_trips() -> None:
    store = InMemoryAccountStore()
    record = _record()

    saved = store.upsert(record)

    assert saved == record
    assert store.get(record.account_id) == record


def test_upsert_is_idempotent_by_account_id() -> None:
    """Re-upserting the same account_id updates the one record in place,
    not creating a second entry."""
    store = InMemoryAccountStore()
    first = _record(display_name="octocat")
    store.upsert(first)

    second = _record(display_name="octocat-renamed")
    store.upsert(second)

    assert store.get(first.account_id).display_name == "octocat-renamed"


def test_upsert_preserves_created_at_on_update() -> None:
    store = InMemoryAccountStore()
    original_created = datetime.now(timezone.utc) - timedelta(days=30)
    first = _record(created_at=original_created)
    store.upsert(first)

    later = _record(created_at=datetime.now(timezone.utc), display_name="renamed")
    result = store.upsert(later)

    assert result.created_at == original_created
    assert store.get(first.account_id).created_at == original_created


def test_upsert_preserves_blocked_state_even_when_the_incoming_record_is_unblocked() -> None:
    """A routine sign-in re-upsert must never silently unblock a blocked
    account: blocking is exclusively set_blocked's job."""
    store = InMemoryAccountStore()
    store.upsert(_record())
    store.set_blocked(account_id_for("github", "12345"), True)

    # Simulates the OAuth callback re-upserting on a later sign-in, with a
    # freshly built record whose `blocked` defaults to False.
    reupserted = store.upsert(_record(display_name="octocat"))

    assert reupserted.blocked is True
    assert store.get(reupserted.account_id).blocked is True


def test_upsert_updates_mutable_identity_fields_from_the_incoming_record() -> None:
    store = InMemoryAccountStore()
    store.upsert(_record(display_name="octocat", email="old@example.com"))

    updated = store.upsert(_record(display_name="octocat-new", email="new@example.com"))

    assert updated.display_name == "octocat-new"
    assert updated.email == "new@example.com"


def test_set_blocked_flips_the_flag() -> None:
    store = InMemoryAccountStore()
    record = _record()
    store.upsert(record)

    store.set_blocked(record.account_id, True)

    assert store.get(record.account_id).blocked is True


def test_set_blocked_can_unblock() -> None:
    store = InMemoryAccountStore()
    record = _record(blocked=True)
    store.upsert(record)

    store.set_blocked(record.account_id, False)

    assert store.get(record.account_id).blocked is False


def test_set_blocked_bumps_updated_at() -> None:
    store = InMemoryAccountStore()
    stale = datetime.now(timezone.utc) - timedelta(days=1)
    record = _record(updated_at=stale)
    store.upsert(record)

    store.set_blocked(record.account_id, True)

    assert store.get(record.account_id).updated_at > stale


def test_set_blocked_does_not_disturb_other_accounts() -> None:
    store = InMemoryAccountStore()
    first = _record(provider_user_id="1")
    second = _record(provider_user_id="2")
    store.upsert(first)
    store.upsert(second)

    store.set_blocked(first.account_id, True)

    assert store.get(first.account_id).blocked is True
    assert store.get(second.account_id).blocked is False


def test_set_blocked_missing_account_id_raises_account_not_found_error() -> None:
    store = InMemoryAccountStore()
    with pytest.raises(AccountNotFoundError):
        store.set_blocked("github:does-not-exist", True)


def test_account_not_found_error_carries_the_offending_id() -> None:
    error = AccountNotFoundError("github:missing")
    assert error.account_id == "github:missing"
    assert "github:missing" in str(error)


# --- list_all (t4 operator listing) ------------------------------------------


def test_list_all_returns_every_account() -> None:
    store = InMemoryAccountStore()
    store.upsert(_record(provider_user_id="1"))
    store.upsert(_record(provider_user_id="2"))

    ids = {record.account_id for record in store.list_all()}
    assert ids == {account_id_for("github", "1"), account_id_for("github", "2")}


def test_list_all_reflects_blocked_state() -> None:
    store = InMemoryAccountStore()
    store.upsert(_record(provider_user_id="1"))
    store.upsert(_record(provider_user_id="2"))
    store.set_blocked(account_id_for("github", "1"), True)

    blocked_by_id = {record.account_id: record.blocked for record in store.list_all()}
    assert blocked_by_id[account_id_for("github", "1")] is True
    assert blocked_by_id[account_id_for("github", "2")] is False


def test_list_all_on_an_empty_store_is_empty() -> None:
    assert InMemoryAccountStore().list_all() == []


def test_account_store_list_all_default_raises_not_implemented() -> None:
    """The base-class default documents the contract concrete stores grow
    (see the DynamoDB adapter) — same pattern as ``TokenStore.list_all``."""

    class _Bare(AccountStore):
        def get(self, account_id: str) -> AccountRecord | None:  # pragma: no cover - unused
            raise AssertionError

        def upsert(self, record: AccountRecord) -> AccountRecord:  # pragma: no cover - unused
            raise AssertionError

        def set_blocked(self, account_id: str, blocked: bool) -> None:  # pragma: no cover
            raise AssertionError

    with pytest.raises(NotImplementedError):
        _Bare().list_all()
