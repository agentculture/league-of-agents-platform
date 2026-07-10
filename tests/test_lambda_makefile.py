"""Verifies the repo-root Makefile's SAM build targets install the
``league-of-agents`` game package into the Lambda artifact.

``infra/template.yaml``'s ``HttpHandlerFunction`` drives the league CLI as a
subprocess at runtime (see ``league_site/game/runner.py``); the CLI has to
actually be installed into the deployment package for that to work — this
Makefile's job (see the module docstring at the top of the Makefile itself
for why a Makefile and not SAM's built-in Python build workflow).

This is a static/text check of the Makefile only — no ``pip install`` and no
network access happens in this test.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAKEFILE = _REPO_ROOT / "Makefile"

#: The pin this Makefile install must use. ``docs/game-integration.md``
#: calls for "the game version [pinned] to 0.14.0 or later"; 0.16.0 is the
#: currently-verified-compatible release (see that doc and
#: ``tests/test_game_real_cli.py``), so the upper bound stays one minor
#: ahead of it, open below the next minor.
_LEAGUE_PACKAGE_PIN = "league-of-agents>=0.16,<0.17"


def _target_recipe(text: str, target: str) -> list[str]:
    """The recipe lines (originally tab-indented, per Make's syntax)
    belonging to ``target:``, stripped, up to the first non-recipe line."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    recipe: list[str] = []
    for line in lines[start + 1 :]:
        if not line.startswith("\t"):
            break
        recipe.append(line.strip())
    return recipe


def test_makefile_exists() -> None:
    assert _MAKEFILE.is_file()


def test_http_handler_target_still_installs_the_platform_package() -> None:
    recipe = _target_recipe(_MAKEFILE.read_text(encoding="utf-8"), "build-HttpHandlerFunction")
    assert any(
        line.startswith("pip install") and line.split()[-1] == "." for line in recipe
    ), recipe


def test_http_handler_target_installs_the_pinned_league_game_package() -> None:
    recipe = _target_recipe(_MAKEFILE.read_text(encoding="utf-8"), "build-HttpHandlerFunction")
    league_lines = [line for line in recipe if "league-of-agents" in line]
    assert len(league_lines) == 1, recipe
    (line,) = league_lines
    assert line.startswith("pip install"), line
    assert "--target" in line
    assert "$(ARTIFACTS_DIR)" in line
    assert _LEAGUE_PACKAGE_PIN in line


def test_cleanup_target_does_not_install_the_league_game_package() -> None:
    """CleanupFunction never touches the game CLI — it only talks to
    DynamoDB/S3 (see league_site/aws_lambda/cleanup.py) — so there's no
    reason to bloat that artifact with a game-CLI install."""
    recipe = _target_recipe(_MAKEFILE.read_text(encoding="utf-8"), "build-CleanupFunction")
    assert not any("league-of-agents" in line for line in recipe), recipe


def test_pyproject_does_not_declare_the_game_package_as_a_dependency() -> None:
    """Architectural rule: league-of-agents is subprocess-only, installed
    into the Lambda artifact by this Makefile alone, never a project
    dependency — a dependency entry would tempt an ``import league``, which
    tests/test_game_import_boundary.py structurally bans. Parses the actual
    dependency arrays (not a raw substring search) so this doesn't false-fail
    on unrelated text like the project's own name/description, which
    legitimately mention "league-of-agents" (the platform's URL, e.g.)."""
    data = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = data["project"]
    all_deps = list(project.get("dependencies", []))
    for extra_deps in project.get("optional-dependencies", {}).values():
        all_deps.extend(extra_deps)
    offenders = [dep for dep in all_deps if dep.lower().startswith("league-of-agents")]
    assert not offenders, offenders
