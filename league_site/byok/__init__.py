"""Bring-your-own-key: a player pastes their own LLM API key and the platform
runs a hosted agent on it — the alternative to Bring Your Own Agent
(:mod:`league_site.auth.tokens`, bearer-token auth for an externally-hosted
agent). User matches never consume operator-owned API keys.

Vendor-neutral by construction: :mod:`league_site.byok.providers` reaches
Anthropic, OpenAI, Amazon Bedrock, Hugging Face Inference, NVIDIA NIM, and
arbitrary OpenAI-compatible endpoints through one injectable transport, so
no game logic anywhere is provider-specific.

- :mod:`league_site.byok.vault` — the ``KeyVault`` storage interface,
  :class:`~league_site.byok.vault.SecretKey` (the redacting wrapper key
  material always travels in), and :class:`~league_site.byok.vault.
  InMemoryKeyVault`.
- :mod:`league_site.byok.aws_vault` — a KMS/DynamoDB-backed ``KeyVault``
  skeleton (the only module here that imports ``boto3``; imported
  separately, mirrors :mod:`league_site.matches.aws`).
- :mod:`league_site.byok.sigv4` — a stdlib-only AWS Signature Version 4
  signer, used by the Bedrock provider.
- :mod:`league_site.byok.providers` — the provider registry and the
  uniform ``complete(provider, handle_or_key, model, messages)`` call.
- :mod:`league_site.byok.runner` — the hosted-agent turn function: match
  view in, provider call, tolerant JSON extraction, legal_actions
  validation, validated orders + decision record out.
"""

from __future__ import annotations

from league_site.byok.providers import (
    PROVIDERS,
    ProviderError,
    Transport,
    TransportRequest,
    TransportResponse,
    UnknownProviderError,
    complete,
    default_transport,
)
from league_site.byok.runner import (
    DroppedAction,
    MatchView,
    NoVaultKeyError,
    TurnDecision,
    build_messages,
    run_turn,
)
from league_site.byok.vault import (
    InMemoryKeyVault,
    KeyHandleInfo,
    KeyNotFoundError,
    KeyVault,
    KeyVaultError,
    SecretKey,
    coerce_secret,
)

__all__ = [
    "PROVIDERS",
    "DroppedAction",
    "InMemoryKeyVault",
    "KeyHandleInfo",
    "KeyNotFoundError",
    "KeyVault",
    "KeyVaultError",
    "MatchView",
    "NoVaultKeyError",
    "ProviderError",
    "SecretKey",
    "Transport",
    "TransportRequest",
    "TransportResponse",
    "TurnDecision",
    "UnknownProviderError",
    "build_messages",
    "coerce_secret",
    "complete",
    "default_transport",
    "run_turn",
]
