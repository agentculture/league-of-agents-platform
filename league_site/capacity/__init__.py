"""Safe-capacity safeguards, price-aware cleanup config, and telemetry.

Three independent, composable pieces:

* :mod:`league_site.capacity.config` — :class:`CapacityConfig`, the
  committed hard caps (+ documented price reasoning against the platform's
  20 USD/month ceiling).
* :mod:`league_site.capacity.guard` — :func:`check_capacity`, the pure
  allow/refuse gate the match-create path calls against a
  :class:`~league_site.matches.store.MatchStore`.
* :mod:`league_site.capacity.telemetry` — :func:`telemetry_snapshot`, the
  month-one counters (registrations, completed matches, distinct
  providers) read from injected stores.

The scheduled cleanup job that archives/deletes stale match state using
:class:`CapacityConfig` lives in :mod:`league_site.aws_lambda.cleanup`
(alongside the other Lambda entrypoints, not in this package, since it is a
handler, not a domain module).
"""

from __future__ import annotations

from league_site.capacity.config import CEILING_USD, CapacityConfig
from league_site.capacity.guard import ALLOW, Allow, CapacityDecision, Refusal, check_capacity
from league_site.capacity.telemetry import telemetry_snapshot

__all__ = [
    "ALLOW",
    "CEILING_USD",
    "Allow",
    "CapacityConfig",
    "CapacityDecision",
    "Refusal",
    "check_capacity",
    "telemetry_snapshot",
]
