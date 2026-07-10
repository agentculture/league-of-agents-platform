"""DynamoDB-backed ``TokenStore`` skeleton for agent token persistence.

This is the only module in :mod:`league_site.auth` that imports ``boto3``.
The import is guarded: ``boto3`` ships behind this project's ``aws`` extra
(``uv sync --extra aws``), and nothing in :mod:`league_site.auth.tokens` or
its test suite should require it — importing this module without ``boto3``
installed raises a clear ``RuntimeError`` only when the adapter is actually
*instantiated*, not merely imported (mirrors :mod:`league_site.matches.aws`
— read that module first if you're wiring up a second adapter here).

Accepts a pre-built ``resource`` so callers (and tests) can inject a fake
and never touch real AWS — no credentials or region configuration are
required to exercise this module.

DynamoDB single-table design
-----------------------------
One table, keyed by a generic ``PK``/``SK`` pair. A token's record is a
single item::

    PK                    SK          Attributes
    TOKEN#<token_hash>    METADATA    entity_type, token_id, token_hash,
                                       agent_name, model, provider,
                                       created_at, revoked

``token_hash`` is the partition key because every request-path lookup is by
hash (:func:`league_site.auth.tokens.verify` hashes the presented bearer
token and looks it up). Revoking by ``token_id`` — the identifier
:func:`league_site.auth.tokens.revoke` receives — would ideally use a GSI on
``token_id`` that isn't wired up yet; until it exists,
:meth:`DynamoDBTokenStore.revoke` falls back to a paginated full-table scan
(the same O(n)-at-launch-scale tradeoff ``DynamoDBMatchStore.list_ids`` in
:mod:`league_site.matches.aws` documents) so revocation isn't blocked on
standing up the GSI first — see that method's docstring for the
recommendation to add the GSI later.
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

from league_site.auth.token_store import TokenNotFoundError, TokenRecord, TokenStore


def _require_boto3() -> None:
    if boto3 is None:
        raise RuntimeError(
            "boto3 is required for league_site.auth.aws_tokens adapters; "
            "install it with `uv sync --extra aws`"
        ) from _IMPORT_ERROR


def _to_item(record: TokenRecord) -> dict[str, Any]:
    return {
        "PK": f"TOKEN#{record.token_hash}",
        "SK": "METADATA",
        "entity_type": "agent_token",
        "token_id": record.token_id,
        "token_hash": record.token_hash,
        "agent_name": record.agent_name,
        "model": record.model,
        "provider": record.provider,
        "created_at": record.created_at.isoformat(),
        "revoked": record.revoked,
    }


def _from_item(item: dict[str, Any]) -> TokenRecord:
    return TokenRecord(
        token_id=item["token_id"],
        token_hash=item["token_hash"],
        agent_name=item["agent_name"],
        model=item["model"],
        provider=item["provider"],
        created_at=datetime.fromisoformat(item["created_at"]),
        revoked=bool(item["revoked"]),
    )


class DynamoDBTokenStore(TokenStore):
    """``TokenStore`` backed by a single DynamoDB table (single-table design).

    See the module docstring for the item shape this class reads and
    writes. Pass ``resource`` to inject a fake (or a pre-configured)
    ``boto3`` DynamoDB resource; otherwise one is built lazily from the
    default AWS config.
    """

    def __init__(self, table_name: str, *, resource: Any | None = None) -> None:
        _require_boto3()
        self._table_name = table_name
        self._resource = resource if resource is not None else boto3.resource("dynamodb")
        self._table = self._resource.Table(table_name)

    def save(self, record: TokenRecord) -> None:
        self._table.put_item(Item=_to_item(record))

    def get_by_hash(self, token_hash: str) -> TokenRecord | None:
        response = self._table.get_item(Key={"PK": f"TOKEN#{token_hash}", "SK": "METADATA"})
        item = response.get("Item")
        if item is None:
            return None
        return _from_item(item)

    def revoke(self, token_id: str) -> None:
        """Mark the token identified by ``token_id`` as revoked.

        No GSI on ``token_id`` exists yet (see the module docstring), so this
        is a paginated full-table scan filtered to ``token_id`` followed by a
        targeted ``update_item`` on the one matching item — O(n) in the
        number of issued tokens. That is an accepted, documented tradeoff at
        launch scale (agent token counts are small; see
        :mod:`league_site.capacity.config` for the same reasoning applied to
        matches) so that revocation isn't blocked on standing up the GSI
        first. Recommendation for a follow-up: add a GSI keyed on
        ``token_id`` (``GSI1PK=TOKEN_ID#<id>``) once issued-token volume
        makes an O(n) scan noticeable, and swap this for a ``Query``.

        Raises :class:`~league_site.auth.token_store.TokenNotFoundError` if
        no record has that ``token_id`` — the scan exhausts every page
        without finding a match.
        """
        from boto3.dynamodb.conditions import Attr

        scan_kwargs: dict[str, Any] = {
            "ProjectionExpression": "PK, SK, token_id",
            "FilterExpression": Attr("token_id").eq(token_id),
        }
        while True:
            response = self._table.scan(**scan_kwargs)
            # Re-check `token_id` client-side rather than trusting every
            # returned item already matches: `FilterExpression` is applied
            # server-side by real DynamoDB, but this stays correct even
            # against a scan that doesn't (or can't) filter server-side.
            for item in response.get("Items", []):
                if item.get("token_id") == token_id:
                    self._table.update_item(
                        Key={"PK": item["PK"], "SK": item["SK"]},
                        UpdateExpression="SET revoked = :revoked",
                        ExpressionAttributeValues={":revoked": True},
                    )
                    return
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        raise TokenNotFoundError(token_id)
