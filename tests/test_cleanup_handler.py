"""Tests for :func:`league_site.aws_lambda.cleanup.run_cleanup` (the core sweep).

Every dependency is a fake/in-memory object — no real AWS anywhere in this
module. Covers h5's "the scheduled job archives or deletes stale match
state and its price logic is documented" acceptance criterion: seeded
hot-stale, archive-stale, and overflow matches produce the correct
archive/delete actions in both dry-run (reported, not executed) and real
(executed against fakes) mode.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from league_site.aws_lambda.cleanup import run_cleanup
from league_site.capacity.config import CapacityConfig
from league_site.matches import InMemoryMatchStore, Match
from league_site.matches.aws import S3MatchArchive
from league_site.matches.serialization import archive_key
from tests._matches_support import CounterGameEngine, make_participants

_NOW = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


class FakeS3Body:
    """Stand-in for a boto3 StreamingBody: exposes the ``.read()`` the adapter uses."""

    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    """Stand-in for a boto3 S3 client: an in-process dict with list/delete support.

    Extends the ``put_object``/``get_object`` pattern from
    ``tests/test_matches_aws.py``'s ``FakeS3Client`` with
    ``list_objects_v2``/``delete_object`` so ``cleanup.py``'s archive-aging
    pass is exercised without real AWS. Paginates two keys at a time so the
    continuation-token loop in ``_delete_aged_archives`` gets real exercise.
    """

    _PAGE_SIZE = 2

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.last_modified: dict[str, datetime] = {}

    def put_object(
        self, *, Bucket: str, Key: str, Body: bytes, ContentType: str
    ) -> None:  # noqa: N803
        assert ContentType == "application/json"
        self.objects[Key] = Body
        self.last_modified.setdefault(Key, _NOW)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, FakeS3Body]:  # noqa: N803
        return {"Body": FakeS3Body(self.objects[Key])}

    def delete_object(self, *, Bucket: str, Key: str) -> None:  # noqa: N803
        self.objects.pop(Key, None)
        self.last_modified.pop(Key, None)

    def list_objects_v2(
        self, *, Bucket: str, Prefix: str = "", ContinuationToken: str | None = None
    ) -> dict[str, object]:  # noqa: N803
        # Key-based (not index-based) continuation, mirroring real S3's
        # lexicographic resume-after-key semantics: stable even when a
        # caller deletes already-returned objects between pages (as
        # cleanup.py's own aged-archive pass does), unlike a positional
        # index into a list that shrinks as objects are deleted mid-scan.
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        if ContinuationToken is not None:
            keys = [key for key in keys if key > ContinuationToken]
        page = keys[: self._PAGE_SIZE]
        response: dict[str, object] = {
            "Contents": [{"Key": key, "LastModified": self.last_modified[key]} for key in page],
            "IsTruncated": len(keys) > self._PAGE_SIZE,
        }
        if response["IsTruncated"]:
            response["NextContinuationToken"] = page[-1]
        return response

    def set_last_modified(self, key: str, when: datetime) -> None:
        self.last_modified[key] = when


def _completed_match(match_id: str, *, updated_at: datetime) -> Match:
    human, agent = make_participants()
    engine = CounterGameEngine(target=1, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    match.start(engine)
    match.take_turn(engine, human.participant_id, {"delta": 5})
    match.complete(engine)
    match.updated_at = updated_at
    return match


def _active_match(match_id: str, *, updated_at: datetime) -> Match:
    human, agent = make_participants()
    engine = CounterGameEngine(target=1000, game_id="counter-demo")
    match = Match.create(game_id=engine.game_id, participants=[human, agent], match_id=match_id)
    match.start(engine)
    match.updated_at = updated_at
    return match


def _deps() -> tuple[InMemoryMatchStore, S3MatchArchive, FakeS3Client]:
    s3_client = FakeS3Client()
    archive = S3MatchArchive("league-archive", client=s3_client)
    return InMemoryMatchStore(), archive, s3_client


def _default_config(**overrides: int) -> CapacityConfig:
    base = {
        "max_concurrent_matches": 1000,
        "max_stored_matches": 1000,
        "max_match_age_days_hot": 3,
        "max_archive_age_days": 180,
    }
    base.update(overrides)
    return CapacityConfig(**base)


# --- pass 1: hot-stale completed matches -----------------------------------


def test_archives_and_deletes_hot_stale_completed_matches() -> None:
    store, archive, s3_client = _deps()
    stale = _completed_match("m-stale", updated_at=_NOW - timedelta(days=5))
    fresh = _completed_match("m-fresh", updated_at=_NOW - timedelta(days=1))
    store.save(stale)
    store.save(fresh)
    config = _default_config(max_match_age_days_hot=3)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=False,
    )

    assert len(report.actions) == 1
    assert report.actions[0].kind == "archive_hot_stale"
    assert report.actions[0].match_id == "m-stale"
    assert set(store.list_ids()) == {"m-fresh"}
    assert archive_key(stale) in s3_client.objects


def test_hot_stale_dry_run_reports_without_mutating_anything() -> None:
    store, archive, s3_client = _deps()
    stale = _completed_match("m-stale", updated_at=_NOW - timedelta(days=5))
    store.save(stale)
    config = _default_config(max_match_age_days_hot=3)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=True,
    )

    assert report.dry_run is True
    assert [a.kind for a in report.actions] == ["archive_hot_stale"]
    assert report.actions[0].match_id == "m-stale"
    assert set(store.list_ids()) == {"m-stale"}
    assert s3_client.objects == {}


def test_active_and_paused_matches_are_never_archived_by_the_hot_stale_pass() -> None:
    store, archive, s3_client = _deps()
    store.save(_active_match("m-active", updated_at=_NOW - timedelta(days=30)))
    config = _default_config(max_match_age_days_hot=3)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=False,
    )

    assert report.actions == ()
    assert set(store.list_ids()) == {"m-active"}


# --- pass 2: aged-out S3 archives -------------------------------------------


def test_deletes_archives_older_than_max_archive_age_days() -> None:
    store, archive, s3_client = _deps()
    old_match = _completed_match("m-old", updated_at=_NOW)
    fresh_match = _completed_match("m-fresh", updated_at=_NOW)
    old_key = archive.archive(old_match)
    fresh_key = archive.archive(fresh_match)
    s3_client.set_last_modified(old_key, _NOW - timedelta(days=200))
    s3_client.set_last_modified(fresh_key, _NOW - timedelta(days=10))
    config = _default_config(max_archive_age_days=180)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=False,
    )

    assert [a.kind for a in report.actions] == ["delete_aged_archive"]
    assert report.actions[0].match_id == "m-old"
    assert old_key not in s3_client.objects
    assert fresh_key in s3_client.objects


def test_aged_archive_deletion_dry_run_does_not_delete() -> None:
    store, archive, s3_client = _deps()
    old_match = _completed_match("m-old", updated_at=_NOW)
    old_key = archive.archive(old_match)
    s3_client.set_last_modified(old_key, _NOW - timedelta(days=200))
    config = _default_config(max_archive_age_days=180)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=True,
    )

    assert [a.kind for a in report.actions] == ["delete_aged_archive"]
    assert old_key in s3_client.objects


def test_aged_archive_pass_paginates_across_multiple_pages() -> None:
    store, archive, s3_client = _deps()
    keys = []
    for i in range(5):
        match = _completed_match(f"m-{i}", updated_at=_NOW)
        key = archive.archive(match)
        s3_client.set_last_modified(key, _NOW - timedelta(days=200))
        keys.append(key)
    config = _default_config(max_archive_age_days=180)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=False,
    )

    assert len(report.actions) == 5
    assert s3_client.objects == {}


# --- pass 3: max_stored_matches overflow ------------------------------------


def test_overflow_archives_oldest_completed_matches_first() -> None:
    store, archive, s3_client = _deps()
    oldest = _completed_match("m-oldest", updated_at=_NOW - timedelta(days=10))
    middle = _completed_match("m-middle", updated_at=_NOW - timedelta(days=5))
    newest = _completed_match("m-newest", updated_at=_NOW - timedelta(days=1))
    for match in (oldest, middle, newest):
        store.save(match)
    # Long hot-age window so pass 1 never fires; only overflow should act.
    config = _default_config(max_match_age_days_hot=365, max_stored_matches=2)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=False,
    )

    assert [a.kind for a in report.actions] == ["archive_overflow"]
    assert report.actions[0].match_id == "m-oldest"
    assert set(store.list_ids()) == {"m-middle", "m-newest"}


def test_overflow_dry_run_reports_without_mutating() -> None:
    store, archive, s3_client = _deps()
    oldest = _completed_match("m-oldest", updated_at=_NOW - timedelta(days=10))
    newest = _completed_match("m-newest", updated_at=_NOW - timedelta(days=1))
    store.save(oldest)
    store.save(newest)
    config = _default_config(max_match_age_days_hot=365, max_stored_matches=1)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=True,
    )

    assert [a.kind for a in report.actions] == ["archive_overflow"]
    assert set(store.list_ids()) == {"m-oldest", "m-newest"}
    assert s3_client.objects == {}


def test_overflow_only_ever_archives_completed_matches() -> None:
    """Active/paused overflow is left alone — that is check_capacity's job, not cleanup's."""
    store, archive, s3_client = _deps()
    completed = _completed_match("m-completed", updated_at=_NOW - timedelta(days=10))
    active = _active_match("m-active", updated_at=_NOW - timedelta(days=10))
    store.save(completed)
    store.save(active)
    config = _default_config(max_match_age_days_hot=365, max_stored_matches=1)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=False,
    )

    assert [a.kind for a in report.actions] == ["archive_overflow"]
    assert report.actions[0].match_id == "m-completed"
    assert set(store.list_ids()) == {"m-active"}


def test_no_overflow_action_when_under_the_cap() -> None:
    store, archive, s3_client = _deps()
    store.save(_completed_match("m-1", updated_at=_NOW))
    config = _default_config(max_match_age_days_hot=365, max_stored_matches=100)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=False,
    )

    assert report.actions == ()


# --- cross-pass consistency + report/logging shape --------------------------


def test_dry_run_and_real_run_compute_the_identical_action_set() -> None:
    """A dry-run preview must be exactly what a real run would do, not an approximation."""

    def _seed(store: InMemoryMatchStore, archive: S3MatchArchive, s3_client: FakeS3Client) -> None:
        store.save(_completed_match("m-hot-stale", updated_at=_NOW - timedelta(days=10)))
        store.save(_completed_match("m-overflow-old", updated_at=_NOW - timedelta(days=200)))
        store.save(_completed_match("m-overflow-new", updated_at=_NOW - timedelta(days=190)))
        aged = _completed_match("m-aged-archive", updated_at=_NOW)
        key = archive.archive(aged)
        s3_client.set_last_modified(key, _NOW - timedelta(days=200))

    config = _default_config(
        max_match_age_days_hot=3, max_stored_matches=1, max_archive_age_days=180
    )

    dry_store, dry_archive, dry_s3 = _deps()
    _seed(dry_store, dry_archive, dry_s3)
    dry_report = run_cleanup(
        match_store=dry_store,
        archive=dry_archive,
        s3_client=dry_s3,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=True,
    )

    real_store, real_archive, real_s3 = _deps()
    _seed(real_store, real_archive, real_s3)
    real_report = run_cleanup(
        match_store=real_store,
        archive=real_archive,
        s3_client=real_s3,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=False,
    )

    dry_pairs = sorted((a.kind, a.match_id) for a in dry_report.actions)
    real_pairs = sorted((a.kind, a.match_id) for a in real_report.actions)
    assert dry_pairs == real_pairs
    assert len(dry_pairs) > 0


def test_report_to_dict_shape() -> None:
    store, archive, s3_client = _deps()
    store.save(_completed_match("m-stale", updated_at=_NOW - timedelta(days=10)))
    config = _default_config(max_match_age_days_hot=3)

    report = run_cleanup(
        match_store=store,
        archive=archive,
        s3_client=s3_client,
        bucket_name="league-archive",
        config=config,
        now=_NOW,
        dry_run=True,
    )
    payload = report.to_dict()

    assert payload["dry_run"] is True
    assert payload["action_count"] == 1
    assert payload["actions"] == [
        {
            "kind": "archive_hot_stale",
            "match_id": "m-stale",
            "reason": payload["actions"][0]["reason"],
        }
    ]


def test_every_action_is_logged_as_a_structured_line(caplog) -> None:
    store, archive, s3_client = _deps()
    store.save(_completed_match("m-stale", updated_at=_NOW - timedelta(days=10)))
    config = _default_config(max_match_age_days_hot=3)

    with caplog.at_level(logging.INFO, logger="league_site.aws_lambda.cleanup"):
        run_cleanup(
            match_store=store,
            archive=archive,
            s3_client=s3_client,
            bucket_name="league-archive",
            config=config,
            now=_NOW,
            dry_run=False,
        )

    # Only the per-action lines are bare JSON objects; the end-of-sweep
    # summary line has a "cleanup sweep complete: " text prefix instead,
    # so a startswith check (not a substring search) isolates them.
    action_lines = [msg for msg in caplog.messages if msg.startswith('{"dry_run"')]
    assert len(action_lines) == 1
    assert '"match_id": "m-stale"' in action_lines[0]
    assert '"dry_run": false' in action_lines[0]
