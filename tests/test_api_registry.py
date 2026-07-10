"""Tests for :mod:`league_site.api.registry` — the merged default engine registry.

Covers: the built-in stub engine survives alongside the three bundled
League of Agents grid-lane launch modes, each grid-lane registry key
produces a correctly-configured :class:`~league_site.game.adapter.GridLaneEngine`,
the bundled mode-name list is never allowed to silently drift from
:mod:`league_site.game.modes`'s own registry, and — the whole point of the
"lazy factory" requirement in this task — building the registry (what
``with_api``/``site_app`` do once per construction/cold start) never
imports :mod:`league_site.game` until a grid-lane factory is actually
*called*.
"""

from __future__ import annotations

import sys

from league_site.api.engines import DEFAULT_ENGINE_REGISTRY, DEFAULT_MODE
from league_site.api.registry import GRID_LANE_LAUNCH_MODES, default_engine_registry


def test_default_engine_registry_still_carries_the_stub_engine() -> None:
    registry = default_engine_registry()
    assert DEFAULT_MODE in registry
    assert registry[DEFAULT_MODE] is DEFAULT_ENGINE_REGISTRY[DEFAULT_MODE]


def test_default_engine_registry_carries_all_three_bundled_launch_modes() -> None:
    registry = default_engine_registry()
    assert set(GRID_LANE_LAUNCH_MODES) <= set(registry)
    assert GRID_LANE_LAUNCH_MODES == ("solo-vs-bot", "team-vs-team", "coop-2")


def test_grid_lane_launch_modes_never_drifts_from_league_site_game_modes() -> None:
    """The hardcoded tuple duplicates ``league_site.game.modes.mode_names()``
    on purpose (see the module docstring: importing ``league_site.game`` at
    module scope here would defeat the lazy-factory requirement) — this test
    is what keeps that duplication honest."""
    from league_site.game import modes

    assert set(GRID_LANE_LAUNCH_MODES) == set(modes.mode_names())


def test_each_grid_lane_factory_builds_a_grid_lane_engine_for_its_own_mode() -> None:
    from league_site.game.adapter import GAME_ID, GridLaneEngine

    registry = default_engine_registry()
    for mode_name in GRID_LANE_LAUNCH_MODES:
        engine = registry[mode_name]()
        assert isinstance(engine, GridLaneEngine)
        assert engine.game_id == GAME_ID
        assert engine.mode.name == mode_name


def test_default_engine_registry_factories_return_a_fresh_instance_each_call() -> None:
    registry = default_engine_registry()
    factory = registry["solo-vs-bot"]
    assert factory() is not factory()


def test_calling_default_engine_registry_does_not_import_league_site_game() -> None:
    """Building the registry dict itself (what ``with_api`` does once per
    construction) must never trigger ``import league_site.game`` — only
    actually *calling* one of the grid-lane factories may. Evicts any
    lingering ``league_site.game*``/``league_site.api.registry`` modules
    first so this assertion isn't order-dependent on whatever another test
    in this same worker process already imported."""
    for name in list(sys.modules):
        if name == "league_site.api.registry" or name.startswith("league_site.game"):
            del sys.modules[name]

    import league_site.api.registry as fresh_registry_module

    assert not any(
        name == "league_site.game" or name.startswith("league_site.game.") for name in sys.modules
    )

    registry = fresh_registry_module.default_engine_registry()
    assert not any(
        name == "league_site.game" or name.startswith("league_site.game.") for name in sys.modules
    )

    # Only *calling* a grid-lane factory pays the import cost.
    registry["solo-vs-bot"]()
    assert any(
        name == "league_site.game" or name.startswith("league_site.game.") for name in sys.modules
    )
