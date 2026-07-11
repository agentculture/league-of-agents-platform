"""Tests for the DynamoDB adapter skeleton in league_site.auth.aws_tokens.

Every test injects a fake resource so nothing here ever touches real AWS,
needs credentials, or needs a region configured — the ``aws`` extra
(``boto3``) only needs to be importable, which `uv sync --extra aws`
guarantees for this suite.
"""

from __future__ import annotations

from typing import Any

import pytest

from league_site.auth import tokens
from league_site.auth.aws_tokens import DynamoDBTokenStore
from league_site.auth.token_store import InMemoryTokenStore, TokenNotFoundError, TokenRecord


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table resource: an in-process dict.

    :meth:`scan` and :meth:`update_item` mimic just enough of the real
    ``Table`` contract to exercise :meth:`DynamoDBTokenStore.revoke`'s
    paginated-scan fallback (see that method's docstring): ``scan`` hands
    back at most :attr:`scan_page_size` items per call plus a
    ``LastEvaluatedKey`` whenever more remain, and ``update_item`` flips
    ``revoked`` on the addressed item in place — neither evaluates
    ``FilterExpression``/``UpdateExpression`` for real (this fake's items are
    all single-attribute-per-update already), the point is proving the
    scan-then-update pagination loop, not reimplementing DynamoDB's
    expression language.
    """

    #: Small on purpose so a handful of saved items already forces >1 page.
    scan_page_size = 2

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}

    def put_item(
        self, *, Item: dict[str, object]
    ) -> None:  # noqa: N803 - matches boto3's kwarg casing
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(self, *, Key: dict[str, str]) -> dict[str, object]:  # noqa: N803
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item is not None else {}

    def scan(self, **kwargs: object) -> dict[str, object]:
        ordered = list(self.items.values())
        start_key: Any = kwargs.get("ExclusiveStartKey")
        start_index = 0
        if start_key is not None:
            needle = (start_key["PK"], start_key["SK"])
            for i, item in enumerate(ordered):
                if (item["PK"], item["SK"]) == needle:
                    start_index = i + 1
                    break
        page = ordered[start_index : start_index + self.scan_page_size]
        response: dict[str, object] = {"Items": page}
        next_index = start_index + self.scan_page_size
        if next_index < len(ordered):
            last = page[-1]
            response["LastEvaluatedKey"] = {"PK": last["PK"], "SK": last["SK"]}
        return response

    def update_item(
        self, *, Key: dict[str, str], UpdateExpression: str, ExpressionAttributeValues: dict
    ) -> None:  # noqa: N803
        assert UpdateExpression == "SET revoked = :revoked"
        item = self.items[(Key["PK"], Key["SK"])]
        item["revoked"] = ExpressionAttributeValues[":revoked"]


class FakeDynamoDBResource:
    def __init__(self) -> None:
        self.table = FakeTable()

    def Table(self, name: str) -> FakeTable:  # noqa: N802 - matches boto3's method casing
        return self.table


def _record(
    token_hash: str = "a" * 64,
    *,
    owner_account_id: str | None = None,
    blocked: bool = False,
) -> TokenRecord:
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
        owner_account_id=owner_account_id,
        blocked=blocked,
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


def test_dynamodb_token_store_revoke_marks_the_matching_record_revoked() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBTokenStore("league-agent-tokens", resource=resource)
    record = _record(token_hash="c" * 64)
    store.save(record)
    assert store.get_by_hash(record.token_hash).revoked is False

    store.revoke(record.token_id)

    reloaded = store.get_by_hash(record.token_hash)
    assert reloaded.revoked is True
    # every other field is untouched
    assert reloaded.token_id == record.token_id
    assert reloaded.agent_name == record.agent_name


def test_dynamodb_token_store_revoke_raises_token_not_found_for_an_unknown_id() -> None:
    store = DynamoDBTokenStore("league-agent-tokens", resource=FakeDynamoDBResource())
    with pytest.raises(TokenNotFoundError):
        store.revoke("does-not-exist")


def test_dynamodb_token_store_revoke_finds_the_record_across_multiple_scan_pages() -> None:
    """FakeTable.scan_page_size (2) is smaller than the number of saved
    tokens below: this only passes if revoke() actually follows
    LastEvaluatedKey across pages rather than giving up after the first."""
    resource = FakeDynamoDBResource()
    store = DynamoDBTokenStore("league-agent-tokens", resource=resource)
    records = [_record(token_hash=str(i) * 64) for i in range(1, 6)]
    for record in records:
        store.save(record)
    target = records[-1]  # lands on a later scan page
    assert resource.table.scan_page_size < len(records)

    store.revoke(target.token_id)

    assert store.get_by_hash(target.token_hash).revoked is True
    # nothing else got touched
    for other in records[:-1]:
        assert store.get_by_hash(other.token_hash).revoked is False


def test_require_boto3_raises_runtime_error_when_boto3_is_unavailable(monkeypatch) -> None:
    import league_site.auth.aws_tokens as aws_tokens_module

    monkeypatch.setattr(aws_tokens_module, "boto3", None)
    monkeypatch.setattr(aws_tokens_module, "_IMPORT_ERROR", ImportError("no boto3"))

    with pytest.raises(RuntimeError, match="aws"):
        DynamoDBTokenStore("league-agent-tokens", resource=FakeDynamoDBResource())


def test_dynamodb_token_store_round_trips_owner_account_id_and_blocked() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBTokenStore("league-agent-tokens", resource=resource)
    record = _record(token_hash="d" * 64, owner_account_id="github:4242", blocked=True)

    store.save(record)
    loaded = store.get_by_hash(record.token_hash)

    assert loaded == record
    assert loaded.owner_account_id == "github:4242"
    assert loaded.blocked is True
    # and the account-ownership fields land in the stored single-table item
    stored_item = resource.table.items[(f"TOKEN#{record.token_hash}", "METADATA")]
    assert stored_item["owner_account_id"] == "github:4242"
    assert stored_item["blocked"] is True


def test_dynamodb_token_store_stores_none_owner_account_id_for_an_anonymous_record() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBTokenStore("league-agent-tokens", resource=resource)
    record = _record(token_hash="e" * 64)  # owner_account_id defaults to None

    store.save(record)
    loaded = store.get_by_hash(record.token_hash)

    assert loaded.owner_account_id is None
    assert loaded.blocked is False


def test_dynamodb_token_store_loads_and_verifies_a_legacy_item_missing_the_new_attributes() -> None:
    """A record written before t3 has no ``owner_account_id``/``blocked`` keys.

    It must still deserialize (defaulting to anonymous/unblocked) and still
    verify end-to-end — the migration must not strand any existing prod token.
    """
    resource = FakeDynamoDBResource()
    store = DynamoDBTokenStore("league-agent-tokens", resource=resource)
    issued = tokens.issue(
        store, agent_name="probe-bot", model="claude-sonnet-5", provider="anthropic"
    )
    # Simulate a pre-t3 item: strip the attributes the old _to_item never wrote.
    (item,) = resource.table.items.values()
    del item["owner_account_id"]
    del item["blocked"]

    loaded = store.get_by_hash(item["token_hash"])
    assert loaded.owner_account_id is None
    assert loaded.blocked is False
    # legacy record still resolves through the request-path verify()
    identity = tokens.verify(store, issued.token)
    assert identity is not None
    assert identity.agent_name == "probe-bot"


def test_dynamodb_token_store_revoked_token_fails_verification() -> None:
    """Acceptance: revoke() works against the DynamoDB adapter and a revoked

    token no longer verifies — exercised through the real issue/verify/revoke
    flow against the injected fake resource.
    """
    resource = FakeDynamoDBResource()
    store = DynamoDBTokenStore("league-agent-tokens", resource=resource)
    issued = tokens.issue(
        store, agent_name="probe-bot", model="claude-sonnet-5", provider="anthropic"
    )
    assert tokens.verify(store, issued.token) is not None

    tokens.revoke(store, issued.identity.token_id)

    assert tokens.verify(store, issued.token) is None


def test_dynamodb_token_store_list_all_returns_every_record_revoked_included() -> None:
    """`list_all` feeds the self-serve issuance guard (see token_store.py) —

    it must return every record, revoked included, across scan pages (the
    FakeTable's page size of 2 forces pagination with three records).
    """
    store = DynamoDBTokenStore("tokens-table", resource=FakeDynamoDBResource())
    records_in = [_record(token_hash=ch * 64) for ch in ("a", "b", "c")]
    for record in records_in:
        store.save(record)
    store.revoke(records_in[1].token_id)

    records = store.list_all()

    assert {r.token_hash for r in records} == {r.token_hash for r in records_in}
    revoked_flags = {r.token_hash: r.revoked for r in records}
    assert revoked_flags[records_in[1].token_hash] is True
    assert revoked_flags[records_in[0].token_hash] is False
