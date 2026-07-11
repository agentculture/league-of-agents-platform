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
                                             display_name, email,
                                             created_at, updated_at, blocked?

``blocked`` is marked optional (``blocked?``) deliberately: :meth:`upsert`
never writes it — only :meth:`set_blocked` does — so a freshly inserted
account's item has no ``blocked`` attribute at all until an operator blocks
it for the first time. See :meth:`DynamoDBAccountStore.upsert`'s docstring
for why (the lost-update fix), and :func:`_from_item` for how a missing
attribute reads as ``False``.

``account_id`` (e.g. ``"github:12345"``, see
:func:`league_site.accounts.store.account_id_for`) is the partition key
because every access here is a direct point lookup by that id — unlike
token verification (hash-keyed lookup, id-keyed revoke needing a scan),
accounts have exactly one identity key and no secondary lookup path, so no
scan/GSI tradeoff exists to document here.
"""

from __future__ import annotations

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


def _from_item(item: dict[str, Any]) -> AccountRecord:
    # ``blocked`` is read with ``.get`` — not ``item["blocked"]`` — because
    # :meth:`DynamoDBAccountStore.upsert` never writes that attribute (see its
    # docstring): a freshly inserted account's item simply has no ``blocked``
    # key until :meth:`DynamoDBAccountStore.set_blocked` writes one for the
    # first time. Absent means "never blocked", i.e. ``False`` — the same
    # defensive-default shape :func:`league_site.auth.aws_tokens._from_item`
    # already uses for ``owner_account_id``/``blocked`` on a legacy item.
    return AccountRecord(
        account_id=item["account_id"],
        provider=item["provider"],
        provider_user_id=item["provider_user_id"],
        display_name=item["display_name"],
        email=item["email"],
        created_at=datetime.fromisoformat(item["created_at"]),
        updated_at=datetime.fromisoformat(item["updated_at"]),
        blocked=bool(item.get("blocked", False)),
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
        # Strongly consistent: this is the request-time read
        # league_site.auth.tokens.verify() relies on (via the account_store it
        # is passed) to honor an operator's set_blocked() on the very next
        # request. DynamoDB's get_item defaults to eventually consistent reads,
        # which could still return a just-blocked account's stale, unblocked
        # row for a short window — ConsistentRead=True closes that window.
        response = self._table.get_item(
            Key={"PK": f"ACCOUNT#{account_id}", "SK": "METADATA"}, ConsistentRead=True
        )
        item = response.get("Item")
        if item is None:
            return None
        return _from_item(item)

    def upsert(self, record: AccountRecord) -> AccountRecord:
        """Insert or update the account identified by ``record.account_id``.

        Writes via a targeted ``update_item`` rather than a get-then-put
        overwrite (the old shape) so this can never lose a concurrent write —
        in particular, it can never resurrect a blocked account. A normal
        OAuth re-``upsert`` and an operator's :meth:`set_blocked` can race
        (this method reads nothing before writing), and the fix is to make
        each write own disjoint attributes rather than round-trip the whole
        item: this ``UpdateExpression`` ``SET``s only the mutable profile
        fields (``display_name``, ``email``, ``updated_at``) unconditionally,
        and the identity/insert-only fields (``account_id``, ``provider``,
        ``provider_user_id``, ``entity_type``, ``created_at``) only via
        ``if_not_exists(...)`` so they're stamped once on first insert and
        never move again. It deliberately never mentions ``blocked`` at all —
        that attribute is exclusively :meth:`set_blocked`'s to write, so this
        method can't revert a block no matter how it interleaves with one.
        ``ReturnValues="ALL_NEW"`` hands back the durable row in one round
        trip, so the returned record reflects what is actually stored
        (including ``created_at``/``blocked`` from any prior write).
        """
        response = self._table.update_item(
            Key={"PK": f"ACCOUNT#{record.account_id}", "SK": "METADATA"},
            UpdateExpression=(
                "SET display_name = :display_name, "
                "email = :email, "
                "updated_at = :updated_at, "
                "account_id = if_not_exists(account_id, :account_id), "
                "provider = if_not_exists(provider, :provider), "
                "provider_user_id = if_not_exists(provider_user_id, :provider_user_id), "
                "entity_type = if_not_exists(entity_type, :entity_type), "
                "created_at = if_not_exists(created_at, :created_at)"
            ),
            ExpressionAttributeValues={
                ":display_name": record.display_name,
                ":email": record.email,
                ":updated_at": record.updated_at.isoformat(),
                ":account_id": record.account_id,
                ":provider": record.provider,
                ":provider_user_id": record.provider_user_id,
                ":entity_type": "account",
                ":created_at": record.created_at.isoformat(),
            },
            ReturnValues="ALL_NEW",
        )
        return _from_item(response["Attributes"])

    def set_blocked(self, account_id: str, blocked: bool) -> None:
        """Flip the ``blocked`` flag for ``account_id`` and stamp ``updated_at``.

        A targeted ``update_item`` that ``SET``s only ``blocked``/``updated_at``
        — never the whole item — so it can never be undone by a concurrent
        :meth:`upsert` (which, per that method's docstring, never writes
        ``blocked`` at all). ``ConditionExpression="attribute_exists(PK)"``
        makes "no such account" fail loudly at DynamoDB rather than silently
        creating a partial item; the resulting ``ConditionalCheckFailedException``
        is translated into :class:`AccountNotFoundError`, preserving this
        method's existing contract.
        """
        from botocore.exceptions import ClientError

        try:
            self._table.update_item(
                Key={"PK": f"ACCOUNT#{account_id}", "SK": "METADATA"},
                UpdateExpression="SET blocked = :blocked, updated_at = :updated_at",
                ExpressionAttributeValues={
                    ":blocked": blocked,
                    ":updated_at": utcnow().isoformat(),
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code == "ConditionalCheckFailedException":
                raise AccountNotFoundError(account_id) from exc
            raise

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
