"""Tests for the /evals pages (index + replay shell), their scripts and mounting."""

from __future__ import annotations

import re

import pytest

from league_site.aws_lambda import wiring
from league_site.evals import styles
from league_site.evals import wsgi as evals_wsgi
from league_site.web.http import site_app
from tests._api_support import call

APP = evals_wsgi.evals_app


def _get(app, path):
    status, headers, body = call(app, "GET", path)
    text = body.decode("utf-8") if isinstance(body, bytes) else str(body)
    return status, headers, text


def test_index_is_200_with_shell_noscript_and_data_base() -> None:
    status, _, html = _get(APP, "/evals/")
    assert status.startswith("200")
    assert 'class="site-header' in html or "<header" in html
    assert "theme-toggle" in html
    assert '<main id="main" class="evals-page"' in html
    assert 'data-base="/evals/data"' in html
    assert "<noscript>" in html and "/evals/data/manifest.json" in html
    assert "No evals yet" in html
    for container in ("hero", "ledger", "benchmarks", "players"):
        assert f'id="{container}"' in html
    assert "/evals/charts.js?v=" in html and "/evals/replay.js?v=" in html


def test_replay_shell_has_labelled_controls_and_data_attributes() -> None:
    status, _, html = _get(APP, "/evals/run-1/m-01/")
    assert status.startswith("200")
    assert 'data-eval="run-1"' in html and 'data-match="m-01"' in html
    assert "theme-toggle" in html
    for label in ("Previous turn", "Play", "Next turn"):
        assert f'aria-label="{label}"' in html
    assert re.search(r'<input type="range"[^>]*aria-label="Turn"', html)
    assert 'role="group" aria-label="Board view"' in html
    assert "<noscript>" in html and "/evals/data/run-1/manifest.json" in html
    assert "/evals/replay.js?v=" in html and "/evals/charts.js" not in html


def test_eval_redirects_to_index_anchor() -> None:
    status, headers, _ = _get(APP, "/evals/run-1/")
    assert status.startswith("302")
    assert headers["Location"] == "/evals/#run-1"
    assert _get(APP, "/evals")[1]["Location"] == "/evals/"


@pytest.mark.parametrize(
    "path",
    [
        "/evals/Bad/",
        "/evals/-x/",
        "/evals/" + "a" * 65 + "/",
        "/evals/ok/Bad/",
        "/evals/ok/m/extra/",
        "/evals/ok/m",
        "/evals/data/",
        "/evals/nope.js",
    ],
)
def test_invalid_ids_and_paths_404(path: str) -> None:
    status, _, html = _get(APP, path)
    assert status.startswith("404")
    assert "not found" in html and "theme-toggle" in html


def test_data_base_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAGUE_EVALS_DATA_BASE", 'http://x/"d"/')
    _, _, html = _get(APP, "/evals/")
    assert 'data-base="http://x/&quot;d&quot;"' in html


def test_hostile_ids_never_reach_markup() -> None:
    assert _get(APP, "/evals/%3Cscript%3E/m/")[0].startswith("404")


@pytest.mark.parametrize("name", ["replay.js", "charts.js"])
def test_scripts_served_with_immutable_cache_and_within_budget(name: str) -> None:
    status, headers, body = _get(APP, f"/evals/{name}")
    assert status.startswith("200")
    assert "javascript" in headers["Content-Type"]
    assert "immutable" in headers["Cache-Control"]
    assert 'dataset.evals = "ready"' in body


def test_evals_scripts_fit_their_own_budget() -> None:
    total = sum(len(b) for b in evals_wsgi._SCRIPTS.values())
    assert total <= evals_wsgi.EVALS_JS_BUDGET_BYTES


def test_every_evals_css_selector_is_scoped() -> None:
    def walk(css: str) -> None:
        for m in re.finditer(r"([^{}]+)\{", css):
            prelude = m.group(1).strip()
            if prelude.startswith("@") or re.fullmatch(r"[\d.%,\s]+|from|to", prelude):
                continue
            for sel in prelude.split(","):
                assert sel.strip().startswith(".evals-page"), sel

    walk(styles.EVALS_CSS)
    assert ".evals-page .wrap" in styles.EVALS_CSS


def test_composed_site_app_serves_evals() -> None:
    app = site_app()
    assert _get(app, "/evals/")[0].startswith("200")
    assert _get(app, "/evals/run-1/m-01/")[0].startswith("200")
    assert _get(app, "/evals/replay.js")[0].startswith("200")
    assert _get(app, "/index")[0].startswith("200")


def test_lambda_wiring_serves_evals(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in list(__import__("os").environ):
        if var.startswith("LEAGUE_") and var != "LEAGUE_EVALS_DATA_BASE":
            monkeypatch.delenv(var)
    app = wiring.build_site_app()
    status, _, html = _get(app, "/evals/")
    assert status.startswith("200") and "evals-page" in html
