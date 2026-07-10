"""The platform never imports the league game package.

``league_site.game`` drives the League of Agents game exclusively as an
external subprocess (``docs/game-integration.md``); the game repo
(``league``) is explicitly non-importable by platform policy (spec scope:
"the game repo remains non-importable"). This test proves it structurally,
by AST — not by grepping for the substring "league" (which would also flag
every ``league_site``/``LeagueRunner``/``LeagueCliError`` identifier in the
codebase) — over every ``.py`` file under ``league_site/``.
"""

from __future__ import annotations

import ast
from pathlib import Path

import league_site

_LEAGUE_SITE_ROOT = Path(league_site.__file__).resolve().parent


def _python_files() -> list[Path]:
    return sorted(_LEAGUE_SITE_ROOT.rglob("*.py"))


def _forbidden_imports(path: Path) -> list[str]:
    """Names of any top-level module this file imports that is (or is a
    submodule of) the ``league`` game package — i.e. ``league`` or
    ``league.<anything>``, but never ``league_site`` or its submodules."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _is_league_game_module(alias.name):
                    hits.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level == 0 and _is_league_game_module(module):
                hits.append(module)
    return hits


def _is_league_game_module(dotted_name: str) -> bool:
    return dotted_name == "league" or dotted_name.startswith("league.")


def test_python_files_are_discovered() -> None:
    # A guard against this test silently passing because the glob found
    # nothing (e.g. a future package layout change moving league_site/game
    # out from under this root).
    files = _python_files()
    assert len(files) > 10
    assert any(p.name == "adapter.py" and p.parent.name == "game" for p in files)


def test_no_source_file_under_league_site_imports_the_league_game_package() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _python_files():
        hits = _forbidden_imports(path)
        if hits:
            offenders[str(path.relative_to(_LEAGUE_SITE_ROOT.parent))] = hits
    assert not offenders, (
        "league_site must never import the league game package (subprocess-only "
        f"integration; see docs/game-integration.md): {offenders}"
    )
