"""Tests for league_site.byok.runner: the hosted-agent turn function.

Covers the task's acceptance criteria:

* A hosted agent turn completes end to end (build prompt -> call provider
  via a stubbed transport -> tolerant JSON extraction -> legal_actions
  validation) via three distinct provider paths: anthropic-shaped,
  openai-shaped, and an openai-compatible local stub.
* Orders validation: an illegal action from the model is dropped and
  recorded with a reason; legal ones pass through untouched.
* No operator-key fallback: monkeypatching ANTHROPIC_API_KEY/
  OPENAI_API_KEY/etc. into the environment and calling run_turn without a
  vault-supplied key still refuses to run.
* A revoked vault handle makes a turn attempt fail cleanly.
"""

from __future__ import annotations

import json

import pytest

from league_site.byok import runner
from league_site.byok.providers import TransportRequest, TransportResponse
from league_site.byok.runner import (
    DroppedAction,
    MatchView,
    NoVaultKeyError,
    TurnDecision,
    build_messages,
    run_turn,
)
from league_site.byok.vault import InMemoryKeyVault, KeyNotFoundError, SecretKey

API_KEY = "sk-test-key-456"  # nosec B105 - test fixture

LEGAL_ACTIONS = [
    {"unit": "team-a-u1", "action": {"kind": "move", "to": [1, 2]}},
    {"unit": "team-a-u1", "action": {"kind": "hold"}},
    {"unit": "team-a-u2", "action": {"kind": "attack", "target": "team-b-u1"}},
]


def _match_view(**overrides: object) -> MatchView:
    fields: dict[str, object] = {
        "state": {"turn": 3, "units": {"team-a-u1": {"hp": 5}, "team-a-u2": {"hp": 3}}},
        "legal_actions": LEGAL_ACTIONS,
        "last_turn_rejections": [],
        "team": "team-a",
    }
    fields.update(overrides)
    return MatchView(**fields)  # type: ignore[arg-type]


class RecordingTransport:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.requests: list[TransportRequest] = []

    def __call__(self, request: TransportRequest) -> TransportResponse:
        self.requests.append(request)
        return self.response


def _json_response(payload: dict) -> TransportResponse:
    return TransportResponse(status=200, body=json.dumps(payload).encode("utf-8"))


def _vault_with_key() -> tuple[InMemoryKeyVault, str]:
    vault = InMemoryKeyVault()
    handle = vault.put("player-1", "openai", API_KEY)
    return vault, handle


# --- build_messages ---------------------------------------------------------------


def test_build_messages_is_compact_json_and_carries_the_full_view() -> None:
    messages = build_messages(_match_view())

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    # compact: no pretty-printing whitespace
    assert "\n" not in messages[1]["content"]
    assert "  " not in messages[1]["content"]
    payload = json.loads(messages[1]["content"])
    assert payload["legal_actions"] == LEGAL_ACTIONS
    assert payload["team"] == "team-a"
    assert payload["last_turn_rejections"] == []


# --- three distinct provider paths ------------------------------------------------


def test_run_turn_completes_via_anthropic_shaped_transport() -> None:
    vault, handle = _vault_with_key()
    reply = json.dumps({"orders": [{"unit": "team-a-u1", "action": {"kind": "hold"}}]})
    transport = RecordingTransport(_json_response({"content": [{"type": "text", "text": reply}]}))

    decision = run_turn(
        _match_view(),
        provider="anthropic",
        model="claude-sonnet-5",
        handle=handle,
        vault=vault,
        transport=transport,
    )

    assert decision.orders == [{"unit": "team-a-u1", "action": {"kind": "hold"}}]
    assert decision.dropped == []
    assert decision.provider == "anthropic"


def test_run_turn_completes_via_openai_shaped_transport() -> None:
    vault, handle = _vault_with_key()
    reply = json.dumps(
        {"orders": [{"unit": "team-a-u2", "action": {"kind": "attack", "target": "team-b-u1"}}]}
    )
    transport = RecordingTransport(_json_response({"choices": [{"message": {"content": reply}}]}))

    decision = run_turn(
        _match_view(),
        provider="openai",
        model="gpt-4o",
        handle=handle,
        vault=vault,
        transport=transport,
    )

    assert decision.orders == [
        {"unit": "team-a-u2", "action": {"kind": "attack", "target": "team-b-u1"}}
    ]
    assert decision.dropped == []


def test_run_turn_completes_via_openai_compatible_local_stub_transport() -> None:
    vault, handle = _vault_with_key()
    reply = json.dumps(
        {"orders": [{"unit": "team-a-u1", "action": {"kind": "move", "to": [1, 2]}}]}
    )
    transport = RecordingTransport(_json_response({"choices": [{"message": {"content": reply}}]}))

    decision = run_turn(
        _match_view(),
        provider="openai-compatible",
        model="local-model",
        handle=handle,
        vault=vault,
        transport=transport,
        base_url="http://localhost:8000/v1",
    )

    assert decision.orders == [{"unit": "team-a-u1", "action": {"kind": "move", "to": [1, 2]}}]
    assert decision.dropped == []
    assert transport.requests[0].url == "http://localhost:8000/v1/chat/completions"


# --- tolerant JSON extraction ------------------------------------------------------


def test_run_turn_extracts_json_wrapped_in_prose_and_markdown_fences() -> None:
    vault, handle = _vault_with_key()
    wrapped_reply = (
        "Sure, here are my orders for this turn:\n"
        "```json\n"
        + json.dumps({"orders": [{"unit": "team-a-u1", "action": {"kind": "hold"}}]})
        + "\n```\nLet me know if you need anything else!"
    )
    transport = RecordingTransport(
        _json_response({"choices": [{"message": {"content": wrapped_reply}}]})
    )

    decision = run_turn(
        _match_view(),
        provider="openai",
        model="gpt-4o",
        handle=handle,
        vault=vault,
        transport=transport,
    )

    assert decision.orders == [{"unit": "team-a-u1", "action": {"kind": "hold"}}]


def test_run_turn_with_unparseable_reply_yields_no_orders_not_an_exception() -> None:
    vault, handle = _vault_with_key()
    transport = RecordingTransport(
        _json_response({"choices": [{"message": {"content": "I refuse to reply in JSON."}}]})
    )

    decision = run_turn(
        _match_view(),
        provider="openai",
        model="gpt-4o",
        handle=handle,
        vault=vault,
        transport=transport,
    )

    assert decision.orders == []
    assert decision.dropped == []
    assert decision.raw_response == "I refuse to reply in JSON."


# --- _extract_first_json_object: direct unit tests of the tolerant extractor -----


def test_extract_first_json_object_handles_escaped_quotes_inside_strings() -> None:
    text = '{"orders": [{"unit": "u1", "action": {"note": "she said \\"go\\""}}]}'

    parsed = runner._extract_first_json_object(text)

    assert parsed == {"orders": [{"unit": "u1", "action": {"note": 'she said "go"'}}]}


def test_extract_first_json_object_skips_braces_inside_string_values() -> None:
    text = 'prefix { "a": "text with a } brace inside" } {"orders": []}'

    parsed = runner._extract_first_json_object(text)

    # the first candidate (the leading `{...}`) is itself valid JSON, so it wins
    assert parsed == {"a": "text with a } brace inside"}


def test_extract_first_json_object_retries_after_an_invalid_candidate() -> None:
    text = 'not json: { this is not valid json } but here it is: {"orders": []}'

    parsed = runner._extract_first_json_object(text)

    assert parsed == {"orders": []}


def test_extract_first_json_object_returns_none_when_nothing_parses() -> None:
    assert runner._extract_first_json_object("no braces here at all") is None
    assert runner._extract_first_json_object("{ unterminated") is None
    assert runner._extract_first_json_object("{not valid} {also not valid}") is None


# --- orders validation: illegal dropped + recorded, legal pass through untouched --


def test_illegal_action_is_dropped_and_recorded_legal_ones_pass_through_untouched() -> None:
    vault, handle = _vault_with_key()
    reply = json.dumps(
        {
            "orders": [
                {"unit": "team-a-u1", "action": {"kind": "hold"}},  # legal
                {
                    "unit": "team-a-u2",
                    "action": {"kind": "self_destruct"},
                },  # illegal: not in legal_actions
                {
                    "unit": "team-a-u9",
                    "action": {"kind": "move", "to": [9, 9]},
                },  # illegal: unknown unit
            ]
        }
    )
    transport = RecordingTransport(_json_response({"choices": [{"message": {"content": reply}}]}))

    decision = run_turn(
        _match_view(),
        provider="openai",
        model="gpt-4o",
        handle=handle,
        vault=vault,
        transport=transport,
    )

    assert decision.orders == [{"unit": "team-a-u1", "action": {"kind": "hold"}}]
    assert len(decision.dropped) == 2
    dropped_units = {d.unit for d in decision.dropped}
    assert dropped_units == {"team-a-u2", "team-a-u9"}
    for dropped in decision.dropped:
        assert isinstance(dropped, DroppedAction)
        assert dropped.reason  # non-empty explanation recorded


def test_malformed_order_entries_are_dropped_with_a_reason() -> None:
    vault, handle = _vault_with_key()
    reply = json.dumps({"orders": ["not-an-object", {"unit": "team-a-u1"}, 42]})
    transport = RecordingTransport(_json_response({"choices": [{"message": {"content": reply}}]}))

    decision = run_turn(
        _match_view(),
        provider="openai",
        model="gpt-4o",
        handle=handle,
        vault=vault,
        transport=transport,
    )

    assert decision.orders == []
    assert len(decision.dropped) == 3
    assert all("malformed" in d.reason for d in decision.dropped)


def test_orders_field_missing_or_wrong_type_yields_no_orders() -> None:
    vault, handle = _vault_with_key()
    transport = RecordingTransport(
        _json_response({"choices": [{"message": {"content": '{"orders": "nope"}'}}]})
    )

    decision = run_turn(
        _match_view(),
        provider="openai",
        model="gpt-4o",
        handle=handle,
        vault=vault,
        transport=transport,
    )

    assert decision.orders == []
    assert decision.dropped == []


# --- no operator-key fallback -------------------------------------------------------


def test_run_turn_without_a_handle_refuses_even_with_env_keys_poisoned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "operator-owned-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "operator-owned-openai-key")
    monkeypatch.setenv("NVIDIA_API_KEY", "operator-owned-nvidia-key")
    monkeypatch.setenv("HF_TOKEN", "operator-owned-hf-token")

    with pytest.raises(NoVaultKeyError):
        run_turn(_match_view(), provider="anthropic", model="claude-sonnet-5")


def test_run_turn_with_unknown_handle_raises_key_not_found_never_falls_back_to_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "operator-owned-anthropic-key")
    monkeypatch.setenv("OPENAI_API_KEY", "operator-owned-openai-key")
    vault = InMemoryKeyVault()  # deliberately empty - nothing was ever put()

    with pytest.raises(KeyNotFoundError):
        run_turn(
            _match_view(),
            provider="openai",
            model="gpt-4o",
            handle="byok_never_issued",
            vault=vault,
        )


def test_a_revoked_key_makes_a_turn_attempt_fail_cleanly() -> None:
    vault, handle = _vault_with_key()
    vault.revoke(handle)

    with pytest.raises(KeyNotFoundError):
        run_turn(_match_view(), provider="openai", model="gpt-4o", handle=handle, vault=vault)


def test_no_provider_module_reads_operator_environment_variables() -> None:
    """Static guard: neither runner.py nor providers.py ever reads os.environ for a key.

    A behavioral regression here would be catastrophic (a user match
    silently spending the operator's own API budget), so this is checked
    both behaviorally (the tests above) and structurally here.
    """
    import inspect

    from league_site.byok import providers, runner

    for module in (providers, runner):
        source = inspect.getsource(module)
        assert "import os" not in source
        assert "os.getenv(" not in source
        assert "os.environ.get(" not in source
        assert "os.environ[" not in source


def test_run_turn_accepts_an_already_resolved_secret_key_without_a_vault() -> None:
    """handle= may be a resolved SecretKey directly - still never touches the environment."""
    transport = RecordingTransport(
        _json_response({"choices": [{"message": {"content": '{"orders": []}'}}]})
    )

    decision = run_turn(
        _match_view(),
        provider="openai",
        model="gpt-4o",
        handle=SecretKey(API_KEY),
        transport=transport,
    )

    assert isinstance(decision, TurnDecision)
    assert transport.requests[0].headers["authorization"] == f"Bearer {API_KEY}"
