"""Tests for the KMS/DynamoDB adapter skeleton in league_site.byok.aws_vault.

Every test injects fake KMS/DynamoDB clients so nothing here ever touches
real AWS, needs credentials, or needs a region configured — the ``aws``
extra (``boto3``) only needs to be importable, which `uv sync --extra aws`
guarantees for this suite.
"""

from __future__ import annotations

import base64

import pytest

from league_site.byok.aws_vault import KMSDynamoDBKeyVault
from league_site.byok.vault import KeyHandleInfo, KeyNotFoundError, SecretKey

SUPER_SECRET = "sk-super-secret-value-should-never-leak-anywhere"  # nosec B105 - test fixture


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table resource: an in-process dict."""

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, object]] = {}

    def put_item(
        self, *, Item: dict[str, object]
    ) -> None:  # noqa: N803 - matches boto3's kwarg casing
        self.items[(Item["PK"], Item["SK"])] = dict(Item)

    def get_item(self, *, Key: dict[str, str]) -> dict[str, object]:  # noqa: N803
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": dict(item)} if item is not None else {}


class FakeDynamoDBResource:
    def __init__(self) -> None:
        self.table = FakeTable()

    def Table(self, name: str) -> FakeTable:  # noqa: N802 - matches boto3's method casing
        return self.table


class FakeKMSClient:
    """A reversible XOR "encryption" fake — good enough to prove the plumbing,
    never meant to look anything like real KMS crypto."""

    def __init__(self) -> None:
        self.encrypt_calls: list[bytes] = []
        self.decrypt_calls: list[bytes] = []

    @staticmethod
    def _xor(data: bytes) -> bytes:
        return bytes(b ^ 0x5A for b in data)

    def encrypt(self, *, KeyId: str, Plaintext: bytes) -> dict[str, object]:  # noqa: N803
        self.encrypt_calls.append(Plaintext)
        return {"CiphertextBlob": self._xor(Plaintext), "KeyId": KeyId}

    def decrypt(self, *, CiphertextBlob: bytes, KeyId: str) -> dict[str, object]:  # noqa: N803
        self.decrypt_calls.append(CiphertextBlob)
        return {"Plaintext": self._xor(CiphertextBlob), "KeyId": KeyId}


def _vault() -> tuple[KMSDynamoDBKeyVault, FakeDynamoDBResource, FakeKMSClient]:
    resource = FakeDynamoDBResource()
    kms = FakeKMSClient()
    vault = KMSDynamoDBKeyVault(
        "league-byok-keys",
        "arn:aws:kms:us-east-1:123456789012:key/test-key",
        kms_client=kms,
        dynamodb_resource=resource,
    )
    return vault, resource, kms


def test_module_imports_and_boto3_is_available_via_the_aws_extra() -> None:
    import league_site.byok.aws_vault as aws_vault_module

    assert aws_vault_module.boto3 is not None


def test_put_stores_ciphertext_only_never_plaintext_in_the_fake_table() -> None:
    vault, resource, kms = _vault()

    handle = vault.put("player-1", "anthropic", SUPER_SECRET)

    stored_item = resource.table.items[(f"BYOK#{handle}", "METADATA")]
    assert stored_item["entity_type"] == "byok_key"
    assert stored_item["owner"] == "player-1"
    assert stored_item["provider"] == "anthropic"
    # the plaintext never appears anywhere in the stored item
    assert SUPER_SECRET not in str(stored_item)
    # the stored ciphertext really is KMS's (fake) encrypt output, base64-encoded
    expected_ciphertext = base64.b64encode(kms._xor(SUPER_SECRET.encode("utf-8"))).decode("ascii")
    assert stored_item["ciphertext"] == expected_ciphertext


def test_put_calls_kms_encrypt_with_the_plaintext_material() -> None:
    vault, _resource, kms = _vault()

    vault.put("player-1", "anthropic", SUPER_SECRET)

    assert kms.encrypt_calls == [SUPER_SECRET.encode("utf-8")]


def test_get_decrypts_via_kms_and_returns_a_secret_key_round_tripping_the_material() -> None:
    vault, _resource, kms = _vault()
    handle = vault.put("player-1", "anthropic", SUPER_SECRET)

    fetched = vault.get(handle)

    assert isinstance(fetched, SecretKey)
    assert fetched.reveal() == SUPER_SECRET
    assert len(kms.decrypt_calls) == 1


def test_get_unknown_handle_raises_key_not_found() -> None:
    vault, _resource, _kms = _vault()
    with pytest.raises(KeyNotFoundError):
        vault.get("byok_does_not_exist")


def test_put_accepts_a_secret_key_directly() -> None:
    vault, _resource, _kms = _vault()
    handle = vault.put("player-1", "anthropic", SecretKey(SUPER_SECRET))

    assert vault.get(handle).reveal() == SUPER_SECRET


def test_revoke_then_get_fails_cleanly() -> None:
    vault, _resource, _kms = _vault()
    handle = vault.put("player-1", "anthropic", SUPER_SECRET)

    vault.revoke(handle)

    with pytest.raises(KeyNotFoundError):
        vault.get(handle)


def test_revoke_unknown_handle_raises_key_not_found() -> None:
    vault, _resource, _kms = _vault()
    with pytest.raises(KeyNotFoundError):
        vault.revoke("byok_does_not_exist")


def test_describe_returns_metadata_without_touching_kms_or_leaking_material() -> None:
    vault, _resource, kms = _vault()
    handle = vault.put("player-1", "openai", SUPER_SECRET)
    kms.decrypt_calls.clear()

    info = vault.describe(handle)

    assert isinstance(info, KeyHandleInfo)
    assert info.handle == handle
    assert info.owner == "player-1"
    assert info.provider == "openai"
    assert info.revoked is False
    assert kms.decrypt_calls == []  # describe never decrypts


def test_describe_reflects_revocation() -> None:
    vault, _resource, _kms = _vault()
    handle = vault.put("player-1", "anthropic", SUPER_SECRET)
    vault.revoke(handle)

    assert vault.describe(handle).revoked is True


def test_describe_unknown_handle_raises_key_not_found() -> None:
    vault, _resource, _kms = _vault()
    with pytest.raises(KeyNotFoundError):
        vault.describe("byok_does_not_exist")


def test_require_boto3_raises_runtime_error_when_boto3_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import league_site.byok.aws_vault as aws_vault_module

    monkeypatch.setattr(aws_vault_module, "boto3", None)
    monkeypatch.setattr(aws_vault_module, "_IMPORT_ERROR", ImportError("no boto3"))

    with pytest.raises(RuntimeError, match="aws"):
        KMSDynamoDBKeyVault(
            "league-byok-keys",
            "arn:aws:kms:us-east-1:123456789012:key/test-key",
            kms_client=FakeKMSClient(),
            dynamodb_resource=FakeDynamoDBResource(),
        )
