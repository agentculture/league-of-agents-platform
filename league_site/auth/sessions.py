"""Stateless, signed session tokens for logged-in humans.

A session token is not a database key — it *is* the session, self-verifying
via HMAC-SHA256 (see :mod:`league_site.auth._signing`), so no server-side
session store is required. :func:`issue` mints a token from an
:class:`~league_site.auth.oauth.Identity`; :func:`verify` checks its
signature and expiry and returns the decoded :class:`Session`, or ``None``
for anything tampered, malformed, or expired — one uniform "not logged in"
outcome regardless of which way the token is invalid.

The signing secret comes from the ``LEAGUE_SESSION_SECRET`` environment
variable (see :data:`SESSION_SECRET_ENV`); a missing/empty value raises
:class:`~league_site.auth._signing.MissingSecretError`, which names the
variable.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from league_site.accounts.store import account_id_for
from league_site.auth._signing import read_secret, sign_payload, verify_payload

SESSION_SECRET_ENV = "LEAGUE_SESSION_SECRET"  # nosec B105 - an env var *name*, not a credential
DEFAULT_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days


@dataclass(frozen=True)
class Session:
    """A verified, decoded session — the identity a login session carries.

    ``subject`` and ``provider`` together are the durable identity key
    (e.g. a GitHub user id under provider ``"github"``); ``display`` is the
    human-readable name a UI shows. ``issued_at``/``expiry`` are Unix
    timestamps (seconds).
    """

    subject: str
    provider: str
    display: str
    issued_at: int
    expiry: int

    @property
    def account_id(self) -> str:
        """The :class:`~league_site.accounts.store.AccountStore` key this session resolves to.

        This is *by construction* identical to the id the OAuth callback
        upserts the account under: the callback builds both from the very same
        :class:`~league_site.auth.oauth.Identity` — the account via
        ``account_id_for(identity["provider"], identity["subject"])`` (see
        :func:`league_site.auth.wsgi._upsert_account`) and this session via
        ``provider=identity["provider"]``, ``subject=identity["subject"]``
        (see :func:`issue`). So ``account_store.get(session.account_id)`` always
        returns the account of the signed-in human — the one place that
        equality is asserted lives here, next to the fields it derives from.
        """
        return account_id_for(self.provider, self.subject)

    def is_expired(self, *, now: int | None = None) -> bool:
        """Return ``True`` once *now* (default: the current time) reaches ``expiry``."""
        current = int(time.time()) if now is None else now
        return current >= self.expiry

    def to_payload(self) -> dict[str, Any]:
        """The compact dict shape signed into the token (field order-independent)."""
        return {
            "subject": self.subject,
            "provider": self.provider,
            "display": self.display,
            "issued_at": self.issued_at,
            "expiry": self.expiry,
        }


def issue(
    identity: Mapping[str, Any],
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> str:
    """Issue a signed session token for *identity* (an :class:`~league_site.auth.oauth.Identity`).

    ``display`` prefers ``identity["display_name"]``, falling back to
    ``handle`` then ``subject`` so a session always carries *some*
    human-readable label even from a minimal identity dict.
    """
    issued_at = int(time.time()) if now is None else now
    display = str(identity.get("display_name") or identity.get("handle") or identity["subject"])
    session = Session(
        subject=str(identity["subject"]),
        provider=str(identity["provider"]),
        display=display,
        issued_at=issued_at,
        expiry=issued_at + ttl_seconds,
    )
    return sign_payload(session.to_payload(), read_secret(SESSION_SECRET_ENV))


def verify(token: str, *, now: int | None = None) -> Session | None:
    """Verify *token* and return its :class:`Session`, or ``None`` if invalid or expired."""
    payload = verify_payload(token, read_secret(SESSION_SECRET_ENV))
    if payload is None:
        return None
    try:
        session = Session(
            subject=str(payload["subject"]),
            provider=str(payload["provider"]),
            display=str(payload["display"]),
            issued_at=int(payload["issued_at"]),
            expiry=int(payload["expiry"]),
        )
    except (KeyError, TypeError, ValueError):
        return None
    if session.is_expired(now=now):
        return None
    return session
