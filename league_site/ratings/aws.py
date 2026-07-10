"""DynamoDB-backed append-only rating ledger.

This is the only module in :mod:`league_site.ratings` that imports
``boto3``. The import is guarded: ``boto3`` ships behind this project's
``aws`` extra (``uv sync --extra aws``), and nothing in
:mod:`league_site.ratings.ledger` or its test suite should require it —
importing this module without ``boto3`` installed raises a clear
``RuntimeError`` only when the adapter is actually *instantiated*, not
merely imported (mirrors :mod:`league_site.matches.aws` and
:mod:`league_site.auth.aws_tokens` — read those first if you're wiring up
another adapter here). It is deliberately *not* re-exported from
:mod:`league_site.ratings`'s ``__init__`` for the same reason those two
adapters aren't re-exported from their packages: importing the package must
never pay (or risk) the ``boto3`` import.

Accepts a pre-built ``resource`` so callers (and tests) can inject a fake
and never touch real AWS — no credentials or region configuration are
required to exercise this module.

DynamoDB single-table design
-----------------------------
One table, keyed by a generic ``PK``/``SK`` pair, three item shapes::

    PK                       SK               Attributes
    LEDGER#<identity-key>    ENTRY#<seq>      entity_type="rating_entry", seq,
                                              match_id, delta, resulting_rating
    IDENTITIES               ORDER#<n>        entity_type="rating_identity",
                                              kind, display_name, model, provider
    IDENTITIES               COUNTER          next_order

``<identity-key>`` is a compact JSON encoding of the
:class:`~league_site.ratings.system.RatingIdentity` fields (see
:func:`_identity_key`) — JSON rather than a delimiter-joined string so a
display name containing any delimiter character can never collide with a
different identity. ``<seq>``/``<n>`` are zero-padded to
:data:`_SEQ_WIDTH` digits so DynamoDB's lexicographic ``SK`` ordering *is*
numeric ordering.

The append-only contract (:class:`~league_site.ratings.ledger.
RatingLedgerStore`: "never mutates a past entry") is enforced at the
storage layer, not just by convention: every ``put_item`` this adapter
issues carries an ``attribute_not_exists`` ``ConditionExpression``, so an
attempt to rewrite an existing entry fails loudly at DynamoDB rather than
silently rewriting history.

``all_identities``'s insertion-order contract is kept via the ``COUNTER``
item: the first time an identity is recorded, an atomic ``ADD`` update
assigns it the next order number and an ``ORDER#<n>`` marker item is
written under the single ``IDENTITIES`` partition, so listing identities is
one ordered ``Query`` — no scan. (Two *concurrent* first-time recordings of
the same identity could each write a marker; :meth:`DynamoDBRatingLedgerStore.
all_identities` dedupes on read, first marker wins — the same
last-writer-independence the in-memory store's plain ``dict`` gives.)

Numbers round-trip through DynamoDB as ``Decimal``; every read path here
coerces them back to plain ``int`` so the ledger's integer-only invariant
(see :mod:`league_site.ratings.system`) survives persistence.
"""

from __future__ import annotations

import json
from typing import Any

try:
    import boto3
except ImportError as exc:  # pragma: no cover - exercised only without the aws extra
    boto3 = None  # type: ignore[assignment]
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None

from league_site.matches.models import ParticipantKind
from league_site.ratings.ledger import IdentityRating, RatingEntry, RatingLedgerStore
from league_site.ratings.system import MatchOutcome, RatingIdentity, RatingSystem

_IDENTITIES_PK = "IDENTITIES"
_COUNTER_SK = "COUNTER"
_ENTRY_SK_PREFIX = "ENTRY#"
_ORDER_SK_PREFIX = "ORDER#"
#: Zero-pad width for entry sequence numbers and identity order numbers —
#: what makes lexicographic ``SK`` order equal numeric order.
_SEQ_WIDTH = 8


def _require_boto3() -> None:
    if boto3 is None:
        raise RuntimeError(
            "boto3 is required for league_site.ratings.aws adapters; "
            "install it with `uv sync --extra aws`"
        ) from _IMPORT_ERROR


def _identity_key(identity: RatingIdentity) -> str:
    """Collision-free string key for *identity* (see the module docstring)."""
    return json.dumps(
        [identity.kind.value, identity.display_name, identity.model, identity.provider],
        separators=(",", ":"),
    )


def _ledger_pk(identity: RatingIdentity) -> str:
    return f"LEDGER#{_identity_key(identity)}"


def _entry_item(identity: RatingIdentity, seq: int, entry: RatingEntry) -> dict[str, Any]:
    return {
        "PK": _ledger_pk(identity),
        "SK": f"{_ENTRY_SK_PREFIX}{seq:0{_SEQ_WIDTH}d}",
        "entity_type": "rating_entry",
        "seq": seq,
        "match_id": entry.match_id,
        "delta": entry.delta,
        "resulting_rating": entry.resulting_rating,
    }


def _entry_from_item(item: dict[str, Any]) -> RatingEntry:
    return RatingEntry(
        match_id=item["match_id"],
        delta=int(item["delta"]),
        resulting_rating=int(item["resulting_rating"]),
    )


def _identity_item(identity: RatingIdentity, order: int) -> dict[str, Any]:
    return {
        "PK": _IDENTITIES_PK,
        "SK": f"{_ORDER_SK_PREFIX}{order:0{_SEQ_WIDTH}d}",
        "entity_type": "rating_identity",
        "kind": identity.kind.value,
        "display_name": identity.display_name,
        "model": identity.model,
        "provider": identity.provider,
    }


def _identity_from_item(item: dict[str, Any]) -> RatingIdentity:
    return RatingIdentity(
        kind=ParticipantKind(item["kind"]),
        display_name=item["display_name"],
        model=item.get("model"),
        provider=item.get("provider"),
    )


class DynamoDBRatingLedgerStore(RatingLedgerStore):
    """``RatingLedgerStore`` backed by a single DynamoDB table.

    See the module docstring for the item shapes this class reads and
    writes and how the append-only + insertion-order contracts map onto
    them. Pass ``resource`` to inject a fake (or a pre-configured)
    ``boto3`` DynamoDB resource; otherwise one is built lazily from the
    default AWS config.
    """

    def __init__(self, table_name: str, *, resource: Any | None = None) -> None:
        _require_boto3()
        self._table_name = table_name
        self._resource = resource if resource is not None else boto3.resource("dynamodb")
        self._table = self._resource.Table(table_name)

    def _query_partition(self, partition_key: str) -> list[dict[str, Any]]:
        """All items under ``PK == partition_key``, following every page."""
        from boto3.dynamodb.conditions import Key

        items: list[dict[str, Any]] = []
        query_kwargs: dict[str, Any] = {"KeyConditionExpression": Key("PK").eq(partition_key)}
        while True:
            response = self._table.query(**query_kwargs)
            items.extend(response.get("Items", []))
            last_key = response.get("LastEvaluatedKey")
            if not last_key:
                break
            query_kwargs["ExclusiveStartKey"] = last_key
        return items

    def get(self, identity: RatingIdentity) -> IdentityRating:
        entry_items = [
            item
            for item in self._query_partition(_ledger_pk(identity))
            if str(item.get("SK", "")).startswith(_ENTRY_SK_PREFIX)
        ]
        if not entry_items:
            return IdentityRating.initial(identity)
        entry_items.sort(key=lambda item: int(item["seq"]))
        history = tuple(_entry_from_item(item) for item in entry_items)
        return IdentityRating(
            identity=identity,
            rating=history[-1].resulting_rating,
            match_count=len(history),
            history=history,
        )

    def record_match(
        self, outcome: MatchOutcome, rating_system: RatingSystem
    ) -> dict[RatingIdentity, RatingEntry]:
        from boto3.dynamodb.conditions import Attr

        # dict.fromkeys dedupes while preserving first-seen order — the same
        # order-stable-by-construction discipline as the in-memory store
        # (see InMemoryRatingLedgerStore.record_match), which is what makes
        # the two implementations replay-identical.
        involved = list(dict.fromkeys(entry.identity for entry in outcome.entries))
        priors = {identity: self.get(identity) for identity in involved}
        current_ratings = {identity: prior.rating for identity, prior in priors.items()}
        deltas = rating_system.compute_deltas(current_ratings, outcome)

        applied: dict[RatingIdentity, RatingEntry] = {}
        for identity in involved:
            prior = priors[identity]
            delta = deltas[identity]
            entry = RatingEntry(
                match_id=outcome.match_id,
                delta=delta,
                resulting_rating=prior.rating + delta,
            )
            self._table.put_item(
                Item=_entry_item(identity, prior.match_count + 1, entry),
                # append-only, enforced at the storage layer: never rewrite
                # an existing entry (see the module docstring)
                ConditionExpression=Attr("PK").not_exists(),
            )
            if prior.match_count == 0:
                self._register_identity(identity)
            applied[identity] = entry
        return applied

    def _register_identity(self, identity: RatingIdentity) -> None:
        """Assign *identity* the next insertion-order number and write its marker."""
        from boto3.dynamodb.conditions import Attr

        response = self._table.update_item(
            Key={"PK": _IDENTITIES_PK, "SK": _COUNTER_SK},
            UpdateExpression="ADD next_order :one",
            ExpressionAttributeValues={":one": 1},
            ReturnValues="UPDATED_NEW",
        )
        order = int(response["Attributes"]["next_order"])
        self._table.put_item(
            Item=_identity_item(identity, order),
            ConditionExpression=Attr("PK").not_exists(),
        )

    def all_identities(self) -> list[RatingIdentity]:
        marker_items = [
            item
            for item in self._query_partition(_IDENTITIES_PK)
            if str(item.get("SK", "")).startswith(_ORDER_SK_PREFIX)
        ]
        marker_items.sort(key=lambda item: str(item["SK"]))
        identities: list[RatingIdentity] = []
        seen: set[RatingIdentity] = set()
        for item in marker_items:
            identity = _identity_from_item(item)
            if identity not in seen:
                seen.add(identity)
                identities.append(identity)
        return identities
