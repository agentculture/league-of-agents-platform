"""Builds the default game-engine registry :func:`~league_site.api.wsgi.with_api`
(and, through it, :func:`~league_site.web.http.site_app` / the Lambda
handler) uses when no ``engine_registry`` override is injected: the
built-in stub engine (:mod:`league_site.api.engines`) plus the real League
of Agents grid-lane game (:mod:`league_site.game.adapter`), one registry
key per bundled launch mode.

Registry keys and ``match.game_id``
------------------------------------
:mod:`league_site.matches` has no separate "mode" field — the create-match
API's ``mode`` request field *is* the persisted ``match.game_id``
(:mod:`league_site.api.wsgi`'s ``_handle_create_match``/``_handle_take_turn``
both look an engine up from the registry by exactly this one key, at create
time and again on every later turn — see that module's docstring).
:class:`~league_site.game.adapter.GridLaneEngine` plays three distinct
launch modes (:mod:`league_site.game.modes`) behind one constant
``game_id`` (``GAME_ID``, ``"league-of-agents-grid"``), so bridging it
honestly into this single-axis mode/``game_id`` key space means registering
each launch mode *name* as its own registry entry — ``"solo-vs-bot"``,
``"team-vs-team"``, ``"coop-2"`` — rather than the engine's own constant
``game_id``. This causes no mismatch: :class:`~league_site.matches.match.
Match` never compares ``engine.game_id`` against its own ``game_id`` (see
that module's docstring — no such check exists), it only ever asks the
registry for "the engine this match's ``game_id`` should replay with",
which a per-launch-mode registry key answers correctly on every turn,
including after a process restart re-derives the engine from scratch.

Import-boundary / cold-start discipline
------------------------------------------
Importing *any* submodule of :mod:`league_site.game` runs that package's
``__init__.py``, which eagerly imports the whole package — the grid-lane
adapter, and through it :mod:`league_site.game.runner`'s subprocess-driving
machinery. :func:`default_engine_registry` is called once per
:func:`~league_site.api.wsgi.with_api` construction (i.e. once per Lambda
cold start), so this module never imports :mod:`league_site.game` at module
scope — :data:`GRID_LANE_LAUNCH_MODES` duplicates the three bundled launch
mode names as plain string literals rather than importing
:mod:`league_site.game.modes` to read them, and each grid-lane factory
(:func:`_grid_lane_factory`) imports
:class:`~league_site.game.adapter.GridLaneEngine` *inside* its closure —
paid only the first time a caller actually creates a match in that mode,
never merely by building or holding the registry. ``tests/test_api_registry.py``
pins :data:`GRID_LANE_LAUNCH_MODES` against
:func:`league_site.game.modes.mode_names` so the duplication can't silently
drift, and separately asserts building the registry never imports
:mod:`league_site.game`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from league_site.api.engines import DEFAULT_ENGINE_REGISTRY
from league_site.matches import GameEngine

EngineFactory = Callable[[], GameEngine]

#: The bundled League of Agents grid-lane launch mode names — mirrors
#: :func:`league_site.game.modes.mode_names` exactly (pinned by
#: ``tests/test_api_registry.py``), duplicated here rather than imported to
#: keep this module's import graph free of :mod:`league_site.game` at
#: module-import time (see the module docstring).
GRID_LANE_LAUNCH_MODES: tuple[str, ...] = ("solo-vs-bot", "team-vs-team", "coop-2")


def _grid_lane_factory(mode_name: str) -> EngineFactory:
    """Build a registry factory for one bundled grid-lane launch mode.

    The import of :class:`~league_site.game.adapter.GridLaneEngine` happens
    *inside* the returned closure, not at this module's top level — see the
    module docstring's import-boundary/cold-start discipline section.
    """

    def factory() -> GameEngine:
        from league_site.game.adapter import GridLaneEngine

        return GridLaneEngine(mode_name)

    return factory


def default_engine_registry() -> Mapping[str, EngineFactory]:
    """The full default registry: the built-in stub engine plus every bundled
    grid-lane launch mode, one registry key per launch mode name (see the
    module docstring for why a launch mode name, not ``GridLaneEngine``'s
    own constant ``game_id``, is the registry key). Returns a fresh
    ``dict`` on every call — safe for a caller to mutate its own copy.
    """
    registry: dict[str, EngineFactory] = dict(DEFAULT_ENGINE_REGISTRY)
    for mode_name in GRID_LANE_LAUNCH_MODES:
        registry[mode_name] = _grid_lane_factory(mode_name)
    return registry


__all__ = ["GRID_LANE_LAUNCH_MODES", "default_engine_registry"]
