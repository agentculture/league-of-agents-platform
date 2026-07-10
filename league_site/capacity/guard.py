"""``check_capacity`` — the pure hard-cap gate new match creation must pass.

h5's requirement is specific: new matches over the configured cap are
*refused*, not degraded. That means the gate cannot raise an exception the
caller has to remember to catch (an easy place for "degraded" behavior to
creep in — e.g. a caller that catches broadly and silently proceeds) — it
returns a structured value instead, so the API layer that will call this
post-merge maps :class:`Refusal` directly to a 429/409-style JSON error and
:data:`ALLOW` directly to "proceed", with no exception handling in between.

Wiring note for the merger: the match-create path should call
``check_capacity(store, config)`` (a single import, a single call) before
constructing a new :class:`~league_site.matches.match.Match`, and refuse the
request whenever the result is a :class:`Refusal` (``bool(result) is
False``) rather than :data:`ALLOW` (``bool(result) is True``).
"""

from __future__ import annotations

from dataclasses import dataclass

from league_site.capacity.config import CapacityConfig
from league_site.matches.match import MatchStatus
from league_site.matches.store import MatchStore

#: Statuses counted against ``max_concurrent_matches`` — the "hot, playable"
#: surface. ``CREATED`` matches are deliberately excluded: a match that has
#: not called ``start()`` yet holds no game state and is not itself driving
#: ongoing DynamoDB/Lambda cost the way an active or paused match is.
_CONCURRENT_STATUSES = (MatchStatus.ACTIVE, MatchStatus.PAUSED)


@dataclass(frozen=True)
class Allow:
    """Returned by :func:`check_capacity` when a new match may be created.

    Carries no fields on purpose — "allowed" needs no further detail, unlike
    :class:`Refusal`, which must explain *why* to the caller/API layer.
    """

    def __bool__(self) -> bool:
        return True


@dataclass(frozen=True)
class Refusal:
    """Returned by :func:`check_capacity` when a configured cap would be exceeded.

    A structured value, not an exception, so the API layer maps it straight
    to a 429/409-style JSON error (``{"reason": ..., "current": ...,
    "limit": ...}``) without a try/except around the guard call.
    """

    reason: str
    current: int
    limit: int

    def __bool__(self) -> bool:
        return False


#: Canonical "allowed" result — a singleton since :class:`Allow` carries no
#: state, so every caller can compare against (or just truthiness-check)
#: this one value instead of constructing their own.
ALLOW = Allow()

#: The two possible outcomes of :func:`check_capacity`.
CapacityDecision = Allow | Refusal


def check_capacity(store: MatchStore, config: CapacityConfig) -> CapacityDecision:
    """Return :data:`ALLOW` if a new match may be created under ``config``'s caps,
    else a :class:`Refusal` naming which cap was hit.

    Pure over ``store``'s current counts — no mutation, no side effects, and
    the result depends only on what ``store`` currently holds and
    ``config``'s limits, so this is safe to call speculatively (e.g. to
    render a "capacity" status page) as well as on the create-match path.

    Checks, in order:

    1. ``max_concurrent_matches`` — matches with status ``ACTIVE`` or
       ``PAUSED``. This is the cap h5 is primarily about: it is checked
       first because it is the one the create-match path hits under normal
       (non-degenerate) traffic long before total storage would.
    2. ``max_stored_matches`` — every persisted match regardless of status.
       A secondary, broader ceiling: even if concurrency stays low, an
       unbounded stream of completed-but-not-yet-archived matches would
       otherwise grow the hot table without limit.

    Implementation note: ``MatchStore`` (see
    :mod:`league_site.matches.store`) exposes ``list_ids`` +
    ``load``/``save``/``delete`` only — there is no bulk "count by status"
    query — so this necessarily does an O(n) scan (one ``load`` per id) to
    determine concurrency. That is exactly the caps' own point: n is
    bounded by ``max_stored_matches``, so the scan itself stays cheap.
    :class:`~league_site.matches.aws.DynamoDBMatchStore` does not implement
    ``list_ids`` yet (it needs a GSI — see that module's docstring), so
    wiring this guard against the *deployed* store is blocked on that same
    follow-up work; it is fully exercised today against
    :class:`~league_site.matches.store.InMemoryMatchStore`.
    """
    match_ids = store.list_ids()

    concurrent = sum(
        1 for match_id in match_ids if store.load(match_id).status in _CONCURRENT_STATUSES
    )
    if concurrent >= config.max_concurrent_matches:
        return Refusal(
            reason="max_concurrent_matches",
            current=concurrent,
            limit=config.max_concurrent_matches,
        )

    total_stored = len(match_ids)
    if total_stored >= config.max_stored_matches:
        return Refusal(
            reason="max_stored_matches",
            current=total_stored,
            limit=config.max_stored_matches,
        )

    return ALLOW
