"""AWS Signature Version 4 request signing — stdlib ``hmac``/``hashlib`` only.

Implements the SigV4 algorithm exactly as documented by AWS ("Create a
signed AWS API request", *Signature Version 4 signing process*): a
canonical request, a string-to-sign, a derived signing key via a chain of
four HMACs, and a final HMAC-SHA256 signature folded into an
``Authorization`` header.

This exists so :mod:`league_site.byok.providers` can call Amazon Bedrock's
``InvokeModel`` API using the *user's* BYOK-vault-supplied access key and
secret, without depending on ``boto3`` (which only :mod:`league_site.byok.
aws_vault` — the KMS/DynamoDB adapter — is allowed to import). SigV4 is
just HMAC math over a well-defined string; no AWS SDK is required to
produce it.

Every function here is pure and side-effect-free (given a fixed
``amz_date`` there is no wall-clock dependency), which is what makes
:func:`sign_request` golden-testable: the same inputs always produce the
same signature.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

ALGORITHM = "AWS4-HMAC-SHA256"


def _hmac_digest(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sha256_hex(data: bytes) -> str:
    """Hex-encoded SHA-256 digest of ``data`` — used as SigV4's ``HashedPayload``."""
    return hashlib.sha256(data).hexdigest()


def _canonical_uri(path: str) -> str:
    """URI-encode each path segment per SigV4 rules, preserving ``/`` separators."""
    if not path:
        return "/"
    segments = path.split("/")
    return "/".join(quote(segment, safe="-_.~") for segment in segments)


def _canonical_query(query: str) -> str:
    """Sort query params by (encoded key, encoded value) and re-join, SigV4-style."""
    if not query:
        return ""
    pairs = []
    for part in query.split("&"):
        if not part:
            continue
        key, _, value = part.partition("=")
        pairs.append((quote(key, safe="-_.~"), quote(value, safe="-_.~")))
    pairs.sort()
    return "&".join(f"{k}={v}" for k, v in pairs)


def canonical_request(
    method: str,
    path: str,
    query: str,
    headers: dict[str, str],
    payload_hash: str,
) -> tuple[str, str]:
    """Build SigV4's ``CanonicalRequest`` string.

    ``headers`` maps lowercase header name to raw value; every header
    passed in is included in ``SignedHeaders`` (callers decide what to
    sign by what they include). Returns ``(canonical_request, signed_headers)``.
    """
    sorted_items = sorted(
        (name.lower(), " ".join(value.split())) for name, value in headers.items()
    )
    canonical_headers = "".join(f"{name}:{value}\n" for name, value in sorted_items)
    signed_headers = ";".join(name for name, _ in sorted_items)
    parts = [
        method.upper(),
        _canonical_uri(path),
        _canonical_query(query),
        canonical_headers,
        signed_headers,
        payload_hash,
    ]
    return "\n".join(parts), signed_headers


def string_to_sign(amz_date: str, credential_scope: str, canonical_request_str: str) -> str:
    """Build SigV4's ``StringToSign``."""
    return "\n".join(
        [
            ALGORITHM,
            amz_date,
            credential_scope,
            sha256_hex(canonical_request_str.encode("utf-8")),
        ]
    )


def signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    """Derive the SigV4 signing key via the documented four-step HMAC chain."""
    k_date = _hmac_digest(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    k_region = _hmac_digest(k_date, region)
    k_service = _hmac_digest(k_region, service)
    return _hmac_digest(k_service, "aws4_request")


def sign_request(
    *,
    method: str,
    url: str,
    body: bytes,
    region: str,
    service: str,
    access_key: str,
    secret_key: str,
    session_token: str | None = None,
    amz_date: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return the full header set (including ``Authorization``) for one signed request.

    ``amz_date`` — ``%Y%m%dT%H%M%SZ`` UTC — is injectable for deterministic
    tests; it defaults to the current time. ``access_key``/``secret_key``
    are the *user's* Bedrock-capable AWS credentials resolved from the BYOK
    vault, never an operator-owned credential.
    """
    parsed = urlsplit(url)
    host = parsed.netloc
    path = parsed.path or "/"
    query = parsed.query
    now = amz_date or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now[:8]
    payload_hash = sha256_hex(body)

    headers = {"host": host, "x-amz-date": now, "x-amz-content-sha256": payload_hash}
    if session_token:
        headers["x-amz-security-token"] = session_token
    if extra_headers:
        headers.update({name.lower(): value for name, value in extra_headers.items()})

    canonical_request_str, signed_headers = canonical_request(
        method, path, query, headers, payload_hash
    )
    credential_scope = f"{date_stamp}/{region}/{service}/aws4_request"
    sts = string_to_sign(now, credential_scope, canonical_request_str)
    key = signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(key, sts.encode("utf-8"), hashlib.sha256).hexdigest()

    headers["authorization"] = (
        f"{ALGORITHM} Credential={access_key}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, Signature={signature}"
    )
    return headers
