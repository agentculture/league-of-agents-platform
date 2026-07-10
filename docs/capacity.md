# Capacity

The committed capacity + price policy for the League of Agents platform: the
hard caps the running platform enforces, the price math they are tuned
against, what the scheduled cleanup job does and when, and how to read the
platform's telemetry counters. Implementation lives in
`league_site/capacity/` (`config.py`, `guard.py`, `telemetry.py`) and
`league_site/aws_lambda/cleanup.py`; the deploy-time wiring lives in
`infra/template.yaml`. See [architecture](architecture.md) for how this fits
the rest of the platform and [deploy](deploy.md) for the general AWS stack.

## Why hard caps, not degraded service

h5's requirement is specific: **new matches over the configured cap are
refused, not degraded.** `league_site.capacity.guard.check_capacity` is a
pure function over a `MatchStore`'s current counts that returns one of two
structured values — `ALLOW` or a `Refusal(reason, current, limit)` — never
an exception. A `Refusal` is meant to map directly to a 429/409-style JSON
error at the API layer; there is no fallback path where an over-cap request
is served anyway at reduced quality.

**Wiring note (post-merge):** the match-create path should call
`check_capacity(store, config)` before constructing a new `Match`, and
refuse the request whenever the result is falsy (`bool(result) is False`,
i.e. a `Refusal`).

## The caps

| Field | Default | What it bounds |
| --- | --- | --- |
| `max_concurrent_matches` | 50 | Matches with status `ACTIVE` or `PAUSED` at once — the hot, playable surface. |
| `max_stored_matches` | 500 | Every persisted match regardless of status — the hot DynamoDB table's total row count. |
| `max_match_age_days_hot` | 3 | Days a completed match stays in the hot table before the cleanup job archives it to S3. |
| `max_archive_age_days` | 180 | Days an S3 archive is retained before the cleanup job deletes it outright. |
| `ceiling_usd` | 20 | The fixed design ceiling every number above is tuned against — not a per-deploy dial (see `infra/template.yaml`'s `MonthlyBudgetUsd` parameter for the actual AWS Budget enforcement, a separate mechanism). |

Every field except `ceiling_usd` can be overridden at deploy time via
`LEAGUE_CAPACITY_MAX_*` environment variables
(`league_site.capacity.config.CapacityConfig.from_env`), themselves fed by
`infra/template.yaml`'s `MaxConcurrentMatches` / `MaxStoredMatches` /
`MaxMatchAgeDaysHot` / `MaxArchiveAgeDays` parameters.
`tests/test_lambda_template.py` cross-checks the template's parameter
defaults against `CapacityConfig.default()` directly, so the two cannot
silently drift apart.

## The price math against 20 USD

The honest finding, worked through the actual DynamoDB/S3/Lambda envelope
math for this platform's traffic shape (small, turn-based JSON documents —
not media, not bulk data): **these services are nowhere close to
threatening the 20 USD ceiling at any plausible scale for this project.**
The caps below exist as an unconditional circuit breaker, not a reaction to
an observed or imminent danger — the same posture `infra/template.yaml`
already documents for `HttpApi`'s request-rate throttle (burst 20, rate
10/s). The throttle bounds how *fast* a client can generate work; these
caps bound how much *standing state* (open matches, stored matches, S3
archives) the platform carries at any instant, regardless of how slowly
that state was accumulated — a client well within the per-second throttle
could still, over hours, create thousands of matches that never get
cleaned up, and the throttle alone would not catch that.

Approximate published on-demand rates used below (order-of-magnitude, not
exact quotes — good enough for the "documented reasoning" this task asks
for):

- **DynamoDB (on-demand)**: ~1.25 USD per million write-request-units,
  ~0.25 USD per million read-request-units, ~0.25 USD/GB-month storage.
- **S3**: Standard ~0.023 USD/GB-month, Standard-IA (this stack's 30-day
  lifecycle tier) ~0.0125 USD/GB-month, Glacier (90-day tier) ~0.0036
  USD/GB-month; PUT requests ~0.005 USD per 1,000 (DELETE is free).
- **Lambda (arm64)**: ~0.0000133334 USD per GB-second, ~0.20 USD per
  million requests.

Working the month-one target (100 registered players, 500 completed
matches, 3+ providers — see [architecture](architecture.md)'s capacity
section) through that math:

- **DynamoDB requests**: a single match (create, ~20 turns, a pause/resume
  pair, complete) is roughly 24 writes and 25 reads. At 500 matches/month
  that is ~12,000 WRU and ~12,500 RRU: **about 2 cents/month.** Even at
  100x that volume (50,000 matches/month, far past anything month-one
  traffic implies) it stays under 2 USD/month.
- **DynamoDB storage**: bounded directly by `max_stored_matches`. 500 items
  at a generous 50 KB average (nowhere near DynamoDB's 400 KB item limit)
  is 0.025 GB, ~0.6 cents/month; even every item at the hard 400 KB
  ceiling is only 200 MB, ~5 cents/month.
- **S3 archive storage**: at steady state under `max_archive_age_days=180`
  (six months' retention before a hard delete), month-one traffic
  accumulates at most ~3,000 archived matches (6 × 500/month) — well under
  half a GB even at 50 KB/archive, costing a fraction of a cent/month, most
  of it in the cheapest (Glacier) tier by the time it is deleted.
- **Lambda (the cleanup job itself)**: 30 invocations/month, 256 MB, a
  generous 10s runtime (well under its 60s timeout) is ~75 GB-seconds/month
  — a tenth of a cent.

Total capacity/cleanup-related AWS spend at month-one scale: well under a
nickel a month, a rounding error against the 20 USD ceiling. The caps exist
so that stays true unconditionally as traffic evolves — a runaway client, a
caller that never pauses or completes its matches, or genuine growth well
past the month-one target — not because today's numbers are close to
dangerous. They also satisfy the platform's own product requirement,
independent of cost: new matches over a configured cap must be refused,
not degraded.

See `league_site/capacity/config.py`'s module docstring for the full,
field-by-field version of this reasoning (it is the single source of
truth; this doc summarizes it).

## What the cleanup job does, and when

`league_site.aws_lambda.cleanup.handler` runs daily, triggered by
`infra/template.yaml`'s `CleanupFunction` EventBridge `Schedule` event
(`rate(1 day)`). Every run is a three-pass sweep, always in this order:

1. **Archive hot-stale completed matches.** A completed match older (by
   `updated_at`) than `max_match_age_days_hot` is archived to S3
   (`league_site.matches.aws.S3MatchArchive`) and deleted from the hot
   DynamoDB table.
2. **Delete aged-out S3 archives.** Every object under the archive
   bucket's `archives/` prefix older (by S3's own `LastModified`) than
   `max_archive_age_days` is deleted outright — a hard delete, nothing is
   re-archived from here. This is safe because the platform's durable
   historical record is the versioned JSONL dataset export
   (`league_site.datasets.export`, see [dataset-schema](dataset-schema.md)),
   not this raw per-match archive.
3. **Enforce `max_stored_matches` overflow.** If the hot table still holds
   more matches than the cap, the oldest `COMPLETED` matches (by
   `updated_at`, ascending) are archived and deleted, oldest-first, until
   back under the cap. Only `COMPLETED` matches are ever archived here —
   overflow made up of `ACTIVE`/`PAUSED` matches is `check_capacity`'s job
   (refusing *new* matches), not this job's; archiving a match that has
   not reached a terminal state would throw away live game state.

Every dependency (the match store, the S3 archive, the raw S3 client, the
current time) is injected, so the whole sweep runs against fakes in tests —
see `tests/test_cleanup_handler.py` — with zero real AWS calls.

**Dry-run mode**: invoke the function with `{"dry_run": true}` in the event
payload (EventBridge lets an operator set a fixed JSON input on the
schedule target, or invoke the function manually with a test event) to get
back every action the sweep *would* take without calling
`archive`/`delete`/`delete_object`. Dry-run and real runs compute the
identical action list — a match one pass would have removed is excluded
from the next pass's counts even in dry-run — so a dry-run preview is
exactly what a real run would do, never an approximation of it.

**Logging**: every action (real or dry-run) is logged as one structured
JSON line (`{"dry_run": ..., "kind": ..., "match_id": ..., "reason": ...}`)
via the standard `logging` module, so CloudWatch Logs carries a full,
greppable audit trail of every archive/delete decision. `handler` also
returns the full `CleanupReport` as a JSON-shaped dict
(`{"dry_run": ..., "action_count": ..., "actions": [...]}`), which the
operator CLI's `league-site cleanup [--dry-run] [--json]` verb (a later
task) will print directly.

**Production status**: `handler` builds a real `DynamoDBMatchStore` from
`MATCHES_TABLE_NAME`. That store's `list_ids` is not implemented yet — it
needs a GSI on status/updated_at (see `league_site/matches/aws.py`'s module
docstring) — so this job cannot run against the *deployed* table until that
GSI lands. That is the same, already-documented limitation
`league_site.capacity.guard` calls out for the create-match path, not a new
gap this job introduces. The sweep logic itself is fully implemented and
fully tested against in-memory/fake stores today.

## How to read telemetry

`league_site.capacity.telemetry.telemetry_snapshot` returns the three
month-one numbers h26 asks for as a plain dict:

```python
{
    "registrations": 0,       # distinct agent tokens + distinct human subjects
    "completed_matches": 0,   # matches with status COMPLETED
    "distinct_providers": 0,  # distinct model providers on the leaderboard
}
```

Every source is an optional, independently injectable argument
(`match_store`, `rating_store`, `agent_tokens`, `human_subjects`) — an
omitted source contributes `0` to whichever counter(s) it feeds rather than
raising, since a telemetry read must always succeed even when only some
stores are wired up in the caller's context. `agent_tokens` and
`human_subjects` are plain iterables rather than store objects because
`TokenStore` (see `league_site/auth/token_store.py`) exposes lookup by hash
only — no enumeration primitive exists yet — and there is no committed
human-registration store at all (human sessions are stateless signed
tokens, see `league_site/auth/sessions.py`). Whatever later plumbing
enumerates those stores hands their contents to this function unchanged.

The operator CLI's `league-site telemetry [--json]` verb (a later task)
will print this dict directly — plain mode as a rendered summary, `--json`
mode as the dict verbatim — and is what the month-one target (100 players,
500 matches, 3+ providers) reads to know where the platform stands.
