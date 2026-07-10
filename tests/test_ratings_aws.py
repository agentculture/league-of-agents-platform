"""Tests for the DynamoDB-backed rating ledger in league_site.ratings.aws.

Every test injects a fake resource so nothing here ever touches real AWS,
needs credentials, or needs a region configured — the ``aws`` extra
(``boto3``) only needs to be importable, which `uv sync --extra aws`
guarantees for this suite. The fake mirrors real DynamoDB's one observable
data quirk: numbers round-trip as ``Decimal``, so these tests prove the
adapter coerces every rating/delta back to plain ``int`` (the ledger's
integer-only invariant — see ``tests/test_ratings_ledger.py``).
"""

from __future__ import annotations

import re
from decimal import Decimal
from typing import Any

import pytest

from league_site.matches import ParticipantKind
from league_site.ratings import (
    IdentityRating,
    InMemoryRatingLedgerStore,
    IntegerEloRatingSystem,
    MatchOutcome,
    OutcomeEntry,
    RatingIdentity,
    RatingLedgerStore,
)
from league_site.ratings.aws import DynamoDBRatingLedgerStore

HUMAN = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Ada")
AGENT = RatingIdentity(
    kind=ParticipantKind.AGENT,
    display_name="Sonnet",
    model="claude-sonnet-5",
    provider="anthropic",
)
THIRD = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Zed")


def _outcome(match_id: str, *pairs: tuple[RatingIdentity, int]) -> MatchOutcome:
    return MatchOutcome(
        match_id=match_id,
        entries=tuple(OutcomeEntry(identity=identity, score=score) for identity, score in pairs),
    )


#: Scripted sequences (mirroring tests/test_ratings_ledger.py) replayed
#: against both the in-memory reference store and the DynamoDB adapter to
#: prove the two implementations agree entry for entry.
SEQUENCES: list[list[MatchOutcome]] = [
    [
        _outcome("m1", (HUMAN, 10), (AGENT, 3)),
        _outcome("m2", (AGENT, 8), (HUMAN, 8)),
        _outcome("m3", (HUMAN, 1), (AGENT, 9)),
    ],
    [
        _outcome("m1", (AGENT, 1), (HUMAN, 1)),
        _outcome("m2", (AGENT, 5), (HUMAN, 2)),
    ],
    [
        _outcome("m1", (THIRD, 1), (AGENT, 9), (HUMAN, 5)),
        _outcome("m2", (HUMAN, 4), (THIRD, 4), (AGENT, 4)),
        _outcome("m3", (AGENT, 2), (HUMAN, 9), (THIRD, 1)),
    ],
]


class FakeConditionalCheckFailed(Exception):
    """Raised by the fake when a conditional ``put_item`` addresses an existing item."""


def _to_dynamo(value: Any) -> Any:
    """Deep-convert Python ints to ``Decimal``, as boto3's resource layer stores them."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, dict):
        return {key: _to_dynamo(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(item) for item in value]
    return value


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table resource: an in-process dict.

    Mimics just enough of the real ``Table`` contract to exercise the
    adapter: ``put_item`` honours a ``ConditionExpression`` as
    "item-must-not-already-exist" (raising
    :class:`FakeConditionalCheckFailed` on an attempted overwrite — the
    append-only guarantee), ``query`` resolves a single ``Key("PK").eq(...)``
    condition (via the condition object's own ``get_expression()``), orders
    by ``SK``, and paginates via ``LastEvaluatedKey``/``ExclusiveStartKey``
    at :attr:`query_page_size` items per call, and ``update_item`` applies an
    atomic ``ADD`` counter update. Numbers are stored as ``Decimal`` (see
    :func:`_to_dynamo`), exactly as real DynamoDB returns them.
    """

    #: Small on purpose so a handful of entries already forces >1 page.
    query_page_size = 2

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}
        self.put_calls: list[dict[str, Any]] = []

    def put_item(
        self, *, Item: dict[str, Any], ConditionExpression: Any = None
    ) -> None:  # noqa: N803 - matches boto3's kwarg casing
        key = (Item["PK"], Item["SK"])
        self.put_calls.append({"Item": Item, "ConditionExpression": ConditionExpression})
        if ConditionExpression is not None and key in self.items:
            raise FakeConditionalCheckFailed(f"item already exists: {key}")
        self.items[key] = _to_dynamo(Item)

    def query(self, **kwargs: Any) -> dict[str, Any]:  # noqa: N803
        expression = kwargs["KeyConditionExpression"].get_expression()
        assert expression["operator"] == "="
        assert expression["values"][0].name == "PK"
        partition = expression["values"][1]
        matching = [item for item in self.items.values() if item["PK"] == partition]
        matching.sort(key=lambda item: str(item["SK"]))
        start_key: Any = kwargs.get("ExclusiveStartKey")
        start_index = 0
        if start_key is not None:
            needle = (start_key["PK"], start_key["SK"])
            for i, item in enumerate(matching):
                if (item["PK"], item["SK"]) == needle:
                    start_index = i + 1
                    break
        page = matching[start_index : start_index + self.query_page_size]
        response: dict[str, Any] = {"Items": page}
        if start_index + self.query_page_size < len(matching):
            last = page[-1]
            response["LastEvaluatedKey"] = {"PK": last["PK"], "SK": last["SK"]}
        return response

    def update_item(
        self,
        *,
        Key: dict[str, str],  # noqa: N803
        UpdateExpression: str,  # noqa: N803
        ExpressionAttributeValues: dict[str, Any],  # noqa: N803
        ReturnValues: str = "NONE",  # noqa: N803
    ) -> dict[str, Any]:
        parsed = re.fullmatch(r"ADD (\w+) (:\w+)", UpdateExpression)
        assert parsed is not None, f"fake only supports ADD updates, got {UpdateExpression!r}"
        attr, placeholder = parsed.group(1), parsed.group(2)
        key = (Key["PK"], Key["SK"])
        item = self.items.setdefault(key, {"PK": Key["PK"], "SK": Key["SK"]})
        item[attr] = Decimal(item.get(attr, 0)) + Decimal(ExpressionAttributeValues[placeholder])
        if ReturnValues == "UPDATED_NEW":
            return {"Attributes": {attr: item[attr]}}
        return {}


class FakeDynamoDBResource:
    def __init__(self) -> None:
        self.table = FakeTable()

    def Table(self, name: str) -> FakeTable:  # noqa: N802 - matches boto3's method casing
        return self.table


def _fake_store() -> tuple[DynamoDBRatingLedgerStore, FakeDynamoDBResource]:
    resource = FakeDynamoDBResource()
    return DynamoDBRatingLedgerStore("league-ratings", resource=resource), resource


def _snapshot(store: RatingLedgerStore) -> dict[RatingIdentity, IdentityRating]:
    return {identity: store.get(identity) for identity in store.all_identities()}


def test_module_imports_and_boto3_is_available_via_the_aws_extra() -> None:
    import league_site.ratings.aws as ratings_aws_module

    assert ratings_aws_module.boto3 is not None


def test_get_unknown_identity_returns_initial_rating_with_empty_history() -> None:
    store, _ = _fake_store()
    assert store.get(HUMAN) == IdentityRating.initial(HUMAN)


def test_record_match_round_trips_rating_match_count_and_history() -> None:
    store, _ = _fake_store()
    system = IntegerEloRatingSystem(k_factor=32)

    applied = store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), system)

    standing = store.get(HUMAN)
    assert standing.match_count == 1
    assert standing.rating == 1500 + applied[HUMAN].delta
    assert standing.history == (applied[HUMAN],)
    assert applied[HUMAN].match_id == "m1"
    assert applied[HUMAN].resulting_rating == standing.rating


@pytest.mark.parametrize("sequence", SEQUENCES, ids=["two-player", "short", "multiway"])
def test_dynamodb_ledger_agrees_with_the_in_memory_reference_store(
    sequence: list[MatchOutcome],
) -> None:
    """Replaying a sequence through both stores yields identical ledgers."""
    system = IntegerEloRatingSystem(k_factor=32)
    reference = InMemoryRatingLedgerStore()
    dynamo, _ = _fake_store()
    for outcome in sequence:
        reference_applied = reference.record_match(outcome, system)
        dynamo_applied = dynamo.record_match(outcome, system)
        assert dynamo_applied == reference_applied

    assert dynamo.all_identities() == reference.all_identities()
    assert _snapshot(dynamo) == _snapshot(reference)


def test_all_identities_reflects_recorded_participants_in_first_seen_order() -> None:
    store, _ = _fake_store()
    system = IntegerEloRatingSystem(k_factor=32)
    assert store.all_identities() == []

    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), system)
    assert store.all_identities() == [HUMAN, AGENT]

    store.record_match(_outcome("m2", (THIRD, 1), (AGENT, 1)), system)
    assert store.all_identities() == [HUMAN, AGENT, THIRD]


def test_history_follows_last_evaluated_key_across_query_pages() -> None:
    """FakeTable.query_page_size (2) is smaller than the 5 entries below:
    ``get`` only returns the full history if it follows ``LastEvaluatedKey``
    across pages rather than reading just the first."""
    store, resource = _fake_store()
    system = IntegerEloRatingSystem(k_factor=32)
    for i in range(5):
        winner, loser = (HUMAN, AGENT) if i % 2 == 0 else (AGENT, HUMAN)
        store.record_match(_outcome(f"m{i}", (winner, 1), (loser, 0)), system)
    assert resource.table.query_page_size < 5

    standing = store.get(HUMAN)
    assert standing.match_count == 5
    assert len(standing.history) == 5
    # each entry's resulting_rating chains from the previous one
    rating = 1500
    for entry in standing.history:
        rating += entry.delta
        assert entry.resulting_rating == rating
    assert standing.rating == rating


def test_every_ledger_write_is_a_conditional_append() -> None:
    """No write may ever overwrite an existing item: every ``put_item`` the
    adapter issues carries a ``ConditionExpression``, and the entries written
    for an earlier match are byte-identical after a later one is recorded."""
    store, resource = _fake_store()
    system = IntegerEloRatingSystem(k_factor=32)

    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), system)
    first_match_items = {
        key: dict(item) for key, item in resource.table.items.items() if key[1] != "COUNTER"
    }
    store.record_match(_outcome("m2", (HUMAN, 2), (AGENT, 9)), system)

    assert resource.table.put_calls
    assert all(call["ConditionExpression"] is not None for call in resource.table.put_calls)
    for key, item in first_match_items.items():
        assert resource.table.items[key] == item


def test_fake_conditional_put_rejects_overwrites() -> None:
    """Sanity check: the append-only assertion above isn't vacuous — the fake
    really does reject a conditional put that addresses an existing item."""
    table = FakeTable()
    table.put_item(Item={"PK": "LEDGER#x", "SK": "ENTRY#00000001"}, ConditionExpression=object())
    with pytest.raises(FakeConditionalCheckFailed):
        table.put_item(
            Item={"PK": "LEDGER#x", "SK": "ENTRY#00000001"}, ConditionExpression=object()
        )


def test_ratings_and_deltas_survive_dynamodbs_decimal_round_trip_as_ints() -> None:
    """Real DynamoDB hands numbers back as ``Decimal``; the adapter must
    coerce them to plain ``int`` so the ledger's integer-only invariant
    holds (``Decimal(5) == 5`` is ``True``, so equality tests alone would
    never catch a leak)."""
    store, _ = _fake_store()
    system = IntegerEloRatingSystem(k_factor=32)
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), system)

    for identity in store.all_identities():
        standing = store.get(identity)
        assert type(standing.rating) is int
        assert type(standing.match_count) is int
        for entry in standing.history:
            assert type(entry.delta) is int
            assert type(entry.resulting_rating) is int


def test_identities_with_the_same_display_name_but_different_models_stay_distinct() -> None:
    store, _ = _fake_store()
    system = IntegerEloRatingSystem(k_factor=32)
    other_agent = RatingIdentity(
        kind=ParticipantKind.AGENT,
        display_name="Sonnet",
        model="claude-opus-5",
        provider="anthropic",
    )
    store.record_match(_outcome("m1", (AGENT, 1), (other_agent, 0)), system)

    assert store.all_identities() == [AGENT, other_agent]
    assert store.get(AGENT).history != store.get(other_agent).history


def test_require_boto3_raises_runtime_error_when_boto3_is_unavailable(monkeypatch) -> None:
    import league_site.ratings.aws as ratings_aws_module

    monkeypatch.setattr(ratings_aws_module, "boto3", None)
    monkeypatch.setattr(ratings_aws_module, "_IMPORT_ERROR", ImportError("no boto3"))

    with pytest.raises(RuntimeError, match="aws"):
        DynamoDBRatingLedgerStore("league-ratings", resource=FakeDynamoDBResource())
