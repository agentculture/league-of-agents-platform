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

    That task (t3) has now landed, so the importorskip is a formality —
    kept so this file reads the same before and after the module existed.
    """
    scripts = pytest.importorskip("league_site.web.scripts")
    payload = scripts.SITE_JS.encode("utf-8")
    assert len(payload) <= theme.JS_BUDGET_BYTES


def test_shell_scripts_are_exactly_the_budgeted_first_party_ones() -> None:
    """The successor to the pre-dazzle zero-script baseline test.

    The old assertion here (``"<script" not in inspect.getsource(shell)``)
    documented the baseline the JS budget was renegotiated *from*; its own
    docstring said it was expected to change once a later task wired in
    ``/site.js`` and an inline pre-paint snippet. That task (t3) landed —
    so the assertion evolves with the contract: the shell may only emit
    the inline pre-paint snippet plus a deferred first-party ``/site.js``
    tag, and their combined weight (the snippet counts toward the JS
    budget even though it never travels via ``/site.js``) stays within
    :data:`league_site.web.theme.JS_BUDGET_BYTES`.
    """
    import inspect

    from league_site.web import scripts, shell

    source = inspect.getsource(shell)
    assert "<script" in source, "t3's script wiring should be present now"
    assert '"/site.js"' in source
    assert 'src="http' not in source and "src='http" not in source
    combined = len(scripts.SITE_JS.encode("utf-8")) + len(scripts.PRE_PAINT_JS.encode("utf-8"))
    assert combined <= theme.JS_BUDGET_BYTES


def test_stylesheet_still_makes_no_external_requests() -> None:
    assert "url(http" not in theme.STYLESHEET
    assert "@import" not in theme.STYLESHEET
    assert "<script" not in theme.STYLESHEET


def test_real_combined_payload_fits_the_total_asset_budget() -> None:
    """The combined budget is measured against REAL bytes, not restated as
    arithmetic: stylesheet + /site.js + the inline pre-paint snippet must
    fit TOTAL_ASSET_BUDGET_BYTES together (review finding: the constant
    alone was a tautology no test tied to actual payloads)."""
    from league_site.web import scripts

    combined = (
        len(theme.STYLESHEET.encode("utf-8"))
        + len(scripts.SITE_JS.encode("utf-8"))
        + len(scripts.PRE_PAINT_JS.encode("utf-8"))
    )
    assert combined <= theme.TOTAL_ASSET_BUDGET_BYTES


def test_both_dark_paths_interpolate_the_same_token_block() -> None:
    """The dark palette is one Python constant interpolated into both dark
    selectors — this drift alarm pins that both interpolations landed (the
    glow value is unique to the dark palette, so exactly two occurrences
    means explicit-choice dark and OS-default dark can never disagree)."""
    assert theme.STYLESHEET.count("--accent-glow: rgba(255, 138, 61, .22);") == 2
