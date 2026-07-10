"""The scheduled cleanup Lambda: price-aware archive/delete sweep of match state.

Triggered daily by ``infra/template.yaml``'s ``CleanupFunction`` EventBridge
``Schedule`` event (see ``docs/capacity.md`` for the full price-aware
retention policy this job enforces, and
:mod:`league_site.capacity.config` for the caps/windows it reads).

Three passes, always in this order, each named after the
:class:`~league_site.capacity.config.CapacityConfig` window it enforces:

1. **Hot-stale ``COMPLETED`` matches** — a completed match older (by
   ``updated_at``, the timestamp
   :meth:`~league_site.matches.match.Match.complete` stamps) than
   ``max_match_age_days_hot`` is archived to S3
   (:class:`~league_site.matches.aws.S3MatchArchive`) and deleted from the
   hot store.
2. **Aged-out S3 archives** — every object under the archive bucket's
   ``archives/`` prefix older (by S3's own ``LastModified``) than
   ``max_archive_age_days`` is deleted outright; nothing is re-archived
   from here — a hard delete. See ``docs/capacity.md`` for why this is
   safe: the platform's durable historical record is the versioned JSONL
   dataset export (:mod:`league_site.datasets.export`), not this raw
   per-match archive.
3. **``max_stored_matches`` overflow** — if the hot store still holds more
   matches than the cap, the oldest ``COMPLETED`` matches (by
   ``updated_at``, ascending) are archived and deleted, oldest-first, until
   back under the cap. Only ``COMPLETED`` matches are ever archived here —
   overflow made up of ``ACTIVE``/``PAUSED`` matches is
   :mod:`league_site.capacity.guard`'s job (refusing *new* matches), not
   this job's; archiving a match that has not reached a terminal state
   would throw away live game state.

Every dependency (``match_store``, ``archive``, ``s3_client``, ``now``) is
injected so the whole sweep is exercised in tests against fakes, never real
AWS — see ``tests/test_cleanup_handler.py``. ``event={"dry_run": true}``
computes and logs every :class:`CleanupAction` the sweep *would* take
without calling ``archive``/``delete``/``delete_object`` — dry-run and real
runs compute the identical action list (a match one pass would have
removed is excluded from the next pass's counts even in dry-run, via
``removed_ids``), so a dry-run preview is exactly what a real run would do,
never an approximation of it.

Production wiring note: :func:`handler` builds a real
:class:`~league_site.matches.aws.DynamoDBMatchStore` from
``MATCHES_TABLE_NAME``, whose ``list_ids`` is not implemented yet (it needs
a GSI on status/updated_at — see that module's docstring); this job cannot
run against the *deployed* table until that GSI lands. That is the same,
already-documented limitation :mod:`league_site.capacity.guard` calls out
for the create-match path, not a new gap this module introduces.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

try:
    import boto3
except ImportError as exc:  # pragma: no cover - exercised only without the aws extra
    boto3 = None  # type: ignore[assignment]
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None

from league_site.capacity.config import CapacityConfig
from league_site.matches.aws import DynamoDBMatchStore, S3MatchArchive
from league_site.matches.match import Match, MatchStatus
from league_site.matches.store import MatchStore

logger = logging.getLogger(__name__)

#: S3 key prefix every match archive lives under (see
#: :func:`league_site.matches.serialization.archive_key`).
_ARCHIVE_PREFIX = "archives/"


def _require_boto3() -> None:
    if boto3 is None:
        raise RuntimeError(
            "boto3 is required for league_site.aws_lambda.cleanup.handler; "
            "install it with `uv sync --extra aws`"
        ) from _IMPORT_ERROR


@dataclass(frozen=True)
class CleanupAction:
    """One action the sweep took (or, in dry-run, would take).

    ``kind`` is one of ``"archive_hot_stale"``, ``"delete_aged_archive"``,
    or ``"archive_overflow"`` — see the module docstring for what each pass
    does. ``reason`` is a human-readable explanation carried into the
    structured log line and into :meth:`CleanupReport.to_dict`, so an
    operator reading logs or ``league-site cleanup --json`` output never
    has to reverse-engineer *why* an action fired.
    """

    kind: str
    match_id: str
    reason: str


@dataclass(frozen=True)
class CleanupReport:
    """The full, structured result of one :func:`run_cleanup` call."""

    dry_run: bool
    actions: tuple[CleanupAction, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "action_count": len(self.actions),
            "actions": [asdict(action) for action in self.actions],
        }


def _log_action(action: CleanupAction, *, dry_run: bool) -> None:
    """Log ``action`` as one structured JSON line, tagged with whether it actually ran."""
    logger.info(json.dumps({"dry_run": dry_run, **asdict(action)}))


def _archive_and_delete(match: Match, match_store: MatchStore, archive: S3MatchArchive) -> None:
    archive.archive(match)
    match_store.delete(match.match_id)


def _archive_hot_stale(
    match_store: MatchStore,
    archive: S3MatchArchive,
    config: CapacityConfig,
    current_time: datetime,
    *,
    dry_run: bool,
    actions: list[CleanupAction],
    removed_ids: set[str],
) -> None:
    cutoff = current_time - timedelta(days=config.max_match_age_days_hot)
    for match_id in list(match_store.list_ids()):
        match = match_store.load(match_id)
        if match.status is not MatchStatus.COMPLETED or match.updated_at > cutoff:
            continue
        action = CleanupAction(
            kind="archive_hot_stale",
            match_id=match_id,
            reason=(
                f"completed match last updated {match.updated_at.isoformat()}, older than "
                f"max_match_age_days_hot={config.max_match_age_days_hot}d"
            ),
        )
        actions.append(action)
        _log_action(action, dry_run=dry_run)
        removed_ids.add(match_id)
        if not dry_run:
            _archive_and_delete(match, match_store, archive)


def _delete_aged_archives(
    s3_client: Any,
    bucket_name: str,
    config: CapacityConfig,
    current_time: datetime,
    *,
    dry_run: bool,
    actions: list[CleanupAction],
) -> None:
    cutoff = current_time - timedelta(days=config.max_archive_age_days)
    continuation_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Bucket": bucket_name, "Prefix": _ARCHIVE_PREFIX}
        if continuation_token is not None:
            kwargs["ContinuationToken"] = continuation_token
        response = s3_client.list_objects_v2(**kwargs)
        for entry in response.get("Contents", []):
            last_modified = entry["LastModified"]
            if last_modified > cutoff:
                continue
            key = entry["Key"]
            match_id = key.rsplit("/", 1)[-1].removesuffix(".json")
            action = CleanupAction(
                kind="delete_aged_archive",
                match_id=match_id,
                reason=(
                    f"archive last modified {last_modified.isoformat()}, older than "
                    f"max_archive_age_days={config.max_archive_age_days}d"
                ),
            )
            actions.append(action)
            _log_action(action, dry_run=dry_run)
            if not dry_run:
                s3_client.delete_object(Bucket=bucket_name, Key=key)
        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")


def _archive_overflow(
    match_store: MatchStore,
    archive: S3MatchArchive,
    config: CapacityConfig,
    *,
    dry_run: bool,
    actions: list[CleanupAction],
    removed_ids: set[str],
) -> None:
    remaining_ids = [mid for mid in match_store.list_ids() if mid not in removed_ids]
    overflow = len(remaining_ids) - config.max_stored_matches
    if overflow <= 0:
        return

    completed = sorted(
        (
            match
            for match in (match_store.load(mid) for mid in remaining_ids)
            if match.status is MatchStatus.COMPLETED
        ),
        key=lambda match: match.updated_at,
    )
    for match in completed[:overflow]:
        action = CleanupAction(
            kind="archive_overflow",
            match_id=match.match_id,
            reason=(
                f"stored match count exceeds max_stored_matches={config.max_stored_matches} "
                f"by {overflow}; archiving oldest-first"
            ),
        )
        actions.append(action)
        _log_action(action, dry_run=dry_run)
        removed_ids.add(match.match_id)
        if not dry_run:
            _archive_and_delete(match, match_store, archive)


def run_cleanup(
    *,
    match_store: MatchStore,
    archive: S3MatchArchive,
    s3_client: Any,
    bucket_name: str,
    config: CapacityConfig,
    now: datetime | None = None,
    dry_run: bool = False,
) -> CleanupReport:
    """Run the three-pass sweep described in the module docstring.

    ``s3_client`` is used directly (not through ``archive``) to list and
    delete objects under the archive bucket's ``archives/`` prefix —
    :class:`~league_site.matches.aws.S3MatchArchive` only knows how to
    write/read one archive at a time, not enumerate the bucket. Both
    ``archive`` and ``s3_client`` should point at the same ``bucket_name``.

    ``now`` defaults to the current UTC time; tests pass a fixed value so
    "stale" is deterministic. Every dependency is otherwise a plain
    injected object — nothing here reaches for a real AWS credential or
    endpoint on its own.
    """
    current_time = now if now is not None else datetime.now(timezone.utc)
    actions: list[CleanupAction] = []
    removed_ids: set[str] = set()

    _archive_hot_stale(
        match_store,
        archive,
        config,
        current_time,
        dry_run=dry_run,
        actions=actions,
        removed_ids=removed_ids,
    )
    _delete_aged_archives(
        s3_client, bucket_name, config, current_time, dry_run=dry_run, actions=actions
    )
    _archive_overflow(
        match_store, archive, config, dry_run=dry_run, actions=actions, removed_ids=removed_ids
    )

    report = CleanupReport(dry_run=dry_run, actions=tuple(actions))
    logger.info("cleanup sweep complete: %s", json.dumps(report.to_dict()))
    return report


def handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    """Lambda entrypoint for the daily EventBridge-scheduled cleanup rule.

    Builds real ``DynamoDBMatchStore``/``S3MatchArchive``/S3-client
    dependencies from the environment (``MATCHES_TABLE_NAME``,
    ``ARCHIVE_BUCKET_NAME``, and the ``LEAGUE_CAPACITY_*`` variables read by
    :meth:`~league_site.capacity.config.CapacityConfig.from_env`) and runs
    :func:`run_cleanup`. ``event`` may carry ``{"dry_run": true}`` — both
    EventBridge's fixed JSON schedule input and a manual test invocation
    can set it — to run in report-only mode; absent or falsy runs for real.
    Returns :meth:`CleanupReport.to_dict`.
    """
    del context
    _require_boto3()
    event = event or {}
    dry_run = bool(event.get("dry_run", False))

    table_name = os.environ["MATCHES_TABLE_NAME"]
    bucket_name = os.environ["ARCHIVE_BUCKET_NAME"]
    config = CapacityConfig.from_env()

    match_store = DynamoDBMatchStore(table_name)
    archive = S3MatchArchive(bucket_name)
    s3_client = boto3.client("s3")

    report = run_cleanup(
        match_store=match_store,
        archive=archive,
        s3_client=s3_client,
        bucket_name=bucket_name,
        config=config,
        dry_run=dry_run,
    )
    return report.to_dict()
