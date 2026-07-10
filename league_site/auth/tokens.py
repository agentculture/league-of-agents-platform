"""Agent token issuance, verification, and revocation.

Agent identity on the platform is a bearer token, not a session: an operator
issues a token bound to an agent's benchmark identity — name, model,
provider, see :class:`AgentTokenIdentity` — the agent presents it as
``Authorization: Bearer loa_...`` on every request (:func:`parse_bearer_token`
extracts the token from that header), and :func:`verify` resolves it back to
the identity a rated-match authorization hook trusts.

Only a sha256 hash of the token is ever persisted
(:mod:`league_site.auth.token_store`) or logged — the plaintext is returned
exactly once, at :func:`issue` time, and is never reconstructible
afterwards. :func:`verify` compares hashes with ``hmac.compare_digest`` so a
timing side-channel can't be used to fish for a valid hash.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from league_site.auth.token_store import TokenRecord, TokenStore

TOKEN_PREFIX = "loa_"

_BEARER_PREFIX = "bearer "


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    """sha256 hex digest of ``token``. Never store or log the token itself — only this."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AgentTokenIdentity:
    """The benchmark identity carried by an agent token.

    ``agent_name``/``model``/``provider`` are the fields a rated-match
    authorization hook (and the leaderboard) needs. ``token_id`` and
    ``created_at`` identify *this* token, e.g. for :func:`revoke` or
    auditing. ``revoked`` is always ``False`` on a value returned by
    :func:`verify` — a revoked token verifies as ``None`` instead, so
    callers never see a ``True`` here from that path; it only reflects the
    token's state at :func:`issue` time.
    """

    token_id: str
    agent_name: str
    model: str
    provider: str
    created_at: datetime
    revoked: bool = False


def _identity_from_record(record: TokenRecord) -> AgentTokenIdentity:
    return AgentTokenIdentity(
        token_id=record.token_id,
        agent_name=record.agent_name,
        model=record.model,
        provider=record.provider,
        created_at=record.created_at,
        revoked=record.revoked,
    )


@dataclass(frozen=True)
class IssuedToken:
    """Returned once, at :func:`issue` time.

    ``token`` is the plaintext bearer secret — this is the only place it is
    ever available. Callers must hand it to the agent immediately and
    discard it; every later lookup (:func:`verify`, :func:`revoke`) goes
    through the hash held in :mod:`league_site.auth.token_store`.
    """

    token: str
    identity: AgentTokenIdentity


def issue(store: TokenStore, *, agent_name: str, model: str, provider: str) -> IssuedToken:
    """Mint a new agent token bound to ``(agent_name, model, provider)``.

    Returns the plaintext token and its identity record. The token is
    :data:`TOKEN_PREFIX` followed by a URL-safe random secret
    (``secrets.token_urlsafe``); only its sha256 hash is persisted to
    ``store``.
    """
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    record = TokenRecord(
        token_id=uuid.uuid4().hex,
        token_hash=_hash_token(token),
        agent_name=agent_name,
        model=model,
        provider=provider,
        created_at=_utcnow(),
    )
    store.save(record)
    return IssuedToken(token=token, identity=_identity_from_record(record))


def verify(store: TokenStore, token: str | None) -> AgentTokenIdentity | None:
    """Resolve a bearer token to its agent identity, or ``None`` if invalid.

    ``None`` covers every failure mode uniformly — a falsy ``token``, a
    token nothing was ever issued for, or a revoked token — so callers (a
    rated-match authorization hook) never need to distinguish "absent" from
    "revoked" from "never issued". The stored hash is compared against the
    candidate's hash with ``hmac.compare_digest`` for constant-time
    comparison.
    """
    if not token:
        return None
    candidate_hash = _hash_token(token)
    record = store.get_by_hash(candidate_hash)
    if record is None:
        return None
    if not hmac.compare_digest(record.token_hash, candidate_hash):
        return None
    if record.revoked:
        return None
    return _identity_from_record(record)


def revoke(store: TokenStore, token_id: str) -> None:
    """Revoke the token identified by ``token_id``.

    Future :func:`verify` calls for that token return ``None``. Raises
    :class:`~league_site.auth.token_store.TokenNotFoundError` if no token has
    that id.
    """
    store.revoke(token_id)


def parse_bearer_token(header_value: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header value.

    Returns ``None`` — never raises — for anything that isn't a well-formed
    bearer header: absent, ``None``, a non-string value, the wrong scheme, or
    an empty token. The ``Bearer`` scheme name is matched case-insensitively;
    surrounding whitespace around the token is tolerated.
    """
    if not isinstance(header_value, str) or not header_value:
        return None
    if not header_value.lower().startswith(_BEARER_PREFIX):
        return None
    token = header_value[len(_BEARER_PREFIX) :].strip()
    return token or None
