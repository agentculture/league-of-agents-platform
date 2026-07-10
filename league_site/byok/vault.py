"""BYO-key vault: store a player's own LLM API key, never an operator's.

A player can either connect their own external agent (bearer-token auth —
see :mod:`league_site.auth.tokens`) or paste their own LLM API key and let
the platform run a hosted agent against it (see
:mod:`league_site.byok.runner`). This module is the storage side of the
second path: :class:`KeyVault` is the persistence interface, and
:class:`SecretKey` is the redacting wrapper every bit of key material
passes through from the moment it enters this package.

Encryption-at-rest contract
----------------------------
Every persistence-backed :class:`KeyVault` implementation (see
:mod:`league_site.byok.aws_vault` for the KMS/DynamoDB adapter) MUST store
only ciphertext: key material is encrypted before it is written to disk or
to any remote store, and decrypted only transiently, in-process, to answer
a :meth:`KeyVault.get` call. Plaintext key material must never be written
to a log, a persistence layer, or any field that could be serialized.

:class:`InMemoryKeyVault` is the exception that proves the rule: it holds
key material in a process-local dict, in RAM only, and never persists it
anywhere — nothing is ever written to disk, so there is no "at rest" state
to encrypt. That absence of persistence *is* its at-rest story: a process
restart (or crash) loses every key it held, which is the correct behavior
for a reference/test implementation that must never accidentally become a
production secret store.

Nothing in this module ever puts raw key material into a log record,
an exception message, or a ``repr``/``str`` — see :class:`SecretKey`.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SecretKey:
    """A redacting wrapper around raw LLM API key material.

    ``SecretKey`` exists so that key material can be passed around,
    stashed in dataclasses, and handed to provider transports without ever
    being one accidental ``f"{key}"``, ``logger.info("%s", key)``, or
    ``repr(record)`` away from a log line. :meth:`__repr__` and
    :meth:`__str__` always return a fixed redacted placeholder — never the
    material, never even its length or a hash of it (a length or hash can
    itself leak information about the secret). The only way to read the
    real value is the explicit :meth:`reveal` call, which every call site
    in this codebase treats as a "handle with care" signal.
    """

    __slots__ = ("_material",)

    def __init__(self, material: str) -> None:
        if not isinstance(material, str) or not material:
            raise ValueError("key material must be a non-empty string")
        self._material = material

    def reveal(self) -> str:
        """Return the raw key material. Callers must never log or print the result."""
        return self._material

    def __repr__(self) -> str:  # pragma: no cover - trivial, exercised via test assertions
        return "SecretKey(***redacted***)"

    def __str__(self) -> str:  # pragma: no cover - trivial, exercised via test assertions
        return "SecretKey(***redacted***)"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretKey):
            return NotImplemented
        return self._material == other._material

    __hash__ = None  # type: ignore[assignment] - secrets are not meant to be dict/set keys


def coerce_secret(key_material: "SecretKey | str") -> SecretKey:
    """Wrap ``key_material`` in a :class:`SecretKey` if it isn't one already.

    Shared by :class:`InMemoryKeyVault` and
    :mod:`league_site.byok.aws_vault` so both accept a plain ``str`` or an
    already-wrapped :class:`SecretKey` at their ``put`` boundary.
    """
    if isinstance(key_material, SecretKey):
        return key_material
    return SecretKey(key_material)


class KeyVaultError(Exception):
    """Base class for :mod:`league_site.byok.vault` errors."""


class KeyNotFoundError(KeyVaultError):
    """Raised by :meth:`KeyVault.get`/:meth:`KeyVault.revoke` for an unknown or revoked handle.

    Deliberately raised for *both* "never existed" and "existed but was
    revoked" cases, the same uniform-failure design as
    :func:`league_site.auth.tokens.verify` returning ``None`` for every
    invalid-token reason — callers (the hosted-agent runner) don't need to
    distinguish why a handle doesn't work, only that it doesn't.
    """

    def __init__(self, handle: str) -> None:
        self.handle = handle
        super().__init__(f"no usable key found for handle {handle!r}")


@dataclass(frozen=True)
class KeyHandleInfo:
    """Metadata about a stored key, deliberately excluding the key material itself.

    Safe to log, display in user settings, or return from an API endpoint
    listing a player's stored keys.
    """

    handle: str
    owner: str
    provider: str
    created_at: datetime
    revoked: bool = False


class KeyVault(ABC):
    """Persistence interface for BYO LLM API keys. Implementations own how/where state lives.

    See the module docstring for the encryption-at-rest contract every
    persistence-backed implementation must honor. ``put`` returns an
    opaque *handle* string; nothing downstream (the hosted-agent runner,
    match records, logs) ever holds the key material itself, only this
    handle — mirroring how :mod:`league_site.auth.tokens` hands out a
    bearer token once and thereafter only deals in hashes/ids.
    """

    @abstractmethod
    def put(self, owner: str, provider: str, key_material: SecretKey | str) -> str:
        """Store ``key_material`` for ``owner``/``provider`` and return an opaque handle.

        ``owner`` is a platform user/agent identity string; ``provider`` is
        a provider registry name (see :mod:`league_site.byok.providers`),
        recorded so the runner knows which wire format to use without ever
        having to guess from the key's shape.
        """

    @abstractmethod
    def get(self, handle: str) -> SecretKey:
        """Return the :class:`SecretKey` for ``handle``.

        Raises :class:`KeyNotFoundError` if ``handle`` is unknown or has
        been revoked.
        """

    @abstractmethod
    def revoke(self, handle: str) -> None:
        """Revoke ``handle``: future :meth:`get` calls raise :class:`KeyNotFoundError`.

        Raises :class:`KeyNotFoundError` if ``handle`` is unknown.
        Revocation is permanent and one-way — there is no "un-revoke".
        """

    @abstractmethod
    def describe(self, handle: str) -> KeyHandleInfo:
        """Return metadata about ``handle`` without ever touching the key material.

        Raises :class:`KeyNotFoundError` if ``handle`` is unknown. Revoked
        handles are still describable (``revoked=True``) so a settings UI
        can show "revoked" rather than "not found".
        """


@dataclass
class _Entry:
    owner: str
    provider: str
    material: SecretKey
    created_at: datetime = field(default_factory=_utcnow)
    revoked: bool = False


class InMemoryKeyVault(KeyVault):
    """Reference :class:`KeyVault` holding key material in a process-local dict.

    See the module docstring's encryption-at-rest section: this
    implementation's at-rest story is that there is no "rest" — nothing is
    ever persisted to disk or to a remote store, so there is no ciphertext
    contract to satisfy. Suitable for tests and for a single-process
    deployment where losing all stored keys on restart is acceptable; not
    suitable for production (use :mod:`league_site.byok.aws_vault` there).
    """

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}

    def put(self, owner: str, provider: str, key_material: SecretKey | str) -> str:
        handle = f"byok_{uuid.uuid4().hex}"
        self._entries[handle] = _Entry(
            owner=owner, provider=provider, material=coerce_secret(key_material)
        )
        return handle

    def get(self, handle: str) -> SecretKey:
        entry = self._entries.get(handle)
        if entry is None or entry.revoked:
            raise KeyNotFoundError(handle)
        return entry.material

    def revoke(self, handle: str) -> None:
        entry = self._entries.get(handle)
        if entry is None:
            raise KeyNotFoundError(handle)
        entry.revoked = True

    def describe(self, handle: str) -> KeyHandleInfo:
        entry = self._entries.get(handle)
        if entry is None:
            raise KeyNotFoundError(handle)
        return KeyHandleInfo(
            handle=handle,
            owner=entry.owner,
            provider=entry.provider,
            created_at=entry.created_at,
            revoked=entry.revoked,
        )
