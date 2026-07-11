"""Tests for the DynamoDB adapter skeleton in league_site.accounts.aws.

Every test injects a fake resource so nothing here ever touches real AWS,
needs credentials, or needs a region configured — the ``aws`` extra
(``boto3``) only needs to be importable, which `uv sync --extra aws`
guarantees for this suite. Mirrors tests/test_tokens_aws.py's shape for the
sibling DynamoDBTokenStore.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest
from botocore.exceptions import ClientError

from league_site.accounts.aws import DynamoDBAccountStore
from league_site.accounts.store import AccountNotFoundError, AccountRecord, account_id_for


def _split_top_level_commas(expression: str) -> list[str]:
    """Split ``expression`` on commas outside of parentheses.

    So a clause like ``if_not_exists(account_id, :account_id)`` stays one
    piece, not two — a plain ``str.split(",")`` would cut it in half.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in expression:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


_IF_NOT_EXISTS_RE = re.compile(r"if_not_exists\((\w+),\s*(:\w+)\)")


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table resource: an in-process dict.

    ``get_item``/``put_item`` cover the direct-by-partition-key access
    (``ACCOUNT#<account_id>``) most methods use; ``scan`` (paginated, and
    *not* evaluating ``FilterExpression`` server-side, exactly like the token
    fake) exercises :meth:`DynamoDBAccountStore.list_all`'s
    entity-type-filtered enumeration across the shared table. ``update_item``
    is a minimal-but-faithful emulation of boto3's real semantics for the two
    shapes :class:`DynamoDBAccountStore` actually issues: a ``SET`` expression
    whose clauses may be a plain ``:value`` (always overwritten) or
    ``if_not_exists(attr, :value)`` (written only the first time), plus the
    single ``ConditionExpression`` this codebase uses,
    ``"attribute_exists(PK)"`` — raising the same ``ConditionalCheckFailedException``
    shape real DynamoDB does when that condition fails, since
    :meth:`DynamoDBAccountStore.set_blocked` depends on catching exactly that.
    ``get_item``/``update_item`` calls are recorded so tests can assert on the
    exact kwargs a method issued (e.g. ``ConsistentRead=True``).
    """

    #: Small on purpose so a handful of saved items already forces >1 page.
    scan_page_size = 2

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}
        self.get_item_calls: list[dict[str, object]] = []
        self.update_item_calls: list[dict[str, object]] = []

    def put_item(
        self, *, Item: dict[str, object]
    ) -> None:  # noqa: N803 - matches boto3's kwarg casing
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(
        self, *, Key: dict[str, str], ConsistentRead: bool = False
    ) -> dict[str, object]:  # noqa: N803
        self.get_item_calls.append({"Key": Key, "ConsistentRead": ConsistentRead})
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item is not None else {}

    def update_item(
        self,
        *,
        Key: dict[str, str],
        UpdateExpression: str,
        ExpressionAttributeValues: dict[str, object] | None = None,
        ConditionExpression: str | None = None,
        ReturnValues: str | None = None,
    ) -> dict[str, object]:  # noqa: N803
        self.update_item_calls.append(
            {
                "Key": Key,
                "UpdateExpression": UpdateExpression,
                "ExpressionAttributeValues": ExpressionAttributeValues,
                "ConditionExpression": ConditionExpression,
                "ReturnValues": ReturnValues,
            }
        )
        key = (Key["PK"], Key["SK"])
        exists = key in self.items
        if ConditionExpression == "attribute_exists(PK)" and not exists:
            raise ClientError(
                {
                    "Error": {
                        "Code": "ConditionalCheckFailedException",
                        "Message": "The conditional request failed",
                    }
                },
                "UpdateItem",
            )
        item = dict(self.items.get(key, {"PK": Key["PK"], "SK": Key["SK"]}))
        values = ExpressionAttributeValues or {}
        assert UpdateExpression.startswith("SET "), UpdateExpression
        for clause in _split_top_level_commas(UpdateExpression[len("SET ") :]):
            attr, _, expr = clause.partition("=")
            attr = attr.strip()
            expr = expr.strip()
            match = _IF_NOT_EXISTS_RE.fullmatch(expr)
            if match:
                if attr not in item:
                    item[attr] = values[match.group(2)]
            else:
                item[attr] = values[expr]
        self.items[key] = item
        if ReturnValues == "ALL_NEW":
            return {"Attributes": dict(item)}
        return {}

    def scan(self, **kwargs: object) -> dict[str, object]:
        ordered = list(self.items.values())
        start_key = kwargs.get("ExclusiveStartKey")
        start_index = 0
        if start_key is not None:
            needle = (start_key["PK"], start_key["SK"])  # type: ignore[index]
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
    # upsert() never writes `blocked` at all (see its docstring) — a fresh
    # insert's item has no `blocked` key until set_blocked() writes one.
    assert "blocked" not in stored_item


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


def test_dynamodb_account_store_upsert_never_writes_the_blocked_attribute() -> None:
    """Regression test for the lost-update race (qodo bug / SonarCloud S5655 at
    aws.py:137): the old ``get_item`` + full ``put_item`` overwrite in ``upsert()``
    could clobber a concurrent ``set_blocked()``'s write, silently un-blocking an
    operator-blocked account. A single-threaded fake can't reproduce the race
    itself (calls never interleave), so this asserts directly on what was sent to
    DynamoDB: the fix's actual guarantee is that ``upsert()``'s ``UpdateExpression``
    never mentions ``blocked`` at all, on either an insert or an update — so no
    interleaving with ``set_blocked()`` can ever revert a block.
    """
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)

    store.upsert(_record())  # first insert
    store.upsert(_record(display_name="octocat-renamed"))  # subsequent update

    for call in resource.table.update_item_calls:
        assert "blocked" not in call["UpdateExpression"]
        values = call["ExpressionAttributeValues"] or {}
        assert ":blocked" not in values


def test_dynamodb_account_store_upsert_cannot_resurrect_a_stale_unblocked_record() -> None:
    """The behavioral half of the S5655 fix: block an account, then re-upsert with
    a record built as if from a stale read (``blocked=False``, the dataclass
    default) — the kind of record a normal OAuth re-sign-in re-upsert builds. The
    stored row must stay blocked."""
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    record = _record()
    store.upsert(record)
    store.set_blocked(record.account_id, True)
    stale_record = _record(display_name="octocat", blocked=False)

    reupserted = store.upsert(stale_record)

    assert reupserted.blocked is True
    assert store.get(record.account_id).blocked is True


def test_dynamodb_account_store_set_blocked_uses_a_conditional_update() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    store.upsert(_record())

    store.set_blocked(account_id_for("github", "12345"), True)

    call = resource.table.update_item_calls[-1]
    assert call["ConditionExpression"] == "attribute_exists(PK)"
    assert call["UpdateExpression"] == "SET blocked = :blocked, updated_at = :updated_at"


def test_dynamodb_account_store_get_issues_a_strongly_consistent_read() -> None:
    """Bug 2: ``get()`` is the request-time read ``tokens.verify()`` relies on
    (via the account store) to honor an operator block on the very next request —
    it must not use DynamoDB's default eventually-consistent read."""
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    store.upsert(_record())

    store.get(account_id_for("github", "12345"))

    assert resource.table.get_item_calls[-1]["ConsistentRead"] is True


def test_dynamodb_account_store_list_all_returns_every_account_across_scan_pages() -> None:
    """`list_all` feeds ``accounts list`` — it must return every account record
    across scan pages (the fake's page size of 2 forces pagination with three)."""
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    ids = [account_id_for("github", str(i)) for i in range(1, 4)]
    for account_id in ids:
        store.upsert(_record(provider_user_id=account_id.split(":")[1]))
    store.set_blocked(ids[1], True)

    records = store.list_all()

    assert {r.account_id for r in records} == set(ids)
    blocked_by_id = {r.account_id: r.blocked for r in records}
    assert blocked_by_id[ids[1]] is True
    assert blocked_by_id[ids[0]] is False


def test_dynamodb_account_store_list_all_ignores_non_account_items_in_the_shared_table() -> None:
    """Accounts share the physical tokens table; ``list_all`` must return only
    ``entity_type == "account"`` items, never the agent-token rows alongside."""
    resource = FakeDynamoDBResource()
    store = DynamoDBAccountStore("league-agent-tokens", resource=resource)
    store.upsert(_record())
    # A stray token item lands in the same table (as the real deployment does).
    resource.table.put_item(
        Item={
            "PK": "TOKEN#abc",
            "SK": "METADATA",
            "entity_type": "agent_token",
            "token_id": "tok-1",
        }
    )

    records = store.list_all()

    assert [r.account_id for r in records] == [account_id_for("github", "12345")]


def test_require_boto3_raises_runtime_error_when_boto3_is_unavailable(monkeypatch) -> None:
    import league_site.accounts.aws as accounts_aws_module

    monkeypatch.setattr(accounts_aws_module, "boto3", None)
    monkeypatch.setattr(accounts_aws_module, "_IMPORT_ERROR", ImportError("no boto3"))

    with pytest.raises(RuntimeError, match="aws"):
        DynamoDBAccountStore("league-agent-tokens", resource=FakeDynamoDBResource())
