"""Tests for the DynamoDB adapter skeleton in league_site.accounts.aws.

Every test injects a fake resource so nothing here ever touches real AWS,
needs credentials, or needs a region configured — the ``aws`` extra
(``boto3``) only needs to be importable, which `uv sync --extra aws`
guarantees for this suite. Mirrors tests/test_tokens_aws.py's shape for the
sibling DynamoDBTokenStore.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from league_site.accounts.aws import DynamoDBAccountStore
from league_site.accounts.store import AccountNotFoundError, AccountRecord, account_id_for


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table resource: an in-process dict.

    Only ``get_item``/``put_item`` are needed — unlike
    :mod:`league_site.auth.aws_tokens`'s token-hash lookup,
    :class:`DynamoDBAccountStore` addresses every item directly by its
    partition key (``ACCOUNT#<account_id>``), so there is no scan-fallback
    path here to fake.
    """

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}

    def put_item(
        self, *, Item: dict[str, object]
    ) -> None:  # noqa: N803 - matches boto3's kwarg casing
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(self, *, Key: dict[str, str]) -> dict[str, object]:  # noqa: N803
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item is not None else {}


class FakeDynamoDBResource:
    def __init__(self) -> None:
        self.table = FakeTable()

    def Table(self, name: str) -> FakeTable:  # noqa: N802 - matches boto3's method casing
        return self.table


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


def test_module_imports_and_boto3_is_available_via_the_aws_extra() -> None:
    import league_site.accounts.aws as accounts_aws_module

    assert accounts_aws_module.boto3 is not None


def test_dynamodb_account_store_upsert_then_get_round_trips_via_fake_resource() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    record = _record()

    store.upsert(record)
    loaded = store.get(record.account_id)

    assert loaded == record
    # and the fake table really did receive the documented item shape,
    # keyed into the *same* table the token store already uses
    stored_item = resource.table.items[(f"ACCOUNT#{record.account_id}", "METADATA")]
    assert stored_item["entity_type"] == "account"
    assert stored_item["provider"] == "github"
    assert stored_item["provider_user_id"] == "12345"
    assert stored_item["display_name"] == "octocat"
    assert stored_item["email"] == "octocat@example.com"
    assert stored_item["blocked"] is False


def test_dynamodb_account_store_get_missing_returns_none() -> None:
    store = DynamoDBAccountStore("league-agent-tokens", resource=FakeDynamoDBResource())
    assert store.get("github:does-not-exist") is None


def test_dynamodb_account_store_stores_an_absent_email_as_none() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    record = _record(email=None)

    store.upsert(record)

    assert store.get(record.account_id).email is None


def test_dynamodb_account_store_upsert_is_idempotent_by_account_id() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    store.upsert(_record(display_name="octocat"))

    store.upsert(_record(display_name="octocat-renamed"))

    assert len(resource.table.items) == 1
    assert store.get(account_id_for("github", "12345")).display_name == "octocat-renamed"


def test_dynamodb_account_store_upsert_preserves_created_at_on_update() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    original_created = datetime.now(timezone.utc) - timedelta(days=30)
    store.upsert(_record(created_at=original_created))

    result = store.upsert(_record(created_at=datetime.now(timezone.utc)))

    assert result.created_at == original_created
    assert store.get(result.account_id).created_at == original_created


def test_dynamodb_account_store_upsert_preserves_blocked_state() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    record = _record()
    store.upsert(record)
    store.set_blocked(record.account_id, True)

    reupserted = store.upsert(_record(display_name="octocat"))

    assert reupserted.blocked is True
    assert store.get(record.account_id).blocked is True


def test_dynamodb_account_store_set_blocked_flips_the_flag() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    record = _record()
    store.upsert(record)

    store.set_blocked(record.account_id, True)

    reloaded = store.get(record.account_id)
    assert reloaded.blocked is True
    # every other field is untouched
    assert reloaded.display_name == record.display_name
    assert reloaded.email == record.email


def test_dynamodb_account_store_set_blocked_bumps_updated_at() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    stale = datetime.now(timezone.utc) - timedelta(days=1)
    record = _record(updated_at=stale)
    store.upsert(record)

    store.set_blocked(record.account_id, True)

    assert store.get(record.account_id).updated_at > stale


def test_dynamodb_account_store_set_blocked_raises_account_not_found_error() -> None:
    store = DynamoDBAccountStore("league-agent-tokens", resource=FakeDynamoDBResource())
    with pytest.raises(AccountNotFoundError):
        store.set_blocked("github:does-not-exist", True)


def test_require_boto3_raises_runtime_error_when_boto3_is_unavailable(monkeypatch) -> None:
    import league_site.accounts.aws as accounts_aws_module

    monkeypatch.setattr(accounts_aws_module, "boto3", None)
    monkeypatch.setattr(accounts_aws_module, "_IMPORT_ERROR", ImportError("no boto3"))

    with pytest.raises(RuntimeError, match="aws"):
        DynamoDBAccountStore("league-agent-tokens", resource=FakeDynamoDBResource())
