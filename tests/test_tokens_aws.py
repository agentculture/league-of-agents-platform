"""Tests for the DynamoDB adapter skeleton in league_site.auth.aws_tokens.

Every test injects a fake resource so nothing here ever touches real AWS,
needs credentials, or needs a region configured — the ``aws`` extra
(``boto3``) only needs to be importable, which `uv sync --extra aws`
guarantees for this suite.
"""

from __future__ import annotations

import pytest

from league_site.auth import tokens
from league_site.auth.aws_tokens import DynamoDBTokenStore
from league_site.auth.token_store import InMemoryTokenStore, TokenRecord


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table resource: an in-process dict."""

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


def _record(token_hash: str = "a" * 64) -> TokenRecord:
    """A realistic TokenRecord, minted via tokens.issue() against a throwaway in-memory store."""
    fixture_store = InMemoryTokenStore()
    issued = tokens.issue(
        fixture_store, agent_name="probe-bot", model="claude-sonnet-5", provider="anthropic"
    )
    return TokenRecord(
        token_id=issued.identity.token_id,
        token_hash=token_hash,
        agent_name=issued.identity.agent_name,
        model=issued.identity.model,
        provider=issued.identity.provider,
        created_at=issued.identity.created_at,
    )


def test_module_imports_and_boto3_is_available_via_the_aws_extra() -> None:
    import league_site.auth.aws_tokens as aws_tokens_module

    assert aws_tokens_module.boto3 is not None


def test_dynamodb_token_store_save_and_get_by_hash_round_trip_via_fake_resource() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBTokenStore("league-agent-tokens", resource=resource)
    record = _record(token_hash="b" * 64)

    store.save(record)
    loaded = store.get_by_hash(record.token_hash)

    assert loaded == record
    # And the fake table really did receive the documented single-table item shape.
    stored_item = resource.table.items[(f"TOKEN#{record.token_hash}", "METADATA")]
    assert stored_item["token_hash"] == record.token_hash
    assert stored_item["agent_name"] == record.agent_name
    assert stored_item["entity_type"] == "agent_token"


def test_dynamodb_token_store_get_by_hash_missing_returns_none() -> None:
    store = DynamoDBTokenStore("league-agent-tokens", resource=FakeDynamoDBResource())
    assert store.get_by_hash("does-not-exist") is None


class _RecordingStore:
    """Captures the TokenRecord tokens.issue() builds, without persisting it anywhere real."""

    def __init__(self) -> None:
        self.saved: TokenRecord | None = None

    def save(self, record: TokenRecord) -> None:
        self.saved = record


def test_dynamodb_token_store_stored_item_never_carries_the_plaintext_token() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBTokenStore("league-agent-tokens", resource=resource)
    recording_store = _RecordingStore()

    issued = tokens.issue(
        recording_store, agent_name="probe-bot", model="claude-sonnet-5", provider="anthropic"
    )
    assert recording_store.saved is not None
    store.save(recording_store.saved)

    stored_item = resource.table.items[(f"TOKEN#{recording_store.saved.token_hash}", "METADATA")]
    assert issued.token not in str(stored_item)


def test_dynamodb_token_store_revoke_is_not_wired_up_yet() -> None:
    store = DynamoDBTokenStore("league-agent-tokens", resource=FakeDynamoDBResource())
    with pytest.raises(NotImplementedError):
        store.revoke("tok-1")


def test_require_boto3_raises_runtime_error_when_boto3_is_unavailable(monkeypatch) -> None:
    import league_site.auth.aws_tokens as aws_tokens_module

    monkeypatch.setattr(aws_tokens_module, "boto3", None)
    monkeypatch.setattr(aws_tokens_module, "_IMPORT_ERROR", ImportError("no boto3"))

    with pytest.raises(RuntimeError, match="aws"):
        DynamoDBTokenStore("league-agent-tokens", resource=FakeDynamoDBResource())
