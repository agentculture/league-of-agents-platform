"""Shared DynamoDB fake-table helpers for the store/wiring tests.

Several test modules stand in for a boto3 ``Table`` with an in-process dict
(``tests/test_accounts_aws.py``, ``tests/test_tokens_aws.py``,
``tests/test_accounts_wiring.py``, ``tests/test_lambda_wiring.py``). Once
:class:`league_site.accounts.aws.DynamoDBAccountStore` moved its
``upsert``/``set_blocked`` onto ``update_item`` (so a concurrent block can
never be clobbered by a stale full-item overwrite), every such fake needs to
apply a real DynamoDB ``SET`` expression — including the ``if_not_exists``
clauses ``upsert`` uses for insert-only fields. This module holds that one
faithful applier so the fakes don't each re-derive it.

Not collected by pytest (module name doesn't match ``test_*``); imported by
the ``test_*`` modules that need it.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["apply_set_expression"]

_IF_NOT_EXISTS_RE = re.compile(r"if_not_exists\((\w+),\s*(:\w+)\)")


def _split_top_level_commas(expression: str) -> list[str]:
    """Split *expression* on commas outside of parentheses.

    So a clause like ``if_not_exists(account_id, :account_id)`` stays one
    piece, not two — a plain ``str.split(",")`` would cut it in half.
    """
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for char in expression:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def apply_set_expression(
    item: dict[str, Any], update_expression: str, values: dict[str, Any]
) -> None:
    """Apply a DynamoDB ``SET`` *update_expression* to *item* in place.

    Handles the two clause shapes the code under test issues: a plain
    ``attr = :value`` (always written) and ``attr = if_not_exists(attr,
    :value)`` (written only when *attr* is absent — insert-only fields like
    ``created_at``). Raises on anything else so a fake never silently
    mis-emulates an expression it wasn't taught.
    """
    if not update_expression.startswith("SET "):
        raise AssertionError(f"fake only applies SET expressions, got {update_expression!r}")
    for clause in _split_top_level_commas(update_expression[len("SET ") :]):
        attr, _, expr = clause.partition("=")
        attr = attr.strip()
        expr = expr.strip()
        match = _IF_NOT_EXISTS_RE.fullmatch(expr)
        if match:
            if attr not in item:
                item[attr] = values[match.group(2)]
        else:
            item[attr] = values[expr]
