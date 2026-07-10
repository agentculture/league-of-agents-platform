"""Hosted-agent turn runner: BYOK key + match view in, validated orders out.

This is the "the platform runs a hosted agent" half of BYO Key (see the
module docstring in :mod:`league_site.byok.vault` for the other half, key
storage). Given a snapshot of a match — ``state``, ``legal_actions``, and
``last_turn_rejections``, the same shapes the platform adapter reads from
``league match show <id> --json`` (see ``docs/game-integration.md``) — this
module builds a compact prompt, calls the player's own provider via
:mod:`league_site.byok.providers`, tolerantly extracts a JSON orders reply
out of whatever text the model returned, and validates every proposed
order against ``legal_actions`` before handing anything back to the match
adapter.

Hard rule, enforced by construction: :func:`run_turn` only ever obtains key
material via an explicit ``vault``/``handle`` pair (or an already-resolved
:class:`~league_site.byok.vault.SecretKey`). Nowhere in this module, or in
:mod:`league_site.byok.providers`, is ``os.environ`` ever read for a
provider API key — a user match must never be able to consume an
operator-owned key, even by accident, even if the operator's process
happens to have ``ANTHROPIC_API_KEY``/``OPENAI_API_KEY``/etc. set for its
own unrelated purposes. See ``tests/test_byok_runner.py`` for the
env-poisoning regression test that pins this down.

``docs/game-integration.md`` notes the upstream game CLI is an external
dependency not vendored into this repo, and that ``legal_actions`` is
"load-bearing": an exact, precomputed list the adapter must read fresh
and never approximate client-side. This module treats each
``legal_actions`` entry as a ``{"unit": <unit id>, "action": <opaque
JSON>}`` pair — one fully-specified legal (unit, action) combination — and
validates a proposed order by exact structural equality against that list,
never by re-deriving legality itself.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from league_site.byok import providers
from league_site.byok.providers import Transport
from league_site.byok.vault import KeyVault, SecretKey

_SYSTEM_PROMPT = (
    "You are a hosted agent playing the League of Agents grid-lane game. "
    "You will be given the current match state, the exact list of legal "
    "(unit, action) pairs, and any orders the game rejected last turn. "
    "Reply with ONLY a single JSON object of the shape "
    '{"orders": [{"unit": "<unit id>", "action": <copy the action value '
    "exactly from a legal_actions entry for that unit>}, ...]}. Do not "
    "invent actions, units, or fields that are not present in "
    "legal_actions — anything that does not match exactly will be dropped "
    "and will not count as your move."
)


@dataclass(frozen=True)
class MatchView:
    """The subset of a match's live state a hosted agent needs to take one turn.

    Mirrors ``league match show <id> --json``'s ``state``, ``legal_actions``,
    and ``last_turn_rejections`` fields (``docs/game-integration.md``).
    Beyond the ``unit``/``action`` keys documented there, this module treats
    ``state`` and each ``legal_actions``/``last_turn_rejections`` entry as
    opaque JSON — the upstream game CLI is an external dependency this repo
    doesn't vendor, so staying opaque keeps this runner forward-compatible
    with the grid-lane game today and the continuous lane later.
    """

    state: dict[str, Any]
    legal_actions: list[dict[str, Any]]
    last_turn_rejections: list[dict[str, Any]] = field(default_factory=list)
    team: str | None = None


@dataclass(frozen=True)
class DroppedAction:
    """One proposed order that failed validation against ``legal_actions``, and why."""

    unit: Any
    action: Any
    reason: str


@dataclass(frozen=True)
class TurnDecision:
    """The result of one hosted-agent turn: validated orders plus a full audit trail.

    ``orders`` contains only proposed orders that matched a
    ``legal_actions`` entry exactly, untouched. ``dropped`` records every
    rejected proposal and why, for surfacing back to the model next turn
    (via ``last_turn_rejections``) and for operator/debugging visibility.
    ``raw_response`` is the model's full untouched reply text, kept for
    audit — never logged with key material attached, since this decision
    record never carries a :class:`~league_site.byok.vault.SecretKey`.
    """

    orders: list[dict[str, Any]]
    dropped: list[DroppedAction]
    provider: str
    model: str
    raw_response: str


class NoVaultKeyError(ValueError):
    """Raised by :func:`run_turn` when no vault-issued key handle was supplied.

    This is the refusal path proving there is no operator-key fallback:
    :func:`run_turn` never falls back to reading a provider API key out of
    the process environment when a handle is missing — it simply refuses.
    """


def build_messages(match_view: MatchView) -> list[dict[str, str]]:
    """Build a compact two-message prompt (system + user) from ``match_view``.

    The user message is a single ``json.dumps`` of state/legal_actions/
    last_turn_rejections with no indentation — "compact" is a deliberate
    token-budget choice, not an oversight.
    """
    payload: dict[str, Any] = {
        "state": match_view.state,
        "legal_actions": match_view.legal_actions,
        "last_turn_rejections": match_view.last_turn_rejections,
    }
    if match_view.team is not None:
        payload["team"] = match_view.team
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, sort_keys=True, separators=(",", ":"))},
    ]


def _extract_first_json_object(text: str) -> dict[str, Any] | None:
    """Tolerantly extract the first balanced top-level JSON *object* found in ``text``.

    Models routinely wrap their JSON in prose or markdown code fences
    (` ```json { ... } ``` `); this scans for the first ``{`` and finds its
    matching ``}`` by brace-depth counting, correctly skipping over braces
    that appear inside JSON string literals (including escaped quotes). If
    the first candidate isn't valid JSON, or parses to something other than
    an object, the scan resumes from the next ``{`` in the text. Returns
    ``None`` if no candidate parses.
    """
    search_from = 0
    while True:
        start = text.find("{", search_from)
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        end = None
        for i in range(start, len(text)):
            ch = text[i]
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
        if end is not None:
            candidate = text[start : end + 1]
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(parsed, dict):
                    return parsed
        search_from = start + 1


def _validate_orders(
    proposed: list[Any], legal_actions: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[DroppedAction]]:
    kept: list[dict[str, Any]] = []
    dropped: list[DroppedAction] = []
    for order in proposed:
        if not isinstance(order, dict) or "unit" not in order or "action" not in order:
            dropped.append(
                DroppedAction(
                    unit=order.get("unit") if isinstance(order, dict) else None,
                    action=order,
                    reason="malformed order: expected an object with 'unit' and 'action'",
                )
            )
            continue
        unit = order["unit"]
        action = order["action"]
        unit_legal_actions = [la.get("action") for la in legal_actions if la.get("unit") == unit]
        if not unit_legal_actions:
            dropped.append(
                DroppedAction(
                    unit=unit, action=action, reason="unknown unit: no legal_actions entry for it"
                )
            )
            continue
        if action not in unit_legal_actions:
            dropped.append(
                DroppedAction(
                    unit=unit,
                    action=action,
                    reason="action not present in legal_actions for this unit",
                )
            )
            continue
        kept.append({"unit": unit, "action": action})
    return kept, dropped


def run_turn(
    match_view: MatchView,
    *,
    provider: str,
    model: str,
    handle: str | SecretKey | None = None,
    vault: KeyVault | None = None,
    transport: Transport | None = None,
    base_url: str | None = None,
    max_tokens: int = providers.DEFAULT_MAX_TOKENS,
) -> TurnDecision:
    """Play one hosted-agent turn for ``match_view`` and return a validated :class:`TurnDecision`.

    ``handle`` must be either a vault handle string (with ``vault=``
    supplied to resolve it) or an already-resolved
    :class:`~league_site.byok.vault.SecretKey`. If ``handle`` is ``None``
    this raises :class:`NoVaultKeyError` immediately — it never falls back
    to reading a provider API key from the process environment. If
    ``handle`` is a revoked or unknown vault handle,
    :meth:`~league_site.byok.vault.KeyVault.get` raises
    :class:`~league_site.byok.vault.KeyNotFoundError`, which propagates
    uncaught: a revoked key means the turn attempt fails, full stop.
    """
    if handle is None:
        raise NoVaultKeyError(
            "run_turn requires a vault-issued key handle (or a resolved SecretKey); "
            "user matches never fall back to operator-owned environment credentials"
        )
    key: SecretKey = vault.get(handle) if isinstance(handle, str) else handle

    messages = build_messages(match_view)
    raw_response = providers.complete(
        provider,
        key,
        model,
        messages,
        transport=transport,
        base_url=base_url,
        max_tokens=max_tokens,
    )

    parsed = _extract_first_json_object(raw_response) or {}
    proposed_orders = parsed.get("orders")
    if not isinstance(proposed_orders, list):
        proposed_orders = []
    kept, dropped = _validate_orders(proposed_orders, match_view.legal_actions)

    return TurnDecision(
        orders=kept,
        dropped=dropped,
        provider=provider,
        model=model,
        raw_response=raw_response,
    )
