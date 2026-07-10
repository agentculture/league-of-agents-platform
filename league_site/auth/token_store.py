"""``TokenStore`` interface plus an in-memory reference implementation.

Agent tokens are never persisted in plaintext: every stored
:class:`TokenRecord` carries only ``token_hash`` (a sha256 hex digest of the
bearer token), never the secret itself. See :mod:`league_site.auth.tokens`
for issuance, verification, and the hashing scheme, and
:mod:`league_site.auth.aws_tokens` for a DynamoDB-backed adapter skeleton
(mirrors :mod:`league_site.matches.aws` — imported separately since it is
the only module in this package that touches ``boto3``).
"""

from __future__ import annotations

import dataclasses
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TokenRecord:
    """Persisted state for one issued agent token.

    ``token_hash`` is a sha256 hex digest of the bearer token — the plaintext
    token itself is never stored here. ``agent_name``/``model``/``provider``
    are the benchmark identity fields and are immutable for the token's
    lifetime; only ``revoked`` changes, via :meth:`TokenStore.revoke`.
    """

    token_id: str
    token_hash: str
    agent_name: str
    model: str
    provider: str
    created_at: datetime
    revoked: bool = False


class TokenNotFoundError(Exception):
    """Raised by :meth:`TokenStore.revoke` when ``token_id`` has no record."""

    def __init__(self, token_id: str) -> None:
        self.token_id = token_id
        super().__init__(f"no token found with id {token_id!r}")


class TokenStore(ABC):
    """Persistence interface for agent tokens. Implementations own how/where state lives."""

    @abstractmethod
    def save(self, record: TokenRecord) -> None:
        """Persist ``record``, overwriting any existing record with the same ``token_hash``."""

    @abstractmethod
    def get_by_hash(self, token_hash: str) -> TokenRecord | None:
        """Return the record for ``token_hash``, or ``None`` if no token hashes to it."""

    @abstractmethod
    def revoke(self, token_id: str) -> None:
        """Mark the token identified by ``token_id`` as revoked.

        Raises :class:`TokenNotFoundError` if no record has that ``token_id``.
        """


class InMemoryTokenStore(TokenStore):
    """Reference ``TokenStore`` backed by a process-local dict, keyed by ``token_hash``.

    Plaintext tokens never pass through this class — callers only ever hand
    it a :class:`TokenRecord`, which already carries the hash, never the
    secret it was derived from.
    """

    def __init__(self) -> None:
        self._records: dict[str, TokenRecord] = {}

    def save(self, record: TokenRecord) -> None:
        self._records[record.token_hash] = record

    def get_by_hash(self, token_hash: str) -> TokenRecord | None:
        return self._records.get(token_hash)

    def revoke(self, token_id: str) -> None:
        for token_hash, record in self._records.items():
            if record.token_id == token_id:
                self._records[token_hash] = dataclasses.replace(record, revoked=True)
                return
        raise TokenNotFoundError(token_id)
