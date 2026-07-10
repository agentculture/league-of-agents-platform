"""Tests for league_site.byok.sigv4: a stdlib-only AWS Signature Version 4 signer.

Covers the task's acceptance criterion: a sigv4 signature test against a
fixed-input golden signature computed by this implementation and asserted
stable (the task spec's explicitly-offered alternative to a hand-copied
external AWS test-suite vector, which risks silent transcription errors
with no way to verify against real AWS in this offline test suite).

Fixed inputs use the same access-key-id/secret-access-key pair AWS uses in
its own published SigV4 documentation examples (``AKIDEXAMPLE`` /
``wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLE``) so the test reads naturally
against that documentation, but the asserted signature values below are
this module's own computed output, not a transcription of AWS's
published hex digests.
"""

from __future__ import annotations

import hashlib
import hmac

from league_site.byok import sigv4

ACCESS_KEY = "AKIDEXAMPLE"
SECRET_KEY = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLE"


# --- golden, fixed-input signature stability -----------------------------------


def test_golden_signature_get_request_empty_body_is_stable() -> None:
    headers = sigv4.sign_request(
        method="GET",
        url="https://example.amazonaws.com/",
        body=b"",
        region="us-east-1",
        service="service",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        amz_date="20150830T123600Z",
    )

    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 "
        "Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
        "SignedHeaders=host;x-amz-content-sha256;x-amz-date, "
        "Signature=99e0cd0478353051f16374b956161fdf62b499264499f4193e204617d2352f0c"
    )
    # empty-body payload hash is the well-known SHA256("") constant, not a magic number
    assert headers["x-amz-content-sha256"] == hashlib.sha256(b"").hexdigest()
    assert headers["host"] == "example.amazonaws.com"
    assert headers["x-amz-date"] == "20150830T123600Z"


def test_golden_signature_bedrock_invoke_shaped_post_is_stable() -> None:
    body = (
        b'{"anthropic_version":"bedrock-2023-05-31","max_tokens":256,'
        b'"messages":[{"role":"user","content":"hi"}]}'
    )
    headers = sigv4.sign_request(
        method="POST",
        url=(
            "https://bedrock-runtime.us-east-1.amazonaws.com/"
            "model/anthropic.claude-3-sonnet-20240229-v1:0/invoke"
        ),
        body=body,
        region="us-east-1",
        service="bedrock",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        amz_date="20250101T000000Z",
        extra_headers={"content-type": "application/json"},
    )

    assert headers["authorization"] == (
        "AWS4-HMAC-SHA256 "
        "Credential=AKIDEXAMPLE/20250101/us-east-1/bedrock/aws4_request, "
        "SignedHeaders=content-type;host;x-amz-content-sha256;x-amz-date, "
        "Signature=d6768e8e24dc4eaf17e2fd50a45c63c5cf2f98162238ff8c761f2b71e27254bf"
    )
    assert headers["x-amz-content-sha256"] == hashlib.sha256(body).hexdigest()


def test_signing_is_deterministic_for_identical_inputs() -> None:
    kwargs = dict(
        method="GET",
        url="https://example.amazonaws.com/",
        body=b"",
        region="us-east-1",
        service="service",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        amz_date="20150830T123600Z",
    )

    assert sigv4.sign_request(**kwargs) == sigv4.sign_request(**kwargs)


def test_signing_changes_when_the_secret_changes() -> None:
    kwargs = dict(
        method="GET",
        url="https://example.amazonaws.com/",
        body=b"",
        region="us-east-1",
        service="service",
        access_key=ACCESS_KEY,
        amz_date="20150830T123600Z",
    )

    signature_a = sigv4.sign_request(secret_key=SECRET_KEY, **kwargs)["authorization"]
    signature_b = sigv4.sign_request(secret_key="a-totally-different-secret", **kwargs)[
        "authorization"
    ]

    assert signature_a != signature_b


# --- component correctness ------------------------------------------------------


def test_sha256_hex_matches_hashlib() -> None:
    assert sigv4.sha256_hex(b"hello world") == hashlib.sha256(b"hello world").hexdigest()


def test_signing_key_matches_the_documented_four_step_hmac_chain() -> None:
    date_stamp, region, service = "20150830", "us-east-1", "service"

    expected_k_date = hmac.new(
        ("AWS4" + SECRET_KEY).encode(), date_stamp.encode(), hashlib.sha256
    ).digest()
    expected_k_region = hmac.new(expected_k_date, region.encode(), hashlib.sha256).digest()
    expected_k_service = hmac.new(expected_k_region, service.encode(), hashlib.sha256).digest()
    expected_k_signing = hmac.new(expected_k_service, b"aws4_request", hashlib.sha256).digest()

    assert sigv4.signing_key(SECRET_KEY, date_stamp, region, service) == expected_k_signing


def test_canonical_request_sorts_headers_and_joins_with_newlines() -> None:
    canonical, signed_headers = sigv4.canonical_request(
        "get",
        "/",
        "",
        {"X-Amz-Date": "20150830T123600Z", "Host": "example.amazonaws.com"},
        hashlib.sha256(b"").hexdigest(),
    )

    assert signed_headers == "host;x-amz-date"
    assert canonical == (
        "GET\n"
        "/\n"
        "\n"
        "host:example.amazonaws.com\n"
        "x-amz-date:20150830T123600Z\n"
        "\n"
        "host;x-amz-date\n" + hashlib.sha256(b"").hexdigest()
    )


def test_canonical_uri_percent_encodes_reserved_characters_in_each_segment() -> None:
    canonical, _ = sigv4.canonical_request(
        "POST", "/model/anthropic.claude:v1/invoke", "", {"host": "h"}, "deadbeef"
    )
    assert "/model/anthropic.claude%3Av1/invoke" in canonical


def test_canonical_query_sorts_and_encodes_pairs() -> None:
    assert sigv4._canonical_query("b=2&a=1") == "a=1&b=2"
    assert sigv4._canonical_query("") == ""


def test_canonical_query_skips_empty_segments_from_stray_ampersands() -> None:
    assert sigv4._canonical_query("a=1&&b=2") == "a=1&b=2"


def test_canonical_uri_defaults_to_root_for_an_empty_path() -> None:
    assert sigv4._canonical_uri("") == "/"


def test_string_to_sign_shape() -> None:
    result = sigv4.string_to_sign(
        "20150830T123600Z", "20150830/us-east-1/service/aws4_request", "canonical"
    )
    lines = result.split("\n")
    assert lines[0] == "AWS4-HMAC-SHA256"
    assert lines[1] == "20150830T123600Z"
    assert lines[2] == "20150830/us-east-1/service/aws4_request"
    assert lines[3] == hashlib.sha256(b"canonical").hexdigest()


def test_sign_request_includes_session_token_header_when_provided() -> None:
    headers = sigv4.sign_request(
        method="POST",
        url="https://bedrock-runtime.us-east-1.amazonaws.com/model/m/invoke",
        body=b"{}",
        region="us-east-1",
        service="bedrock",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        session_token="a-temporary-session-token",
        amz_date="20250101T000000Z",
    )
    assert headers["x-amz-security-token"] == "a-temporary-session-token"
    assert "x-amz-security-token" in headers["authorization"]
