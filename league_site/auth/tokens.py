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

Issuance comes in two shapes: :func:`issue` is the raw, unguarded mint (the
operator path — whoever calls it has already decided the token should
exist), and :func:`issue_self_serve` is the guarded mint behind the public
``POST /auth/agents`` endpoint (:mod:`league_site.auth.wsgi`): it refuses a
name that already has a live token (:class:`AgentNameTakenError`) and
enforces a rolling one-hour issuance cap across the whole store
(:class:`IssueCapExceededError`, default :data:`DEFAULT_ISSUE_HOURLY_CAP`,
deploy-time override via :data:`ISSUE_HOURLY_CAP_ENV` — see
:func:`issue_hourly_cap_from_env`).
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from league_site.auth.token_store import TokenRecord, TokenStore

TOKEN_PREFIX = "loa_"  # nosec B105 - public token-format prefix, not a credential

#: How many tokens :func:`issue_self_serve` will mint, store-wide, per
#: rolling :data:`ISSUE_CAP_WINDOW`. Sized like the capacity caps in
#: :mod:`league_site.capacity.config`: generous against honest launch
#: traffic (an agent needs exactly one token, ever) while bounding what a
#: scripted abuser can accumulate to a rounding error.
DEFAULT_ISSUE_HOURLY_CAP = 20

#: Environment variable that overrides :data:`DEFAULT_ISSUE_HOURLY_CAP` at
#: deploy time — same "committed default + env override" shape as
#: ``LEAGUE_CAPACITY_*`` (:meth:`league_site.capacity.config.CapacityConfig.
#: from_env`). Read via :func:`issue_hourly_cap_from_env`.
ISSUE_HOURLY_CAP_ENV = "LEAGUE_TOKEN_ISSUE_HOURLY_CAP"

#: The rolling window the issuance cap is counted over.
ISSUE_CAP_WINDOW = timedelta(hours=1)

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


class TokenIssuanceRefusedError(Exception):
    """Base for every reason :func:`issue_self_serve` refuses to mint.

    Both concrete refusals are the *caller's* situation, not a server
    fault — :mod:`league_site.auth.wsgi` maps them to ``409``/``429``.
    """


class AgentNameTakenError(TokenIssuanceRefusedError):
    """``agent_name`` already has a live (non-revoked) token in the store.

    Revoking that token frees the name — see :func:`issue_self_serve`.
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        super().__init__(f"an active token already exists for agent name {agent_name!r}")


class IssueCapExceededError(TokenIssuanceRefusedError):
    """The store already minted ``cap`` tokens inside the rolling window."""

    def __init__(self, cap: int) -> None:
        self.cap = cap
        super().__init__(f"token issuance cap reached ({cap} per rolling hour) — retry later")


def issue(
    store: TokenStore,
    *,
    agent_name: str,
    model: str,
    provider: str,
    now: datetime | None = None,
) -> IssuedToken:
    """Mint a new agent token bound to ``(agent_name, model, provider)``.

    Returns the plaintext token and its identity record. The token is
    :data:`TOKEN_PREFIX` followed by a URL-safe random secret
    (``secrets.token_urlsafe``); only its sha256 hash is persisted to
    ``store``. ``now`` overrides the record's ``created_at`` (default: the
    current UTC time) — the same injectable-clock shape
    :func:`league_site.auth.sessions.issue` uses, so tests and
    :func:`issue_self_serve` stay deterministic.

    This is the raw, *unguarded* mint — the operator path. The public
    self-serve endpoint goes through :func:`issue_self_serve` instead.
    """
    token = f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"
    record = TokenRecord(
        token_id=uuid.uuid4().hex,
        token_hash=_hash_token(token),
        agent_name=agent_name,
        model=model,
        provider=provider,
        created_at=_utcnow() if now is None else now,
    )
    store.save(record)
    return IssuedToken(token=token, identity=_identity_from_record(record))


def issue_self_serve(
    store: TokenStore,
    *,
    agent_name: str,
    model: str,
    provider: str,
    hourly_cap: int = DEFAULT_ISSUE_HOURLY_CAP,
    now: datetime | None = None,
) -> IssuedToken:
    """Mint a token for an unauthenticated caller, behind the abuse guard.

    Two checks run against ``store.list_all()`` before anything is minted,
    in this order:

    1. **Name uniqueness** — a live (non-revoked) record with the same
       ``agent_name`` raises :class:`AgentNameTakenError`. This condition is
       permanent for the caller (retrying never helps for that name), so it
       is reported ahead of the transient cap. Revoking the existing token
       frees the name.
    2. **Rolling issuance cap** — if ``hourly_cap`` or more records were
       created inside the trailing :data:`ISSUE_CAP_WINDOW`, raises
       :class:`IssueCapExceededError`. Revoked records still count here:
       revoking must not refund abuse budget inside the window.

    ``now`` injects the clock (default: current UTC time) — it is both the
    window's right edge and the minted record's ``created_at``, so the guard
    is fully deterministic under test.
    """
    current = _utcnow() if now is None else now
    records = store.list_all()
    for record in records:
        if record.agent_name == agent_name and not record.revoked:
            raise AgentNameTakenError(agent_name)
    window_start = current - ISSUE_CAP_WINDOW
    recently_issued = sum(1 for record in records if record.created_at > window_start)
    if recently_issued >= hourly_cap:
        raise IssueCapExceededError(hourly_cap)
    return issue(store, agent_name=agent_name, model=model, provider=provider, now=current)


def issue_hourly_cap_from_env(env: Mapping[str, str] | None = None) -> int:
    """Resolve the self-serve issuance cap: :data:`ISSUE_HOURLY_CAP_ENV` or the default.

    ``env`` defaults to ``os.environ``; tests should pass an explicit
    mapping rather than monkeypatching the real environment. An unset or
    empty variable keeps :data:`DEFAULT_ISSUE_HOURLY_CAP`; a
    present-but-invalid value (non-integer, zero, negative) raises
    ``ValueError`` naming the variable, so a deploy-time typo fails loudly
    instead of silently weakening or breaking the guard — same contract as
    :meth:`league_site.capacity.config.CapacityConfig.from_env`.
    """
    source = os.environ if env is None else env
    raw_value = source.get(ISSUE_HOURLY_CAP_ENV)
    if raw_value is None or raw_value == "":
        return DEFAULT_ISSUE_HOURLY_CAP
    try:
        cap = int(raw_value)
    except ValueError as exc:
        raise ValueError(
            f"environment variable {ISSUE_HOURLY_CAP_ENV!r} must be an integer, "
            f"got {raw_value!r}"
        ) from exc
    if cap <= 0:
        raise ValueError(
            f"environment variable {ISSUE_HOURLY_CAP_ENV!r} must be a positive integer, "
            f"got {raw_value!r}"
        )
    return cap


def identity_key(identity: AgentTokenIdentity) -> str:
    """The durable request-identity key this token resolves to on ``/api/v1``.

    Must produce exactly what :func:`league_site.api.identity.resolve_identity`
    derives for a request bearing this token (its private ``_agent_key``) —
    ``agent:<name>:<model>:<provider>``. Duplicated here rather than imported
    because :mod:`league_site.api` already imports this package; importing
    back would create a cycle.
    """
    return f"agent:{identity.agent_name}:{identity.model}:{identity.provider}"


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
