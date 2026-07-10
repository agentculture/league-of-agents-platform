"""h9 parity test: every state-mutating operator action has a CLI verb.

c17/h9's honesty condition: "Every state-mutating operator action available
anywhere (console, scripts) also exists as a league-site CLI verb with
--json output and a dry-run default." :data:`INVENTORY` below is the single
source of truth for that claim: one entry per state-mutating operator
action shipped so far —

* **deploy** — deploying/redeploying the AWS stack (also the only
  mechanism by which a capacity-cap change, a ``LEAGUE_CAPACITY_MAX_*``
  environment value, actually takes effect on the running platform — there
  is no separate "set capacity" mutation to enumerate).
* **cleanup/archive (sweep-wide)** — the scheduled archive/delete sweep of
  stale match state.
* **match admin (single-match archive)** — an operator archiving one
  chosen match, the same store->S3 path the sweep uses.

Each entry is checked two ways: parser introspection (the verb path
resolves, ``--json`` is supported, a ``--apply`` flag exists and defaults to
``False`` — the dry-run default) and a dry-run invocation against
fakes/spies that observe zero mutation.
"""

from __future__ import annotations

import pytest

from league_site.cli import _build_parser, main
from league_site.cli._commands import _stores, ops
from tests._cli_ops_support import (
    FakeArchive,
    FakeS3Client,
    SpyMatchStore,
    hot_stale_completed_match,
)

INVENTORY = [
    {
        "id": "deploy_stack",
        "description": (
            "Deploy/redeploy the AWS stack; also the only mechanism by which a "
            "capacity-cap change (a LEAGUE_CAPACITY_MAX_* environment value) "
            "takes effect on the running platform"
        ),
        "argv": ["ops", "deploy"],
    },
    {
        "id": "cleanup_sweep",
        "description": "Price-aware archive/delete sweep of stale match state",
        "argv": ["ops", "cleanup"],
    },
    {
        "id": "match_archive",
        "description": (
            "Operator match administration: archive one chosen match to S3 and "
            "remove it from the store"
        ),
        "argv": ["match", "archive", "some-match-id"],
    },
]


def test_inventory_is_the_expected_set_of_mutating_actions() -> None:
    # Guards the inventory itself against silent drift: if a new
    # state-mutating operator action ships, this test (and the parametrized
    # one below) must be extended, not just the command that implements it.
    ids = {entry["id"] for entry in INVENTORY}
    assert ids == {"deploy_stack", "cleanup_sweep", "match_archive"}


@pytest.mark.parametrize("entry", INVENTORY, ids=[e["id"] for e in INVENTORY])
def test_mutating_action_has_verb_with_json_and_dry_run_default(entry: dict) -> None:
    parser = _build_parser()
    args = parser.parse_args([*entry["argv"], "--json"])
    assert callable(args.func), f"{entry['id']}: no handler registered for {entry['argv']}"
    assert getattr(args, "json", None) is True, f"{entry['id']}: --json not supported"
    assert hasattr(args, "apply"), f"{entry['id']}: no --apply flag (no dry-run default)"
    assert args.apply is False, f"{entry['id']}: not dry-run by default"


def test_deploy_stack_dry_run_invokes_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple] = []
    monkeypatch.setattr(ops, "_run_subprocess", lambda *a, **k: calls.append((a, k)))
    rc = main(["ops", "deploy", "--json"])
    assert rc == 0
    assert calls == [], "dry-run must never invoke bash infra/deploy.sh"
    capsys.readouterr()


def test_cleanup_sweep_dry_run_mutates_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(hot_stale_completed_match("m1"))
    store.saved.clear()  # forget the fixture's own setup write
    archive = FakeArchive()
    s3_client = FakeS3Client()
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))
    monkeypatch.setattr(_stores, "resolve_archive_bucket_name", lambda: "league-archive")
    monkeypatch.setattr(_stores, "resolve_archive", lambda bucket_name: archive)
    monkeypatch.setattr(_stores, "resolve_s3_client", lambda: s3_client)

    rc = main(["ops", "cleanup", "--json"])
    assert rc == 0
    assert store.saved == [] and store.deleted == []
    assert archive.archived == []
    assert s3_client.deleted_keys == []
    capsys.readouterr()


def test_match_archive_dry_run_mutates_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    store = SpyMatchStore()
    store.save(hot_stale_completed_match("m1"))
    monkeypatch.setattr(_stores, "resolve_match_store", lambda: (store, False))

    def _must_not_resolve_bucket() -> str:
        raise AssertionError("match archive dry-run must not resolve ARCHIVE_BUCKET_NAME")

    monkeypatch.setattr(_stores, "resolve_archive_bucket_name", _must_not_resolve_bucket)

    rc = main(["match", "archive", "m1", "--json"])
    assert rc == 0
    assert store.deleted == []
    assert store.list_ids() == ["m1"]
    capsys.readouterr()
