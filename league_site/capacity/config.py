"""Committed capacity config: the hard caps ``check_capacity`` enforces.

A :class:`CapacityConfig` is loaded from committed defaults
(:meth:`CapacityConfig.default`) with optional environment-variable
overrides (:meth:`CapacityConfig.from_env`) — the same "committed default +
env override" shape :mod:`league_site.auth.oauth` uses for its provider
credentials, so an operator can retune caps at deploy time
(``infra/template.yaml``'s ``Environment.Variables``, see that file's
``MaxConcurrentMatches``/``MaxStoredMatches``/``MaxMatchAgeDaysHot``/
``MaxArchiveAgeDays`` parameters) without a code change.

Price reasoning (why these specific numbers)
---------------------------------------------
The platform's hard 20 USD/month ceiling (:data:`CEILING_USD`, mirrored by
``infra/template.yaml``'s ``MonthlyBudgetUsd`` parameter) is enforced two
ways today: ``HttpApi``'s request-rate throttle (burst 20, rate 10/s — see
``infra/template.yaml``) bounds how fast a client can *generate* work, and
the caps below bound how much *standing state* (open matches, stored
matches) the platform carries at any instant regardless of how slowly that
state was accumulated. A client well within the per-second throttle can
still, over hours, create thousands of matches that never get cleaned up —
the throttle alone does not catch that; these caps do.

Working the actual DynamoDB/S3/Lambda envelope math against this
platform's traffic shape (small, turn-based JSON documents, not media or
bulk data) shows these services are not, in fact, close to threatening the
20 USD ceiling at any plausible scale for this project — the honest
finding, not a hedge:

* **DynamoDB (on-demand)**: roughly 1.25 USD per million write-request-units
  and 0.25 USD per million read-request-units, plus ~0.25 USD/GB-month
  storage (approximate published on-demand rates; see
  ``infra/template.yaml``'s ``MatchesTable`` comment for why on-demand
  billing was chosen at all). A single match — create, ~20 turns, a
  pause/resume pair, complete — is roughly 24 writes and 25 reads. At this
  platform's month-one target (500 completed matches, from
  ``docs/architecture.md``'s capacity section), that is ~12,000 WRU and
  ~12,500 RRU: **about 2 cents/month**. Even at 100x that volume (50,000
  matches/month — far beyond anything month-one traffic implies) it is
  still under 2 USD/month. Hot-table storage is bounded by
  ``max_stored_matches`` directly: 500 items at a generous 50 KB average
  (this platform's matches are small JSON turn logs, nowhere near
  DynamoDB's 400 KB item ceiling) is 0.025 GB, ~0.6 cents/month; even every
  item at the hard 400 KB ceiling is only 200 MB, ~5 cents/month.
* **S3**: Standard storage ~0.023 USD/GB-month, Standard-IA (this stack's
  30-day lifecycle tier) ~0.0125 USD/GB-month, Glacier (90-day tier)
  ~0.0036 USD/GB-month; PUT requests ~0.005 USD per 1,000 (DELETE is free).
  At steady state under ``max_archive_age_days=180`` (six months' retention
  before a hard delete), month-one traffic accumulates at most ~3,000
  archived matches (6 x 500/month) — well under half a GB even at a
  generous 50 KB/archive — costing a fraction of a cent/month, most of it
  in the cheapest (Glacier) tier by the time it is deleted.
* **Lambda (arm64)**: ~0.0000133334 USD per GB-second + 0.20 USD per
  million requests. The daily cleanup invocation (30/month, 256 MB, a
  generous 10s runtime well under its 60s timeout) is ~75 GB-seconds/month,
  a tenth of a cent.

In short: the caps below are not "or the platform blows the budget" —
DynamoDB/S3/Lambda spend for this workload shape stays a rounding error
against 20 USD across a wide range of plausible traffic. They exist so
that stays true *unconditionally*, independent of how traffic actually
evolves (a runaway client, a bug in a caller that never pauses/completes
matches, or genuine growth well past the month-one target) — the same
"circuit breaker, not a reaction to observed danger" posture
``infra/template.yaml`` already documents for ``HttpApi``'s throttle
limits. They also satisfy the platform's own product requirement,
independent of cost: new matches over a configured cap must be refused,
not degraded (see :mod:`league_site.capacity.guard`).

Field-by-field:

* ``max_concurrent_matches`` (default 50): matches with status ``ACTIVE``
  or ``PAUSED`` at once — the "hot" playable surface. 50 is generous
  against the month-one target of 100 registered players (not all of whom
  are mid-match simultaneously) while keeping the hot-state footprint (and
  therefore the DynamoDB/Lambda envelope above) small by construction.
* ``max_stored_matches`` (default 500): every match still in the hot
  DynamoDB table, regardless of status (active, paused, or completed but
  not yet archived). 500 matches the month-one completed-match target
  exactly, so the overflow-eviction path in
  :mod:`league_site.aws_lambda.cleanup` (archive oldest-first once this cap
  is hit) only engages once the platform is meaningfully past month-one
  scale, not on day one.
* ``max_match_age_days_hot`` (default 3): a completed match stays in the
  hot table for 3 days — long enough for its players to view/share the
  finished match at full DynamoDB read latency immediately after playing —
  before the cleanup job archives it to S3 and deletes the hot copy.
* ``max_archive_age_days`` (default 180): raw per-match S3 archives are
  deleted after 6 months. The platform's durable historical record is the
  versioned JSONL dataset export (``league_site.datasets.export``, see
  ``docs/dataset-schema.md``), not this raw archive — 180 days is ample
  time for that export to run before the per-match S3 copy is reclaimed.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, fields, replace

#: The platform's hard monthly AWS cost ceiling in USD. Mirrors
#: ``infra/template.yaml``'s ``MonthlyBudgetUsd`` parameter (also defaulted
#: to 20) — documented here as a plain constant so any code that reasons
#: about capacity/cost has it available without importing infra YAML. See
#: this module's docstring for the price math the caps below are tuned
#: against relative to this ceiling.
CEILING_USD = 20

#: Environment variable name prefix for :meth:`CapacityConfig.from_env`
#: overrides, e.g. ``LEAGUE_CAPACITY_MAX_CONCURRENT_MATCHES``. Matches
#: ``infra/template.yaml``'s ``CleanupFunction``/``HttpHandlerFunction``
#: ``Environment.Variables`` names.
ENV_PREFIX = "LEAGUE_CAPACITY_"

#: Config fields eligible for an env-var override, in the order
#: :meth:`CapacityConfig.from_env` checks them. ``ceiling_usd`` is
#: deliberately excluded — it documents the fixed design ceiling this
#: config is tuned against, not a per-deploy dial (the real budget
#: enforcement is ``infra/template.yaml``'s ``MonthlyBudgetUsd`` parameter,
#: a separate mechanism).
_OVERRIDABLE_FIELDS: tuple[str, ...] = (
    "max_concurrent_matches",
    "max_stored_matches",
    "max_match_age_days_hot",
    "max_archive_age_days",
)


@dataclass(frozen=True)
class CapacityConfig:
    """Hard caps + retention windows the running platform enforces.

    See the module docstring for the price reasoning behind every default
    and the ``max_*`` semantics. Immutable — build overrides via
    :func:`dataclasses.replace` or :meth:`from_env`, never by mutation.
    """

    max_concurrent_matches: int = 50
    max_stored_matches: int = 500
    max_match_age_days_hot: int = 3
    max_archive_age_days: int = 180
    ceiling_usd: int = CEILING_USD

    def __post_init__(self) -> None:
        for field_def in fields(self):
            if field_def.name == "ceiling_usd":
                continue
            value = getattr(self, field_def.name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{field_def.name} must be a positive integer, got {value!r}")

    @classmethod
    def default(cls) -> CapacityConfig:
        """The committed default config (no environment overrides applied)."""
        return cls()

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CapacityConfig:
        """Build a config from :meth:`default`, overridden by ``LEAGUE_CAPACITY_*`` env vars.

        ``env`` defaults to ``os.environ``; tests should pass an explicit
        mapping rather than monkeypatching the real environment. Only the
        fields named in ``_OVERRIDABLE_FIELDS`` are ever read from the
        environment; an unset or absent variable leaves that field at its
        committed default. A present-but-non-integer value raises
        ``ValueError`` naming the offending variable, so a deploy-time typo
        fails loudly instead of silently keeping the default.
        """
        source = os.environ if env is None else env
        overrides: dict[str, int] = {}
        for field_name in _OVERRIDABLE_FIELDS:
            env_var = ENV_PREFIX + field_name.upper()
            raw_value = source.get(env_var)
            if raw_value is None or raw_value == "":
                continue
            try:
                overrides[field_name] = int(raw_value)
            except ValueError as exc:
                raise ValueError(
                    f"environment variable {env_var!r} must be an integer, got {raw_value!r}"
                ) from exc
        return replace(cls.default(), **overrides)
