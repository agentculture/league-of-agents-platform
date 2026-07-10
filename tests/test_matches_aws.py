"""Tests for the DynamoDB/S3 adapter skeleton in league_site.matches.aws.

Every test injects a fake resource/client so nothing here ever touches real
AWS, needs credentials, or needs a region configured — the ``aws`` extra
(``boto3``) only needs to be importable, which `uv sync --extra aws`
guarantees for this suite.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from league_site.matches import Match, MatchNotFoundError, MatchStatus
from league_site.matches.aws import BY_STATUS_UPDATED_INDEX, DynamoDBMatchStore, S3MatchArchive
from league_site.matches.serialization import archive_key, to_archive_dict, to_item
from tests._matches_support import CounterGameEngine, make_participants


def _mid_game_match(match_id: str = "m-aws") -> Match:
    human, agent = make_participants()
    engine = CounterGameEngine(target=100, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 3})
    return match


def _match_in_status(match_id: str, status: MatchStatus) -> Match:
    """A match driven (via real transitions) into *status*."""
    human, agent = make_participants()
    engine = CounterGameEngine(target=5, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    if status is MatchStatus.CREATED:
        return match
    match.start(engine)
    if status is MatchStatus.PAUSED:
        match.pause()
    elif status is MatchStatus.COMPLETED:
        match.take_turn(engine, human.participant_id, {"delta": 5})
        match.complete(engine)
    assert match.status is status
    return match


def _queried_statuses(table: "FakeTable") -> set[str]:
    """The set of ``status`` partition values the store queried the GSI for."""
    statuses = set()
    for kwargs in table.query_calls:
        condition: Any = kwargs["KeyConditionExpression"]
        expression = condition.get_expression()
        assert expression["operator"] == "="
        assert expression["values"][0].name == "status"
        statuses.add(expression["values"][1])
    return statuses


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table resource: an in-process dict.

    :meth:`query` mimics just enough of the real ``Table.query`` contract to
    exercise ``DynamoDBMatchStore.list_ids``'s GSI loop: it resolves a
    single ``Key(...).eq(...)`` ``KeyConditionExpression`` (via the condition
    object's own ``get_expression()``), filters the stored items on that
    attribute (which is exactly what querying a ``status``-partitioned GSI
    returns), orders by ``updated_at`` (the GSI range key), and hands back at
    most :attr:`query_page_size` items per call plus a ``LastEvaluatedKey``
    whenever more remain — the shape a real paginated query loop must
    round-trip back in as ``ExclusiveStartKey`` on the next call. It doesn't
    evaluate ``ProjectionExpression`` (returning extra attributes is
    harmless) — the point of this fake is proving the per-status pagination
    loop, not reimplementing DynamoDB's expression language. Every call is
    recorded in :attr:`query_calls` (and ``scan`` calls counted in
    :attr:`scan_calls`) so tests can assert *which* index — and never a
    scan — served ``list_ids``.
    """

    #: Small on purpose so a handful of saved items already forces >1 page.
    query_page_size = 2

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}
        self.query_calls: list[dict[str, Any]] = []
        self.scan_calls = 0

    def put_item(
        self, *, Item: dict[str, object]
    ) -> None:  # noqa: N803 - matches boto3's kwarg casing
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(self, *, Key: dict[str, str]) -> dict[str, object]:  # noqa: N803
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item is not None else {}

    def delete_item(self, *, Key: dict[str, str]) -> None:  # noqa: N803
        self.items.pop((Key["PK"], Key["SK"]), None)

    def query(self, **kwargs: Any) -> dict[str, object]:
        self.query_calls.append(kwargs)
        expression = kwargs["KeyConditionExpression"].get_expression()
        assert expression["operator"] == "="
        key_name = expression["values"][0].name
        key_value = expression["values"][1]
        matching = [item for item in self.items.values() if item.get(key_name) == key_value]
        matching.sort(key=lambda item: str(item.get("updated_at", "")))
        start_key: Any = kwargs.get("ExclusiveStartKey")
        start_index = 0
        if start_key is not None:
            needle = (start_key["PK"], start_key["SK"])
            for i, item in enumerate(matching):
                if (item["PK"], item["SK"]) == needle:
                    start_index = i + 1
                    break
        page = matching[start_index : start_index + self.query_page_size]
        response: dict[str, object] = {"Items": page}
        if start_index + self.query_page_size < len(matching):
            last = page[-1]
            response["LastEvaluatedKey"] = {"PK": last["PK"], "SK": last["SK"]}
        return response

    def scan(self, **kwargs: object) -> dict[str, object]:
        self.scan_calls += 1
        return {"Items": list(self.items.values())}


class FakeDynamoDBResource:
    def __init__(self) -> None:
        self.table = FakeTable()

    def Table(self, name: str) -> FakeTable:  # noqa: N802 - matches boto3's method casing
        return self.table


class FakeS3Body:
    """Stand-in for a boto3 StreamingBody: exposes the ``.read()`` the adapter uses."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """Stand-in for a boto3 S3 client: an in-process dict of put objects."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str
    ) -> None:  # noqa: N803
        assert ContentType == "application/json"
        self.objects[Key] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, FakeS3Body]:  # noqa: N803
        return {"Body": FakeS3Body(self.objects[Key])}


def test_module_imports_and_boto3_is_available_via_the_aws_extra() -> None:
    import league_site.matches.aws as aws_module

    assert aws_module.boto3 is not None


def test_dynamodb_match_store_save_and_load_round_trip_via_fake_resource() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBMatchStore("league-matches", resource=resource)
    match = _mid_game_match()

    store.save(match)
    loaded = store.load(match.match_id)

    assert loaded == match
    # And the fake table really did receive the documented single-table item shape.
    stored_item = resource.table.items[("MATCH#m-aws", "METADATA")]
    assert stored_item == to_item(match)


def test_dynamodb_match_store_load_missing_raises_match_not_found() -> None:
    store = DynamoDBMatchStore("league-matches", resource=FakeDynamoDBResource())
    with pytest.raises(MatchNotFoundError):
        store.load("does-not-exist")


def test_dynamodb_match_store_delete_removes_item() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBMatchStore("league-matches", resource=resource)
    match = _mid_game_match()
    store.save(match)

    store.delete(match.match_id)

    with pytest.raises(MatchNotFoundError):
        store.load(match.match_id)


def test_dynamodb_match_store_list_ids_on_an_empty_table_returns_an_empty_list() -> None:
    store = DynamoDBMatchStore("league-matches", resource=FakeDynamoDBResource())
    assert store.list_ids() == []


def test_dynamodb_match_store_list_ids_queries_the_by_status_updated_gsi_never_a_scan() -> None:
    """``list_ids`` is served entirely by the ``by-status-updated`` GSI.

    The index name must be exactly ``by-status-updated`` — the CloudFormation
    template provisions the GSI under that name, so any drift here breaks the
    deployed store even though every fake-backed test still passes.
    """
    resource = FakeDynamoDBResource()
    store = DynamoDBMatchStore("league-matches", resource=resource)
    store.save(_mid_game_match("m-1"))

    assert store.list_ids() == ["m-1"]
    assert resource.table.scan_calls == 0
    assert resource.table.query_calls
    index_names = {kwargs["IndexName"] for kwargs in resource.table.query_calls}
    assert index_names == {BY_STATUS_UPDATED_INDEX}
    assert BY_STATUS_UPDATED_INDEX == "by-status-updated"


def test_dynamodb_match_store_list_ids_covers_every_status_partition() -> None:
    """The GSI partitions on ``status``: one query per status covers the table."""
    resource = FakeDynamoDBResource()
    store = DynamoDBMatchStore("league-matches", resource=resource)
    saved = {}
    for status in MatchStatus:
        match = _match_in_status(f"m-{status.value}", status)
        store.save(match)
        saved[match.match_id] = status

    assert sorted(store.list_ids()) == sorted(saved)
    assert _queried_statuses(resource.table) == {status.value for status in MatchStatus}


def test_dynamodb_match_store_list_ids_returns_every_saved_match_id_across_pages() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBMatchStore("league-matches", resource=resource)
    match_ids = [f"m-{i}" for i in range(5)]
    for match_id in match_ids:
        store.save(_mid_game_match(match_id))
    # FakeTable.query_page_size (2) is smaller than 5 items in the single
    # "active" partition: this only passes if list_ids() actually follows
    # LastEvaluatedKey across multiple pages rather than reading just the
    # first page.
    assert resource.table.query_page_size < len(match_ids)

    assert sorted(store.list_ids()) == sorted(match_ids)


def test_dynamodb_match_store_list_ids_round_trips_last_evaluated_key() -> None:
    resource = FakeDynamoDBResource()
    store = DynamoDBMatchStore("league-matches", resource=resource)
    for match_id in ("m-a", "m-b", "m-c"):
        store.save(_mid_game_match(match_id))

    store.list_ids()

    # All three saved matches are mid-game ("active"), so that partition
    # takes two query pages: the first call per partition has no start key;
    # every follow-up call is seeded from the prior response's
    # LastEvaluatedKey (never re-queried from the beginning).
    active_calls = [
        kwargs
        for kwargs in resource.table.query_calls
        if kwargs["KeyConditionExpression"].get_expression()["values"][1]
        == MatchStatus.ACTIVE.value
    ]
    assert len(active_calls) == 2
    assert "ExclusiveStartKey" not in active_calls[0]
    assert active_calls[1]["ExclusiveStartKey"] is not None


def test_s3_match_archive_writes_the_documented_key_and_body() -> None:
    client = FakeS3Client()
    archive = S3MatchArchive("league-archives", client=client)
    match = _mid_game_match()

    key = archive.archive(match)

    assert key == archive_key(match)
    assert key in client.objects
    assert json.loads(client.objects[key]) == to_archive_dict(match)


def test_s3_match_archive_retrieve_round_trips() -> None:
    client = FakeS3Client()
    archive = S3MatchArchive("league-archives", client=client)
    match = _mid_game_match()
    archive.archive(match)

    restored = archive.retrieve(match.match_id, year=match.created_at.year)

    assert restored == match


def test_require_boto3_raises_runtime_error_when_boto3_is_unavailable(monkeypatch) -> None:
    import league_site.matches.aws as aws_module

    monkeypatch.setattr(aws_module, "boto3", None)
    monkeypatch.setattr(aws_module, "_IMPORT_ERROR", ImportError("no boto3"))

    with pytest.raises(RuntimeError, match="aws"):
        DynamoDBMatchStore("league-matches", resource=FakeDynamoDBResource())

    with pytest.raises(RuntimeError, match="aws"):
        S3MatchArchive("league-archives", client=FakeS3Client())
