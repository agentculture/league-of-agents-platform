"""Tests for the DynamoDB/S3 adapter skeleton in league_site.matches.aws.

Every test injects a fake resource/client so nothing here ever touches real
AWS, needs credentials, or needs a region configured — the ``aws`` extra
(``boto3``) only needs to be importable, which `uv sync --extra aws`
guarantees for this suite.
"""

from __future__ import annotations

import json

import pytest

from league_site.matches import Match, MatchNotFoundError
from league_site.matches.aws import DynamoDBMatchStore, S3MatchArchive
from league_site.matches.serialization import archive_key, to_archive_dict, to_item
from tests._matches_support import CounterGameEngine, make_participants


def _mid_game_match(match_id: str = "m-aws") -> Match:
    human, agent = make_participants()
    engine = CounterGameEngine(target=100, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 3})
    return match


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table resource: an in-process dict."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}

    def put_item(
        self, *, Item: dict[str, object]
    ) -> None:  # noqa: N803 - matches boto3's kwarg casing
        self.items[(Item["PK"], Item["SK"])] = Item

    def get_item(self, *, Key: dict[str, str]) -> dict[str, object]:  # noqa: N803
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item is not None else {}

    def delete_item(self, *, Key: dict[str, str]) -> None:  # noqa: N803
        self.items.pop((Key["PK"], Key["SK"]), None)


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


def test_dynamodb_match_store_list_ids_is_not_wired_up_yet() -> None:
    store = DynamoDBMatchStore("league-matches", resource=FakeDynamoDBResource())
    with pytest.raises(NotImplementedError):
        store.list_ids()


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
