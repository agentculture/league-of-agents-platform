"""KMS + DynamoDB-backed :class:`~league_site.byok.vault.KeyVault` skeleton.

This is the only module in :mod:`league_site.byok` that imports ``boto3``.
The import is guarded: ``boto3`` ships behind this project's ``aws`` extra
(``uv sync --extra aws``), and nothing in :mod:`league_site.byok.vault`,
:mod:`league_site.byok.providers`, :mod:`league_site.byok.runner`, or their
test suites should require it — importing this module without ``boto3``
installed raises a clear ``RuntimeError`` only when an adapter is actually
*instantiated*, not merely imported. Mirrors
:mod:`league_site.matches.aws` and :mod:`league_site.auth.aws_tokens` —
read either first if you're wiring up a third guarded adapter.

Both ``kms_client`` and ``dynamodb_resource`` are injectable so callers
(and tests) can pass a fake and never touch real AWS — no credentials or
region configuration are required to exercise this module.

Encryption-at-rest: :meth:`KMSDynamoDBKeyVault.put` calls KMS ``Encrypt``
on the plaintext key material and stores *only* the returned ciphertext
(base64-encoded) in DynamoDB; plaintext key material is never written to
any persistence layer. :meth:`KMSDynamoDBKeyVault.get` calls KMS
``Decrypt`` on the stored ciphertext and returns the plaintext wrapped in a
:class:`~league_site.byok.vault.SecretKey` — the plaintext exists only
transiently, in-process, for the lifetime of one provider call.

DynamoDB single-table design
-----------------------------
One table, keyed by a generic ``PK``/``SK`` pair, mirroring the shape used
by :mod:`league_site.matches.aws` and :mod:`league_site.auth.aws_tokens`::

    PK              SK        Attributes
    BYOK#<handle>   METADATA  entity_type, handle, owner, provider,
                               ciphertext (base64), created_at, revoked

``handle`` is the partition key because every request-path lookup
(:meth:`get`, :meth:`revoke`, :meth:`describe`) is by handle — the opaque
string :meth:`put` hands back to the caller, the same design as
:mod:`league_site.auth.token_store` keying agent tokens by hash.
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime, timezone
from typing import Any

try:
    import boto3
except ImportError as exc:  # pragma: no cover - exercised only without the aws extra
    boto3 = None  # type: ignore[assignment]
    _IMPORT_ERROR: ImportError | None = exc
else:
    _IMPORT_ERROR = None

from league_site.byok.vault import (
    KeyHandleInfo,
    KeyNotFoundError,
    KeyVault,
    SecretKey,
    coerce_secret,
)


def _require_boto3() -> None:
    if boto3 is None:
        raise RuntimeError(
            "boto3 is required for league_site.byok.aws_vault adapters; "
            "install it with `uv sync --extra aws`"
        ) from _IMPORT_ERROR


def _item_key(handle: str) -> dict[str, str]:
    return {"PK": f"BYOK#{handle}", "SK": "METADATA"}


class KMSDynamoDBKeyVault(KeyVault):
    """:class:`~league_site.byok.vault.KeyVault` storing KMS-encrypted BYOK key material.

    Pass pre-built ``kms_client``/``dynamodb_resource`` to inject fakes
    (tests never touch real AWS or need credentials/region config);
    otherwise both are built lazily from the default AWS config.
    """

    def __init__(
        self,
        table_name: str,
        kms_key_id: str,
        *,
        kms_client: Any | None = None,
        dynamodb_resource: Any | None = None,
    ) -> None:
        _require_boto3()
        self._table_name = table_name
        self._kms_key_id = kms_key_id
        self._kms = kms_client if kms_client is not None else boto3.client("kms")
        resource = (
            dynamodb_resource if dynamodb_resource is not None else boto3.resource("dynamodb")
        )
        self._table = resource.Table(table_name)

    def put(self, owner: str, provider: str, key_material: SecretKey | str) -> str:
        material = coerce_secret(key_material).reveal()
        encrypt_response = self._kms.encrypt(
            KeyId=self._kms_key_id, Plaintext=material.encode("utf-8")
        )
        ciphertext_blob = encrypt_response["CiphertextBlob"]
        if isinstance(ciphertext_blob, str):  # pragma: no cover - defensive, real KMS returns bytes
            ciphertext_blob = ciphertext_blob.encode("utf-8")
        handle = f"byok_{uuid.uuid4().hex}"
        self._table.put_item(
            Item={
                "PK": f"BYOK#{handle}",
                "SK": "METADATA",
                "entity_type": "byok_key",
                "handle": handle,
                "owner": owner,
                "provider": provider,
                "ciphertext": base64.b64encode(ciphertext_blob).decode("ascii"),
                "created_at": datetime.now(timezone.utc).isoformat(),
                "revoked": False,
            }
        )
        return handle

    def get(self, handle: str) -> SecretKey:
        item = self._load_item(handle)
        if item is None or item.get("revoked"):
            raise KeyNotFoundError(handle)
        ciphertext_blob = base64.b64decode(item["ciphertext"])
        decrypt_response = self._kms.decrypt(CiphertextBlob=ciphertext_blob, KeyId=self._kms_key_id)
        plaintext = decrypt_response["Plaintext"]
        if isinstance(plaintext, bytes):
            plaintext = plaintext.decode("utf-8")
        return SecretKey(plaintext)

    def revoke(self, handle: str) -> None:
        item = self._load_item(handle)
        if item is None:
            raise KeyNotFoundError(handle)
        item["revoked"] = True
        self._table.put_item(Item=item)

    def describe(self, handle: str) -> KeyHandleInfo:
        item = self._load_item(handle)
        if item is None:
            raise KeyNotFoundError(handle)
        return KeyHandleInfo(
            handle=item["handle"],
            owner=item["owner"],
            provider=item["provider"],
            created_at=datetime.fromisoformat(item["created_at"]),
            revoked=bool(item["revoked"]),
        )

    def _load_item(self, handle: str) -> dict[str, Any] | None:
        response = self._table.get_item(Key=_item_key(handle))
        return response.get("Item")
