"""Vendor-neutral LLM provider registry — one injectable transport, uniform call shape.

Every provider this module knows about — Anthropic, OpenAI, Amazon
Bedrock, Hugging Face Inference, NVIDIA NIM, and arbitrary
OpenAI-compatible endpoints — is reached through exactly one raw-HTTP
choke point: the :data:`Transport` callable. :func:`default_transport` (the
only place ``urllib.request`` is used in this package) is the default, but
every call site accepts an injected ``transport`` so tests never make a
real network call and production call sites can swap in retries/timeouts/
observability without touching provider logic.

This module never imports ``boto3`` — the Bedrock provider signs requests
itself with :mod:`league_site.byok.sigv4` (stdlib ``hmac``/``hashlib``
only). ``boto3`` is confined to :mod:`league_site.byok.aws_vault`, the KMS/
DynamoDB storage adapter, which is a completely separate concern from
*calling* a provider.

Uniform call shape: :func:`complete` always takes
``(provider, handle_or_key, model, messages)`` plus keyword-only options,
and always returns the model's reply text. ``handle_or_key`` is either an
already-resolved :class:`~league_site.byok.vault.SecretKey` (the common
case — see :mod:`league_site.byok.runner`, which resolves the vault handle
itself before calling here) or a raw vault handle string, in which case a
``vault=`` must also be passed so it can be resolved here. Either way, no
provider implementation in this module ever reads an operator-owned
environment variable (``ANTHROPIC_API_KEY`` and friends) — key material
only ever arrives via this explicit parameter.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from league_site.byok import sigv4
from league_site.byok.vault import KeyVault, SecretKey

# Documented default base URLs for the OpenAI-compatible presets. Callers can
# override either via `base_url=` on `complete()`.
HF_INFERENCE_BASE_URL = "https://router.huggingface.co/v1"
NVIDIA_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"

DEFAULT_MAX_TOKENS = 1024


class ProviderError(Exception):
    """Raised when a provider's HTTP response indicates failure (status >= 400)."""

    def __init__(self, provider: str, status: int, body: bytes) -> None:
        self.provider = provider
        self.status = status
        self.body = body
        super().__init__(f"{provider} request failed with status {status}: {body[:500]!r}")


class UnknownProviderError(Exception):
    """Raised by :func:`complete` for a provider name not in :data:`PROVIDERS`."""

    def __init__(self, provider: str) -> None:
        self.provider = provider
        super().__init__(f"unknown provider {provider!r}; known providers: {sorted(PROVIDERS)}")


@dataclass(frozen=True)
class TransportRequest:
    """One outbound HTTP request, fully built and ready to send."""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes


@dataclass(frozen=True)
class TransportResponse:
    """One inbound HTTP response."""

    status: int
    body: bytes


Transport = Callable[[TransportRequest], TransportResponse]


def default_transport(request: TransportRequest) -> TransportResponse:
    """The only place :mod:`urllib.request` is used in this package.

    A thin, dependency-free HTTP client for provider calls. Tests never
    exercise this function directly — they inject a stub :data:`Transport`
    instead — so this is the sole code path that ever makes a real network
    call.
    """
    urllib_request = urllib.request.Request(
        request.url, data=request.body, headers=request.headers, method=request.method
    )
    try:
        with urllib.request.urlopen(urllib_request, timeout=60) as response:  # nosec B310
            return TransportResponse(status=response.status, body=response.read())
    except urllib.error.HTTPError as exc:
        return TransportResponse(status=exc.code, body=exc.read())


def _split_system(messages: list[dict[str, str]]) -> tuple[str, list[dict[str, str]]]:
    """Split ``system``-role messages out from the rest, joined into one string.

    Anthropic (and Bedrock's Anthropic-family models) take ``system`` as a
    top-level field rather than an inline message; OpenAI-shaped APIs keep
    it inline. Callers that need it inline just don't call this helper.
    """
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    rest = [m for m in messages if m.get("role") != "system"]
    return "\n\n".join(system_parts), rest


@dataclass(frozen=True)
class ProviderSpec:
    """One entry in the provider registry: how to build a request and parse a reply."""

    name: str
    build_request: Callable[
        [SecretKey, str, list[dict[str, str]], dict[str, Any]], TransportRequest
    ]
    parse_response: Callable[[TransportResponse], str]


# --- anthropic ---------------------------------------------------------------


def _anthropic_build_request(
    key: SecretKey, model: str, messages: list[dict[str, str]], options: dict[str, Any]
) -> TransportRequest:
    system_text, chat_messages = _split_system(messages)
    payload: dict[str, Any] = {
        "model": model,
        "max_tokens": options.get("max_tokens", DEFAULT_MAX_TOKENS),
        "messages": [{"role": m["role"], "content": m["content"]} for m in chat_messages],
    }
    if system_text:
        payload["system"] = system_text
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "content-type": "application/json",
        "x-api-key": key.reveal(),
        "anthropic-version": "2023-06-01",
    }
    url = options.get("base_url") or "https://api.anthropic.com/v1/messages"
    return TransportRequest(method="POST", url=url, headers=headers, body=body)


def _anthropic_parse_response(response: TransportResponse) -> str:
    payload = json.loads(response.body.decode("utf-8"))
    blocks = payload.get("content", [])
    return "".join(
        block.get("text", "")
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    )


# --- openai --------------------------------------------------------------


def _openai_build_request(
    key: SecretKey, model: str, messages: list[dict[str, str]], options: dict[str, Any]
) -> TransportRequest:
    payload = {
        "model": model,
        "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
        "max_tokens": options.get("max_tokens", DEFAULT_MAX_TOKENS),
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {"content-type": "application/json", "authorization": f"Bearer {key.reveal()}"}
    url = options.get("base_url") or "https://api.openai.com/v1/chat/completions"
    return TransportRequest(method="POST", url=url, headers=headers, body=body)


def _chat_completions_parse_response(response: TransportResponse) -> str:
    payload = json.loads(response.body.decode("utf-8"))
    choices = payload.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return message.get("content", "") or ""


# --- openai-compatible (also backs hf-inference / nvidia-nim presets) ----


def _make_openai_compatible_build_request(
    default_base_url: str | None = None,
) -> Callable[[SecretKey, str, list[dict[str, str]], dict[str, Any]], TransportRequest]:
    def _build(
        key: SecretKey, model: str, messages: list[dict[str, str]], options: dict[str, Any]
    ) -> TransportRequest:
        base_url = options.get("base_url") or default_base_url
        if not base_url:
            raise ValueError(
                "the openai-compatible provider requires base_url= "
                "(hf-inference/nvidia-nim presets supply their own default)"
            )
        payload = {
            "model": model,
            "messages": [{"role": m["role"], "content": m["content"]} for m in messages],
            "max_tokens": options.get("max_tokens", DEFAULT_MAX_TOKENS),
        }
        body = json.dumps(payload).encode("utf-8")
        headers = {"content-type": "application/json", "authorization": f"Bearer {key.reveal()}"}
        url = base_url.rstrip("/") + "/chat/completions"
        return TransportRequest(method="POST", url=url, headers=headers, body=body)

    return _build


# --- bedrock (sigv4-signed InvokeModel, Anthropic-family model body shape) ---


def _bedrock_build_request(
    key: SecretKey, model: str, messages: list[dict[str, str]], options: dict[str, Any]
) -> TransportRequest:
    """Build a sigv4-signed Bedrock ``InvokeModel`` request for an Anthropic-family model.

    ``key.reveal()`` is a JSON string of the *user's* AWS credentials —
    ``{"access_key_id", "secret_access_key", "region", "session_token"?}`` —
    as stored in the vault by whatever UI collected them; never an
    operator-owned credential. Bedrock hosts several model families with
    different request bodies; this skeleton targets the common
    Anthropic-on-Bedrock shape (``anthropic_version``/``messages``), the
    same one the ``anthropic`` provider uses, which is what a hosted game
    agent needs today. Extending to other model families is future work.
    """
    credentials = json.loads(key.reveal())
    region = options.get("region") or credentials.get("region") or "us-east-1"
    system_text, chat_messages = _split_system(messages)
    payload: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": options.get("max_tokens", DEFAULT_MAX_TOKENS),
        "messages": [{"role": m["role"], "content": m["content"]} for m in chat_messages],
    }
    if system_text:
        payload["system"] = system_text
    body = json.dumps(payload).encode("utf-8")
    host = f"bedrock-runtime.{region}.amazonaws.com"
    url = f"https://{host}/model/{model}/invoke"
    headers = sigv4.sign_request(
        method="POST",
        url=url,
        body=body,
        region=region,
        service="bedrock",
        access_key=credentials["access_key_id"],
        secret_key=credentials["secret_access_key"],
        session_token=credentials.get("session_token"),
        extra_headers={"content-type": "application/json"},
    )
    return TransportRequest(method="POST", url=url, headers=headers, body=body)


def _bedrock_parse_response(response: TransportResponse) -> str:
    return _anthropic_parse_response(response)


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec("anthropic", _anthropic_build_request, _anthropic_parse_response),
    "openai": ProviderSpec("openai", _openai_build_request, _chat_completions_parse_response),
    "openai-compatible": ProviderSpec(
        "openai-compatible",
        _make_openai_compatible_build_request(),
        _chat_completions_parse_response,
    ),
    "hf-inference": ProviderSpec(
        "hf-inference",
        _make_openai_compatible_build_request(HF_INFERENCE_BASE_URL),
        _chat_completions_parse_response,
    ),
    "nvidia-nim": ProviderSpec(
        "nvidia-nim",
        _make_openai_compatible_build_request(NVIDIA_NIM_BASE_URL),
        _chat_completions_parse_response,
    ),
    "bedrock": ProviderSpec("bedrock", _bedrock_build_request, _bedrock_parse_response),
}


def _resolve_key(handle_or_key: SecretKey | str, vault: KeyVault | None) -> SecretKey:
    if isinstance(handle_or_key, SecretKey):
        return handle_or_key
    if isinstance(handle_or_key, str):
        if vault is None:
            raise ValueError(
                "handle_or_key was a vault handle string but no vault= was provided to resolve it"
            )
        return vault.get(handle_or_key)
    raise TypeError(
        f"handle_or_key must be a SecretKey or a vault handle string, got {type(handle_or_key)!r}"
    )


def complete(
    provider: str,
    handle_or_key: SecretKey | str,
    model: str,
    messages: list[dict[str, str]],
    *,
    vault: KeyVault | None = None,
    transport: Transport | None = None,
    base_url: str | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    region: str | None = None,
) -> str:
    """Call ``provider`` with ``messages`` and return the model's reply text.

    Uniform across every registered provider: same call shape, same return
    type (plain text), regardless of whether the wire format underneath is
    Anthropic's messages API, an OpenAI-shaped chat-completions API, or a
    sigv4-signed Bedrock invoke. ``handle_or_key`` is never an environment
    variable lookup — see the module docstring.
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise UnknownProviderError(provider)
    key = _resolve_key(handle_or_key, vault)
    options = {"base_url": base_url, "max_tokens": max_tokens, "region": region}
    request = spec.build_request(key, model, messages, options)
    transport = transport or default_transport
    response = transport(request)
    if response.status >= 400:
        raise ProviderError(provider, response.status, response.body)
    return spec.parse_response(response)
