"""DynamoDB-backed ``AccountStore`` — reuses the existing agent-tokens table.

This is the only module in :mod:`league_site.accounts` that imports
``boto3``. The import is guarded: ``boto3`` ships behind this project's
``aws`` extra (``uv sync --extra aws``), and nothing in
:mod:`league_site.accounts.store` or its test suite should require it —
importing this module without ``boto3`` installed raises a clear
``RuntimeError`` only when the adapter is actually *instantiated*, not
merely imported (mirrors :mod:`league_site.auth.aws_tokens` — read that
module first if this is the second adapter you're wiring up here).

Accepts a pre-built ``resource`` so callers (and tests) can inject a fake
and never touch real AWS — no credentials or region configuration are
required to exercise this module.

Table choice: reuse the agent-tokens table, not a new one
------------------------------------------------------------
Accounts are written into the *same physical table*
:mod:`league_site.auth.aws_tokens` already uses for agent tokens
(``TOKENS_TABLE_NAME`` — see :func:`league_site.aws_lambda.wiring.
build_account_store`), rather than provisioning a dedicated accounts
table. That table is already a generic ``PK``/``SK`` single-table design
with no GSIs (``infra/template.yaml``'s ``TokensTable``), so adding a
second entity type to it needs zero new ``AttributeDefinitions``, zero new
indexes, and zero ``infra/template.yaml`` changes at all — "least new
infra" for a launch-day feature that otherwise has no other reason to
touch infrastructure. The two entity types coexist by ``PK`` prefix alone,
exactly the way :mod:`league_site.ratings.aws` shares one ``RatingsTable``
between its ``LEDGER#*``/``IDENTITIES`` partitions:

    PK                          SK          Attributes
    TOKEN#<token_hash>          METADATA    entity_type="agent_token", ...
    ACCOUNT#<account_id>        METADATA    entity_type="account", account_id,
                                             provider, provider_user_id,
                                             display_name, email, blocked,
                                             created_at, updated_at

``account_id`` (e.g. ``"github:12345"``, see
:func:`league_site.accounts.store.account_id_for`) is the partition key
because every access here is a direct point lookup by that id — unlike
token verification (hash-keyed lookup, id-keyed revoke needing a scan),
accounts have exactly one identity key and no secondary lookup path, so no
scan/GSI tradeoff exists to document here.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime
from typing import Any

try:
    import boto3
except ImportError as exc:  # pragma: no cover - exercised only without the aws extra
    boto3 = None  # type: ignore[assignment]
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None

from league_site.accounts.store import AccountNotFoundError, AccountRecord, AccountStore, utcnow


def _require_boto3() -> None:
    if boto3 is None:
        raise RuntimeError(
            "boto3 is required for league_site.accounts.aws adapters; "
            "install it with `uv sync --extra aws`"
        ) from _IMPORT_ERROR


def _to_item(record: AccountRecord) -> dict[str, Any]:
    return {
        "PK": f"ACCOUNT#{record.account_id}",
        "SK": "METADATA",
        "entity_type": "account",
        "account_id": record.account_id,
        "provider": record.provider,
        "provider_user_id": record.provider_user_id,
        "display_name": record.display_name,
        "email": record.email,
        "blocked": record.blocked,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def _from_item(item: dict[str, Any]) -> AccountRecord:
    return AccountRecord(
        account_id=item["account_id"],
        provider=item["provider"],
        provider_user_id=item["provider_user_id"],
        display_name=item["display_name"],
        email=item["email"],
        created_at=datetime.fromisoformat(item["created_at"]),
        updated_at=datetime.fromisoformat(item["updated_at"]),
        blocked=bool(item["blocked"]),
    )


class DynamoDBAccountStore(AccountStore):
    """``AccountStore`` backed by a single DynamoDB table (single-table design).

    See the module docstring for the item shape this class reads and
    writes, and why it defaults to sharing the agent-tokens table rather
    than a dedicated one. Pass ``resource`` to inject a fake (or a
    pre-configured) ``boto3`` DynamoDB resource; otherwise one is built
    lazily from the default AWS config.
    """

    def __init__(self, table_name: str, *, resource: Any | None = None) -> None:
        _require_boto3()
        self._table_name = table_name
        self._resource = resource if resource is not None else boto3.resource("dynamodb")
        self._table = self._resource.Table(table_name)

    def get(self, account_id: str) -> AccountRecord | None:
        response = self._table.get_item(Key={"PK": f"ACCOUNT#{account_id}", "SK": "METADATA"})
        item = response.get("Item")
        if item is None:
            return None
        return _from_item(item)

    def upsert(self, record: AccountRecord) -> AccountRecord:
        existing = self.get(record.account_id)
        if existing is not None:
            record = dataclasses.replace(
                record, created_at=existing.created_at, blocked=existing.blocked
            )
        self._table.put_item(Item=_to_item(record))
        return record

    def set_blocked(self, account_id: str, blocked: bool) -> None:
        existing = self.get(account_id)
        if existing is None:
            raise AccountNotFoundError(account_id)
        updated = dataclasses.replace(existing, blocked=blocked, updated_at=utcnow())
        self._table.put_item(Item=_to_item(updated))

    def list_all(self) -> list[AccountRecord]:
        """Return every account record via a paginated, entity-type-filtered scan.

        Accounts share the physical agent-tokens table (see the module
        docstring), so the scan filters to ``entity_type == "account"`` to
        return only account rows, never the ``TOKEN#*`` items alongside them.
        The filter is applied server-side (``FilterExpression``) *and*
        re-checked client-side so this stays correct against a scan that
        doesn't filter for real. O(n) in the table's items at launch scale —
        the same tradeoff :meth:`DynamoDBTokenStore.list_all` documents.
        """
        from boto3.dynamodb.conditions import Attr

        records: list[AccountRecord] = []
        scan_kwargs: dict[str, Any] = {"FilterExpression": Attr("entity_type").eq("account")}
        while True:
            response = self._table.scan(**scan_kwargs)
            for item in response.get("Items", []):
                if item.get("entity_type") == "account":
                    records.append(_from_item(item))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return records
            scan_kwargs["ExclusiveStartKey"] = last_key
