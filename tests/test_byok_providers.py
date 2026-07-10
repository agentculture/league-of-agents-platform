"""Tests for league_site.byok.providers: the provider registry + uniform complete().

Covers the task's acceptance criteria:

* A completion works via three distinct provider wire shapes with stubbed
  transports: anthropic-shaped, openai-shaped, and an openai-compatible
  local stub (h12/h22) covering the hf-inference / nvidia-nim presets.
* No provider path ever reads an operator-owned environment variable.
* Everything goes through the one injectable Transport — default_transport
  (urllib) is never exercised by these tests.
"""

from __future__ import annotations

import json

import pytest

from league_site.byok import providers
from league_site.byok.providers import (
    PROVIDERS,
    ProviderError,
    TransportRequest,
    TransportResponse,
    UnknownProviderError,
    complete,
)
from league_site.byok.vault import InMemoryKeyVault, SecretKey

API_KEY = "sk-test-key-123"  # nosec B105 - test fixture


def _messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "You are terse."},
        {"role": "user", "content": "say hi"},
    ]


class RecordingTransport:
    """A stub Transport that records the request it received and returns a canned response."""

    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.requests: list[TransportRequest] = []

    def __call__(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        return self.response


def _json_response(status: int, payload: dict) -> TransportResponse:
    return TransportResponse(status=status, body=json.dumps(payload).encode("utf-8"))


# --- h12: anthropic-shaped -------------------------------------------------------


def test_complete_via_anthropic_shaped_stub_transport() -> None:
    transport = RecordingTransport(
        _json_response(200, {"content": [{"type": "text", "text": "hello from anthropic"}]})
    )

    text = complete(
        "anthropic", SecretKey(API_KEY), "claude-sonnet-5", _messages(), transport=transport
    )

    assert text == "hello from anthropic"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == API_KEY
    body = json.loads(request.body)
    assert body["system"] == "You are terse."
    assert body["messages"] == [{"role": "user", "content": "say hi"}]


def test_anthropic_concatenates_multiple_text_blocks() -> None:
    transport = RecordingTransport(
        _json_response(
            200,
            {
                "content": [
                    {"type": "text", "text": "part one "},
                    {"type": "tool_use", "id": "x", "name": "noop"},
                    {"type": "text", "text": "part two"},
                ]
            },
        )
    )

    text = complete(
        "anthropic", SecretKey(API_KEY), "claude-sonnet-5", _messages(), transport=transport
    )

    assert text == "part one part two"


# --- h12: openai-shaped -----------------------------------------------------------


def test_complete_via_openai_shaped_stub_transport() -> None:
    transport = RecordingTransport(
        _json_response(
            200, {"choices": [{"message": {"role": "assistant", "content": "hello from openai"}}]}
        )
    )

    text = complete("openai", SecretKey(API_KEY), "gpt-4o", _messages(), transport=transport)

    assert text == "hello from openai"
    request = transport.requests[0]
    assert request.url == "https://api.openai.com/v1/chat/completions"
    assert request.headers["authorization"] == f"Bearer {API_KEY}"
    body = json.loads(request.body)
    assert body["messages"][0] == {"role": "system", "content": "You are terse."}


def test_openai_shaped_response_with_no_choices_returns_empty_string() -> None:
    transport = RecordingTransport(_json_response(200, {"choices": []}))

    text = complete("openai", SecretKey(API_KEY), "gpt-4o", _messages(), transport=transport)

    assert text == ""


# --- h22: openai-compatible local stub (covers hf-inference / nvidia-nim) --------


def test_complete_via_openai_compatible_local_stub_transport() -> None:
    transport = RecordingTransport(
        _json_response(200, {"choices": [{"message": {"content": "hello from local stub"}}]})
    )

    text = complete(
        "openai-compatible",
        SecretKey(API_KEY),
        "local-model",
        _messages(),
        transport=transport,
        base_url="http://localhost:8000/v1",
    )

    assert text == "hello from local stub"
    assert transport.requests[0].url == "http://localhost:8000/v1/chat/completions"


def test_openai_compatible_requires_base_url() -> None:
    transport = RecordingTransport(_json_response(200, {"choices": []}))

    with pytest.raises(ValueError, match="base_url"):
        complete(
            "openai-compatible", SecretKey(API_KEY), "local-model", _messages(), transport=transport
        )


def test_hf_inference_preset_uses_its_documented_default_base_url() -> None:
    transport = RecordingTransport(
        _json_response(200, {"choices": [{"message": {"content": "hi"}}]})
    )

    complete("hf-inference", SecretKey(API_KEY), "some/model", _messages(), transport=transport)

    assert transport.requests[0].url == providers.HF_INFERENCE_BASE_URL + "/chat/completions"


def test_nvidia_nim_preset_uses_its_documented_default_base_url() -> None:
    transport = RecordingTransport(
        _json_response(200, {"choices": [{"message": {"content": "hi"}}]})
    )

    complete("nvidia-nim", SecretKey(API_KEY), "some/model", _messages(), transport=transport)

    assert transport.requests[0].url == providers.NVIDIA_NIM_BASE_URL + "/chat/completions"


def test_hf_inference_base_url_is_overridable() -> None:
    transport = RecordingTransport(
        _json_response(200, {"choices": [{"message": {"content": "hi"}}]})
    )

    complete(
        "hf-inference",
        SecretKey(API_KEY),
        "some/model",
        _messages(),
        transport=transport,
        base_url="https://my-dedicated-endpoint.example/v1",
    )

    assert transport.requests[0].url == "https://my-dedicated-endpoint.example/v1/chat/completions"


# --- bedrock (sigv4-signed) --------------------------------------------------------


def test_complete_via_bedrock_signs_with_the_users_credentials_from_the_vault() -> None:
    credentials = json.dumps(
        {
            "access_key_id": "AKIDEXAMPLE",
            "secret_access_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLE",
            "region": "us-east-1",
        }
    )
    transport = RecordingTransport(
        _json_response(200, {"content": [{"type": "text", "text": "hello from bedrock"}]})
    )

    text = complete(
        "bedrock",
        SecretKey(credentials),
        "anthropic.claude-3-sonnet-20240229-v1:0",
        _messages(),
        transport=transport,
    )

    assert text == "hello from bedrock"
    request = transport.requests[0]
    assert request.url == (
        "https://bedrock-runtime.us-east-1.amazonaws.com/"
        "model/anthropic.claude-3-sonnet-20240229-v1:0/invoke"
    )
    assert request.headers["authorization"].startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    # never the operator's/anyone else's key material anywhere on the wire
    assert "wJalrXUtnFEMI" not in json.dumps(request.headers)
    assert "wJalrXUtnFEMI" not in request.body.decode("utf-8")


def test_bedrock_region_option_overrides_the_vaulted_credentials_region() -> None:
    credentials = json.dumps(
        {"access_key_id": "AKIDEXAMPLE", "secret_access_key": "secret", "region": "us-east-1"}
    )
    transport = RecordingTransport(
        _json_response(200, {"content": [{"type": "text", "text": "hi"}]})
    )

    complete(
        "bedrock",
        SecretKey(credentials),
        "some-model",
        _messages(),
        transport=transport,
        region="eu-west-1",
    )

    assert "eu-west-1" in transport.requests[0].url


# --- registry / dispatch -----------------------------------------------------------


def test_unknown_provider_raises() -> None:
    with pytest.raises(UnknownProviderError):
        complete("carrier-pigeon", SecretKey(API_KEY), "model", _messages())


def test_provider_error_response_status_raises_provider_error() -> None:
    transport = RecordingTransport(TransportResponse(status=401, body=b'{"error":"unauthorized"}'))

    with pytest.raises(ProviderError) as excinfo:
        complete("openai", SecretKey(API_KEY), "gpt-4o", _messages(), transport=transport)

    assert excinfo.value.status == 401
    assert excinfo.value.provider == "openai"


def test_all_documented_providers_are_registered() -> None:
    assert set(PROVIDERS) == {
        "anthropic",
        "openai",
        "openai-compatible",
        "hf-inference",
        "nvidia-nim",
        "bedrock",
    }


# --- handle_or_key resolution: string handle requires an explicit vault ----------


def test_complete_resolves_a_string_handle_via_the_supplied_vault() -> None:
    vault = InMemoryKeyVault()
    handle = vault.put("player-1", "openai", API_KEY)
    transport = RecordingTransport(
        _json_response(200, {"choices": [{"message": {"content": "hi"}}]})
    )

    text = complete("openai", handle, "gpt-4o", _messages(), vault=vault, transport=transport)

    assert text == "hi"
    assert transport.requests[0].headers["authorization"] == f"Bearer {API_KEY}"


def test_complete_with_a_string_handle_and_no_vault_raises() -> None:
    transport = RecordingTransport(_json_response(200, {"choices": []}))

    with pytest.raises(ValueError, match="vault"):
        complete("openai", "byok_some_handle", "gpt-4o", _messages(), transport=transport)


def test_complete_rejects_a_non_secret_non_string_handle() -> None:
    with pytest.raises(TypeError):
        complete("openai", 12345, "gpt-4o", _messages())  # type: ignore[arg-type]


# --- default_transport: never actually hit the network, but prove it's the fallback --


def test_omitting_transport_falls_back_to_default_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No transport= means default_transport is used - proven by monkeypatching it, never by
    making a real network call (which these tests must never do)."""
    stub = RecordingTransport(_json_response(200, {"choices": [{"message": {"content": "hi"}}]}))
    monkeypatch.setattr(providers, "default_transport", stub)

    text = complete("openai", SecretKey(API_KEY), "gpt-4o", _messages())

    assert text == "hi"
    assert len(stub.requests) == 1
