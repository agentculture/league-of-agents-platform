"""``AccountStore`` interface plus an in-memory reference implementation.

An :class:`AccountRecord` is the durable identity behind a human's GitHub
sign-in — the accountability unit agent tokens are anchored to (see
:mod:`league_site.auth.token_store`'s eventual ``owner_account_id``, a
later task). This module owns the record shape and the store contract;
:mod:`league_site.accounts.aws` adds a DynamoDB-backed adapter (imported
separately since it is the only module in this package that touches
``boto3``, mirroring :mod:`league_site.auth.aws_tokens`).
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    """Timezone-aware UTC timestamp — centralised so every stamp this package writes agrees.

    Not prefixed private: :mod:`league_site.accounts.aws` reuses this same
    function to stamp ``updated_at`` on :meth:`DynamoDBAccountStore.
    set_blocked`, so both implementations agree on how "now" is produced.
    """
    return datetime.now(timezone.utc)


def account_id_for(provider: str, provider_user_id: str) -> str:
    """Canonical account id: ``"<provider>:<provider_user_id>"`` (e.g. ``"github:12345"``).

    The one place this format is assembled — callers (the OAuth callback,
    operator tooling, tests) should always build ids through this function
    rather than interpolating the string themselves, so every consumer
    agrees on the exact separator and field order.
    """
    return f"{provider}:{provider_user_id}"


@dataclass(frozen=True)
class AccountRecord:
    """Persisted state for one signed-in human account.

    ``account_id`` is the stable identity key — see :func:`account_id_for`
    for how it's built from ``provider``/``provider_user_id``; it is never
    constructed ad hoc. ``email`` may be ``None``: GitHub reports no email
    at all for some accounts even after requesting the extra scope/fallback
    (a later task) — an absent email is a valid, explicit state, not an
    error, so this field has no default and every caller must say so
    either way. ``blocked`` starts ``False`` and, once an account exists,
    is the sole field :meth:`AccountStore.set_blocked` may change —
    :meth:`AccountStore.upsert` never flips it (see that method's
    docstring).
    """

    account_id: str
    provider: str
    provider_user_id: str
    display_name: str
    email: str | None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)
    blocked: bool = False


class AccountNotFoundError(Exception):
    """Raised by :meth:`AccountStore.set_blocked` when ``account_id`` has no record."""

    def __init__(self, account_id: str) -> None:
        self.account_id = account_id
        super().__init__(f"no account found with id {account_id!r}")


class AccountStore(ABC):
    """Persistence interface for human accounts. Implementations own how/where state lives."""

    @abstractmethod
    def get(self, account_id: str) -> AccountRecord | None:
        """Return the record for ``account_id``, or ``None`` if no account has that id."""

    @abstractmethod
    def upsert(self, record: AccountRecord) -> AccountRecord:
        """Insert or update the account identified by ``record.account_id``.

        Idempotent by ``account_id`` — calling this again for the same id
        (e.g. the OAuth callback re-upserting on every sign-in) updates the
        record in place rather than creating a second one. On an update,
        two fields are deliberately taken from the *previously stored*
        record instead of ``record``:

        * ``created_at`` — the account's original creation time never
          moves.
        * ``blocked`` — blocking is exclusively :meth:`set_blocked`'s job;
          a routine re-upsert at sign-in must never silently unblock an
          account just because the freshly built ``record`` defaults
          ``blocked`` to ``False``.

        Every other field (``provider``, ``provider_user_id``,
        ``display_name``, ``email``, ``updated_at``) is taken from
        ``record``. Returns the record actually persisted — identical to
        ``record`` on first insert.
        """

    @abstractmethod
    def set_blocked(self, account_id: str, blocked: bool) -> None:
        """Flip the ``blocked`` flag for ``account_id`` and stamp ``updated_at``.

        Raises :class:`AccountNotFoundError` if no record has that id.
        """

    def list_all(self) -> list[AccountRecord]:
        """Return every stored account record, in no particular order.

        The enumeration the operator ``league-site accounts list`` reads to
        show which accounts are blocked. Accounts have no growth-bounding cap
        the way agent tokens do, but they are one-per-signed-in-human, so a
        full scan is acceptable at launch scale — the same tradeoff
        :meth:`league_site.auth.token_store.TokenStore.list_all` documents.

        Deliberately *not* ``@abstractmethod``, matching
        :meth:`TokenStore.list_all`: a pre-existing :class:`AccountStore`
        subclass stays instantiable, and this default raises
        :class:`NotImplementedError` until the store grows its own
        implementation — see :meth:`InMemoryAccountStore.list_all`.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement AccountStore.list_all()"
        )


class InMemoryAccountStore(AccountStore):
    """Reference ``AccountStore`` backed by a process-local dict, keyed by ``account_id``."""

    def __init__(self) -> None:
        self._records: dict[str, AccountRecord] = {}

    def get(self, account_id: str) -> AccountRecord | None:
        return self._records.get(account_id)

    def upsert(self, record: AccountRecord) -> AccountRecord:
        existing = self._records.get(record.account_id)
        if existing is not None:
            record = dataclasses.replace(
                record, created_at=existing.created_at, blocked=existing.blocked
            )
        self._records[record.account_id] = record
        return record

    def set_blocked(self, account_id: str, blocked: bool) -> None:
        existing = self._records.get(account_id)
        if existing is None:
            raise AccountNotFoundError(account_id)
        self._records[account_id] = dataclasses.replace(
            existing, blocked=blocked, updated_at=utcnow()
        )

    def list_all(self) -> list[AccountRecord]:
        return list(self._records.values())
