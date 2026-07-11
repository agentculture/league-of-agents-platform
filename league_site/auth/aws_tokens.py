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
                                       created_at, revoked,
                                       owner_account_id, blocked

``owner_account_id`` (the owning human account, ``None`` for a legacy or
anonymous token) and ``blocked`` (the operator kill-switch flag) are the
account-anchoring fields; :func:`_from_item` reads both defensively so a
pre-account item that predates them still loads (see that function).

``token_hash`` is the partition key because every request-path lookup is by
hash (:func:`league_site.auth.tokens.verify` hashes the presented bearer
token and looks it up).

Revocation design
-----------------
:func:`league_site.auth.tokens.revoke` addresses a token by ``token_id``, not
by ``token_hash``, so it cannot use the primary key. Three shapes were on the
table — a GSI on ``token_id``, a key redesign, or a tombstone (delete the
item) — chosen against one goal: a revoked token must fail
:func:`~league_site.auth.tokens.verify` with the least migration risk for
records already in the prod table. **The choice is a scan-then-flag: locate
the item with a paginated full-table scan filtered to ``token_id``, then flip
its existing ``revoked`` attribute to ``True`` with a targeted
``update_item``.** ``verify`` already returns ``None`` for a ``revoked``
record, so revocation takes effect naturally with no change to the request
path — the flag design needs no new key, no GSI to provision, and no backfill,
since every prod record already carries ``revoked`` (written since token
issuance shipped). A **tombstone** was rejected on purpose: deleting the item
would make ``verify`` fail via a missing record, but it would also drop the
row from :meth:`DynamoDBTokenStore.list_all`, and the self-serve issuance
guard counts revoked records inside its rolling window
(:meth:`league_site.auth.token_store.TokenStore.list_all`) — revoking must not
refund abuse budget — so a hard delete would silently weaken the cap and lose
the audit trail. A **GSI** on ``token_id`` is the right scale-up but buys no
correctness at launch volume; the tradeoff is that the scan is O(n) in issued
tokens (the same launch-scale tradeoff ``DynamoDBMatchStore.list_ids`` in
:mod:`league_site.matches.aws` documents). Add the GSI and swap the scan for a
``Query`` once issued-token volume makes the scan noticeable — see
:meth:`DynamoDBTokenStore.revoke`.
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
        # ``None`` serializes to a DynamoDB NULL for an anonymous/legacy token;
        # a value binds the token to its owning human account.
        "owner_account_id": record.owner_account_id,
        "blocked": record.blocked,
    }


def _from_item(item: dict[str, Any]) -> TokenRecord:
    # ``owner_account_id``/``blocked`` are read with ``.get`` so an item
    # written before these fields existed still deserializes: a legacy record
    # loads as an anonymous (``None``), unblocked (``False``) token. Do not
    # tighten this to ``item[...]`` — that would strand every pre-account
    # token already in the prod table.
    return TokenRecord(
        token_id=item["token_id"],
        token_hash=item["token_hash"],
        agent_name=item["agent_name"],
        model=item["model"],
        provider=item["provider"],
        created_at=datetime.fromisoformat(item["created_at"]),
        revoked=bool(item["revoked"]),
        owner_account_id=item.get("owner_account_id"),
        blocked=bool(item.get("blocked", False)),
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
        # Strongly consistent: this is the per-request lookup
        # league_site.auth.tokens.verify() calls on every bearer-token check.
        # DynamoDB's get_item defaults to eventually consistent reads, which
        # could return a just-blocked/revoked token's stale row for a short
        # window after an operator's write — breaking verify()'s documented
        # "the flag is read fresh every call ... honoured on the very next
        # request" guarantee. ConsistentRead=True closes that window.
        response = self._table.get_item(
            Key={"PK": f"TOKEN#{token_hash}", "SK": "METADATA"}, ConsistentRead=True
        )
        item = response.get("Item")
        if item is None:
            return None
        return _from_item(item)

    def list_all(self) -> list[TokenRecord]:
        """Return every record — revoked included — via a paginated full scan.

        This is the surface the self-serve issuance guard reads (see
        :meth:`league_site.auth.token_store.TokenStore.list_all`); the same
        launch-scale O(n) tradeoff as :meth:`revoke` applies.
        """
        from boto3.dynamodb.conditions import Attr

        records: list[TokenRecord] = []
        # Accounts share this table (single-table design — see
        # league_site/accounts/aws.py), so the scan must not assume every
        # item is a token. Filter server-side by the PK prefix (true for
        # every token item ever written, including pre-``entity_type``
        # launch records) and re-check client-side for fakes/scans that
        # don't evaluate ``FilterExpression`` — same belt-and-braces as
        # :meth:`revoke`.
        scan_kwargs: dict[str, Any] = {"FilterExpression": Attr("PK").begins_with("TOKEN#")}
        while True:
            response = self._table.scan(**scan_kwargs)
            records.extend(
                _from_item(item)
                for item in response.get("Items", [])
                if str(item.get("PK", "")).startswith("TOKEN#")
            )
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                return records
            scan_kwargs["ExclusiveStartKey"] = last_key

    def revoke(self, token_id: str) -> None:
        """Mark the token identified by ``token_id`` as revoked.

        Per the module docstring's *Revocation design* note, this is the
        deliberate scan-then-flag design (chosen over a tombstone or a GSI for
        least migration risk): a paginated full-table scan filtered to
        ``token_id`` followed by a targeted ``update_item`` that flips the
        item's existing ``revoked`` attribute to ``True`` — O(n) in the number
        of issued tokens. Flipping the flag (rather than deleting the item)
        makes :func:`~league_site.auth.tokens.verify` fail naturally, with no
        change to the request path, while keeping the record countable by the
        self-serve issuance guard. The O(n) scan is an accepted, documented
        tradeoff at launch scale (agent token counts are small; see
        :mod:`league_site.capacity.config` for the same reasoning applied to
        matches). Recommendation for a follow-up: add a GSI keyed on
        ``token_id`` (``GSI1PK=TOKEN_ID#<id>``) once issued-token volume makes
        an O(n) scan noticeable, and swap this for a ``Query``.

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

    def set_blocked(self, token_id: str, blocked: bool) -> None:
        """Flip the operator ``blocked`` kill-switch on the token ``token_id``.

        The same deliberate scan-then-flip shape as :meth:`revoke` (see that
        method and the *Revocation design* note above): a paginated,
        ``token_id``-filtered full-table scan to locate the item, then a
        single targeted ``update_item`` that sets its ``blocked`` attribute —
        the one DynamoDB write the auth path reads on the very next
        :func:`league_site.auth.tokens.verify`, with no cache in between. O(n)
        in issued tokens at launch scale; the same GSI-on-``token_id`` follow-up
        recommended for :meth:`revoke` retires the scan here too.

        Raises :class:`~league_site.auth.token_store.TokenNotFoundError` if no
        record has that ``token_id`` — the scan exhausts every page unmatched.
        """
        from boto3.dynamodb.conditions import Attr

        scan_kwargs: dict[str, Any] = {
            "ProjectionExpression": "PK, SK, token_id",
            "FilterExpression": Attr("token_id").eq(token_id),
        }
        while True:
            response = self._table.scan(**scan_kwargs)
            for item in response.get("Items", []):
                if item.get("token_id") == token_id:
                    self._table.update_item(
                        Key={"PK": item["PK"], "SK": item["SK"]},
                        UpdateExpression="SET blocked = :blocked",
                        ExpressionAttributeValues={":blocked": blocked},
                    )
                    return
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        raise TokenNotFoundError(token_id)
