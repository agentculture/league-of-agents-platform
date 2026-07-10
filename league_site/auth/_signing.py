"""Shared HMAC-SHA256 signing/verification for compact auth tokens.

Both :mod:`league_site.auth.oauth` (the OAuth ``state`` CSRF token) and
:mod:`league_site.auth.sessions` (the human login session token) are the
same *shape* of thing: a small JSON payload, base64url-encoded, signed with
HMAC-SHA256, and verified with a constant-time comparison. Factoring that
one primitive out here means both token kinds share exactly one signing
implementation — a bug fixed here fixes both, and neither module has to
reimplement (or subtly diverge on) the crypto.

Token format: ``<base64url(json payload)>.<base64url(hmac digest)>``. The
payload is never encrypted, only signed — callers must not put secrets in
it, only claims that are safe to be readable by whoever holds the token.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from typing import Any

_TOKEN_SEP = "."  # nosec B105 - a token *separator* character, not a credential


class MissingSecretError(RuntimeError):
    """Raised when a required signing-secret environment variable is unset.

    Names the missing variable in the message so the operator (or a test
    that forgot to set it) knows exactly what to configure.
    """

    def __init__(self, env_var: str) -> None:
        self.env_var = env_var
        super().__init__(
            f"missing required environment variable {env_var!r} "
            "(a signing secret for league-of-agents-platform auth tokens)"
        )


def read_secret(env_var: str) -> bytes:
    """Read a signing secret from the environment.

    Raises :class:`MissingSecretError` naming *env_var* if it is unset or
    empty, rather than silently signing with an empty/predictable key.
    """
    value = os.environ.get(env_var)
    if not value:
        raise MissingSecretError(env_var)
    return value.encode("utf-8")


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def sign_payload(payload: dict[str, Any], secret: bytes) -> str:
    """Encode *payload* as a compact signed token: ``<b64 json>.<b64 hmac>``."""
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = _b64encode(body)
    signature = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}{_TOKEN_SEP}{_b64encode(signature)}"


def verify_payload(token: str, secret: bytes) -> dict[str, Any] | None:
    """Verify *token*'s HMAC signature and return its decoded payload.

    Returns ``None`` — never raises — for any malformed, tampered, or
    wrong-secret token, so callers can treat "invalid" uniformly regardless
    of which way the token is broken. The signature comparison is
    constant-time (:func:`hmac.compare_digest`) to avoid leaking timing
    information about how much of the signature matched.
    """
    if not token or token.count(_TOKEN_SEP) != 1:
        return None
    encoded, _, signature_b64 = token.partition(_TOKEN_SEP)
    expected = hmac.new(secret, encoded.encode("ascii"), hashlib.sha256).digest()
    try:
        actual = _b64decode(signature_b64)
    except (binascii.Error, ValueError):
        return None
    if not hmac.compare_digest(expected, actual):
        return None
    try:
        decoded = _b64decode(encoded)
        payload = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload
