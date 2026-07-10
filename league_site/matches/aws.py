"""Thin DynamoDB/S3 adapter skeleton for match persistence.

This is the only module in :mod:`league_site.matches` that imports
``boto3``. The import is guarded: ``boto3`` ships behind this project's
``aws`` extra (``uv sync --extra aws``), and nothing in the domain model or
its test suite should require it — importing this module without ``boto3``
installed raises a clear ``RuntimeError`` only when an adapter is actually
*instantiated*, not merely imported.

Both classes below accept a pre-built ``resource``/``client`` so callers
(and tests) can inject a fake and never touch real AWS — no credentials or
region configuration are required to exercise this module. Wiring these
adapters into the live platform (table/bucket provisioning, pagination,
retries, GSIs for "list matches by status") is a later task; this module
only fixes the item/key shape so that task has a stable target — see
:mod:`league_site.matches.serialization` for the DynamoDB single-table
design and the S3 archive key scheme.
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

from league_site.matches.errors import MatchNotFoundError
from league_site.matches.match import Match
from league_site.matches.serialization import archive_key, from_item, to_archive_dict, to_item
from league_site.matches.store import MatchStore


def _require_boto3() -> None:
    if boto3 is None:
        raise RuntimeError(
            "boto3 is required for league_site.matches.aws adapters; "
            "install it with `uv sync --extra aws`"
        ) from _IMPORT_ERROR


class DynamoDBMatchStore(MatchStore):
    """``MatchStore`` backed by a single DynamoDB table (single-table design).

    See :mod:`league_site.matches.serialization` for the ``PK``/``SK`` item
    shape this class reads and writes. Pass ``resource`` to inject a fake
    (or a pre-configured) ``boto3`` DynamoDB resource; otherwise one is
    built lazily from the default AWS config.
    """

    def __init__(self, table_name: str, *, resource: Any | None = None) -> None:
        _require_boto3()
        self._table_name = table_name
        self._resource = resource if resource is not None else boto3.resource("dynamodb")
        self._table = self._resource.Table(table_name)

    def save(self, match: Match) -> None:
        self._table.put_item(Item=to_item(match))

    def load(self, match_id: str) -> Match:
        response = self._table.get_item(Key={"PK": f"MATCH#{match_id}", "SK": "METADATA"})
        item = response.get("Item")
        if item is None:
            raise MatchNotFoundError(match_id)
        return from_item(item)

    def delete(self, match_id: str) -> None:
        self._table.delete_item(Key={"PK": f"MATCH#{match_id}", "SK": "METADATA"})

    def list_ids(self) -> list[str]:
        raise NotImplementedError(
            "listing matches requires a GSI query; wiring is a later task (see module docstring)"
        )


class S3MatchArchive:
    """Skeleton archive writer/reader for completed matches in S3.

    Key scheme: ``archives/{year}/{match_id}.json`` — see
    :func:`league_site.matches.serialization.archive_key`. Not a
    ``MatchStore`` (archives are write-once/read-rarely, not the live
    persistence path), so it exposes ``archive``/``retrieve`` instead of
    ``save``/``load``.
    """

    def __init__(self, bucket: str, *, client: Any | None = None) -> None:
        _require_boto3()
        self._bucket = bucket
        self._client = client if client is not None else boto3.client("s3")

    def archive(self, match: Match) -> str:
        """Write ``match``'s archive JSON to S3 and return the key it was written to."""
        key = archive_key(match)
        body = json.dumps(to_archive_dict(match)).encode("utf-8")
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=body, ContentType="application/json"
        )
        return key

    def retrieve(self, match_id: str, *, year: int) -> Match:
        """Read back a previously archived match given its id and archive year."""
        key = f"archives/{year}/{match_id}.json"
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        body = response["Body"].read()
        return from_item(json.loads(body))
