"""Tests for the accounts half of :mod:`league_site.aws_lambda.wiring`.

:func:`~league_site.aws_lambda.wiring.build_account_store` is the env-driven
constructor the accounts store follows the same pattern as
:func:`~league_site.aws_lambda.wiring.build_site_app`'s
match/token/ratings selection: no table env var -> a fresh in-memory store
(local dev, the test suite); the tokens table env var present -> a
DynamoDB-backed store sharing that same table (see
:mod:`league_site.accounts.aws`'s module docstring for why accounts reuse
the agent-tokens table rather than a dedicated one). Every DynamoDB-path
test injects a fake resource so nothing here ever touches real AWS.
"""

from __future__ import annotations

from typing import Any

from league_site.accounts.aws import DynamoDBAccountStore
from league_site.accounts.store import AccountRecord, InMemoryAccountStore, account_id_for
from league_site.aws_lambda import wiring
from tests._dynamo_fake import apply_set_expression


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table: just enough for account items.

    ``upsert`` now writes through ``update_item`` (SET with insert-only
    ``if_not_exists`` clauses) rather than a full ``put_item`` overwrite, and
    the request-time reads pass ``ConsistentRead=True`` — so this fake models
    both (delegating the SET semantics to :func:`tests._dynamo_fake.
    apply_set_expression`).
    """

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(self, *, Item: dict[str, Any]) -> None:  # noqa: N803
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(
        self, *, Key: dict[str, str], ConsistentRead: bool = False  # noqa: N803
    ) -> dict[str, Any]:
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item is not None else {}

    def update_item(
        self,
        *,
        Key: dict[str, str],  # noqa: N803
        UpdateExpression: str,  # noqa: N803
        ExpressionAttributeValues: dict[str, Any],  # noqa: N803
        ConditionExpression: str | None = None,  # noqa: N803
        ReturnValues: str | None = None,  # noqa: N803
    ) -> dict[str, Any]:
        key = (Key["PK"], Key["SK"])
        item = dict(self.items.get(key, {"PK": Key["PK"], "SK": Key["SK"]}))
        apply_set_expression(item, UpdateExpression, ExpressionAttributeValues)
        self.items[key] = item
        return {"Attributes": dict(item)} if ReturnValues == "ALL_NEW" else {}


class FakeDynamoDBResource:
    """Stand-in for ``boto3.resource("dynamodb")``: one :class:`FakeTable` per name."""

    def __init__(self) -> None:
        self.tables: dict[str, FakeTable] = {}

    def Table(self, name: str) -> FakeTable:  # noqa: N802
        return self.tables.setdefault(name, FakeTable())


def _record() -> AccountRecord:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return AccountRecord(
        account_id=account_id_for("github", "12345"),
        provider="github",
        provider_user_id="12345",
        display_name="octocat",
        email="octocat@example.com",
        created_at=now,
        updated_at=now,
    )


def test_no_tokens_table_env_builds_an_in_memory_account_store() -> None:
    store = wiring.build_account_store({})
    assert isinstance(store, InMemoryAccountStore)


def test_tokens_table_env_selects_a_dynamodb_backed_account_store() -> None:
    resource = FakeDynamoDBResource()
    store = wiring.build_account_store(
        {"TOKENS_TABLE_NAME": "league-tokens"}, dynamodb_resource=resource
    )
    assert isinstance(store, DynamoDBAccountStore)


def test_dynamodb_account_store_shares_the_tokens_table_not_a_new_one() -> None:
    """Least-new-infra: accounts must land in the same physical table the
    token store already writes to, not a table of their own."""
    resource = FakeDynamoDBResource()
    account_store = wiring.build_account_store(
        {"TOKENS_TABLE_NAME": "league-tokens"}, dynamodb_resource=resource
    )

    account_store.upsert(_record())

    assert set(resource.tables) == {"league-tokens"}
    stored_item = resource.tables["league-tokens"].items[("ACCOUNT#github:12345", "METADATA")]
    assert stored_item["entity_type"] == "account"


def test_account_created_at_sign_in_survives_a_simulated_cold_start() -> None:
    """A fresh `build_account_store()` call over the same (fake) table --
    a new Lambda cold start -- must still see an account created before it."""
    resource = FakeDynamoDBResource()
    env = {"TOKENS_TABLE_NAME": "league-tokens"}
    first_cold_start = wiring.build_account_store(env, dynamodb_resource=resource)
    record = _record()

    first_cold_start.upsert(record)

    second_cold_start = wiring.build_account_store(env, dynamodb_resource=resource)
    reloaded = second_cold_start.get(record.account_id)

    assert reloaded == record
    assert reloaded is not record  # genuinely re-read, not a shared in-process object


def test_env_var_name_reused_for_accounts_matches_the_deploy_contract() -> None:
    """No new table env var: accounts ride on the existing tokens table name."""
    assert wiring.TOKENS_TABLE_ENV == "TOKENS_TABLE_NAME"
