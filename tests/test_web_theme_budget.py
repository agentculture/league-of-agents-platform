"""Tests for the performance budget contract in :mod:`league_site.web.theme`.

The contract itself — what each ceiling is, and why — is documented in that
module's docstring (its "Performance budget" section); this file is where
the numbers are enforced in code. It is the *first* artifact of the dazzle
pass (spec c11): the budget was renegotiated here, before any dazzle code
landed, so every later task lands inside numbers that were already agreed
and already tested — CSS <= 24KB, first-party JS <= 8KB, combined <= 32KB,
zero external requests. The pre-dazzle contract (CSS <= 10KB, zero JS,
:mod:`league_site.web.shell` emitting no ``<script>`` tag at all) was
re-verified against this repo, not recalled from memory, before the new
ceilings below were chosen.
"""

from __future__ import annotations

import pytest

from league_site.web import theme


def test_css_budget_constant_matches_the_renegotiated_twenty_four_kilobyte_ceiling() -> None:
    assert theme.CSS_BUDGET_BYTES == 24 * 1024


def test_stylesheet_payload_is_within_the_css_budget() -> None:
    payload = theme.STYLESHEET.encode("utf-8")
    assert len(payload) <= theme.CSS_BUDGET_BYTES


def test_js_budget_constant_matches_the_renegotiated_eight_kilobyte_ceiling() -> None:
    assert theme.JS_BUDGET_BYTES == 8 * 1024


def test_total_asset_budget_is_the_sum_of_the_css_and_js_ceilings() -> None:
    assert theme.TOTAL_ASSET_BUDGET_BYTES == theme.CSS_BUDGET_BYTES + theme.JS_BUDGET_BYTES
    assert theme.TOTAL_ASSET_BUDGET_BYTES == 32 * 1024


def test_site_js_payload_is_within_the_js_budget() -> None:
    """Auto-activates once a later task adds :mod:`league_site.web.scripts`.

    Until then this skips cleanly — there is no ``SITE_JS`` to measure yet,
    and the pre-dazzle baseline is genuinely zero JS (see
    ``test_zero_script_shell_baseline_is_still_true_pre_dazzle`` below).
    """
    scripts = pytest.importorskip("league_site.web.scripts")
    payload = scripts.SITE_JS.encode("utf-8")
    assert len(payload) <= theme.JS_BUDGET_BYTES


def test_zero_script_shell_baseline_is_still_true_pre_dazzle() -> None:
    """The pre-dazzle baseline this budget was renegotiated from: zero JS.

    :mod:`league_site.web.shell` emits no ``<script>`` tag anywhere in its
    page template. Once a later task wires in ``/site.js`` (and possibly an
    inline pre-paint snippet), this assertion is expected to change —
    that's the renegotiated JS allowance being spent, not a regression.
    """
    import inspect

    from league_site.web import shell

    source = inspect.getsource(shell)
    assert "<script" not in source


def test_stylesheet_still_makes_no_external_requests() -> None:
    assert "url(http" not in theme.STYLESHEET
    assert "@import" not in theme.STYLESHEET
    assert "<script" not in theme.STYLESHEET
