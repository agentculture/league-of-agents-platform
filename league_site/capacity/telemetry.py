"""Month-one telemetry: registrations, completed matches, distinct providers.

h26's requirement is that these three numbers are readable at any time —
:func:`telemetry_snapshot` is that single read. It takes every source as an
optional, independently injectable argument and returns a plain ``dict``:
no class to instantiate, no persistent counter to keep in sync, just a
snapshot computed fresh from whatever stores are handed to it. The operator
CLI (a later task) prints this dict directly (``--json`` mode: the dict
verbatim; plain mode: a rendered table), and it is what the month-one
target — 100 players, 500 matches, 3+ providers, see
``docs/architecture.md``'s capacity section — reads to know where the
platform stands.

Registrations without a human registry
----------------------------------------
Agent registrations have a natural source: every issued
:class:`~league_site.auth.token_store.TokenRecord`. But
:class:`~league_site.auth.token_store.TokenStore` (see that module) exposes
lookup by hash only — no enumeration method exists yet — and there is no
committed human-registration store at all (human sessions are stateless
signed tokens, see :mod:`league_site.auth.sessions`; nothing durable is
written at login). Rather than block this task on adding enumeration to a
package this task does not own (:mod:`league_site.auth`), this function
accepts the *records* directly (``agent_tokens``) and a plain collection of
human subject ids (``human_subjects``) — whatever later plumbing enumerates
those stores hands their contents to this function unchanged. Both default
to empty, so a caller that has not wired one up yet still gets a correct
(zero) count for it rather than an error.
"""

from __future__ import annotations

from collections.abc import Iterable

from league_site.auth.token_store import TokenRecord
from league_site.matches.match import MatchStatus
from league_site.matches.models import ParticipantKind
from league_site.matches.store import MatchStore
from league_site.ratings.ledger import RatingLedgerStore


def telemetry_snapshot(
    *,
    match_store: MatchStore | None = None,
    rating_store: RatingLedgerStore | None = None,
    agent_tokens: Iterable[TokenRecord] = (),
    human_subjects: Iterable[str] = (),
) -> dict[str, int]:
    """Return ``{"registrations": ..., "completed_matches": ..., "distinct_providers": ...}``.

    Every source is optional and independently injectable; an omitted
    source contributes ``0`` to whichever counter(s) it feeds rather than
    raising, since a telemetry read must always succeed even when only some
    stores are wired up in the caller's context.

    * ``registrations`` = distinct agent tokens in ``agent_tokens`` (deduped
      by ``token_id`` — re-issuing/rotating a token for the same
      ``agent_name`` should not double-count a registration) plus distinct
      entries in ``human_subjects`` (deduped as given; callers should pass
      each human's durable ``(provider, subject)`` key, not a display name,
      to avoid two different accounts colliding on a shared display name).
    * ``completed_matches`` = matches in ``match_store`` with status
      ``COMPLETED`` (0 if ``match_store`` is ``None``).
    * ``distinct_providers`` = distinct model providers among ``AGENT``
      identities recorded in ``rating_store`` — i.e. providers represented
      *on the leaderboard* (see
      :func:`league_site.ratings.leaderboard.leaderboard`), matching h26's
      "provider identity per agent" phrasing (0 if ``rating_store`` is
      ``None``).
    """
    return {
        "registrations": _count_registrations(agent_tokens, human_subjects),
        "completed_matches": _count_completed_matches(match_store),
        "distinct_providers": _count_distinct_providers(rating_store),
    }


def _count_registrations(agent_tokens: Iterable[TokenRecord], human_subjects: Iterable[str]) -> int:
    distinct_agent_ids = {record.token_id for record in agent_tokens}
    distinct_human_subjects = set(human_subjects)
    return len(distinct_agent_ids) + len(distinct_human_subjects)


def _count_completed_matches(match_store: MatchStore | None) -> int:
    if match_store is None:
        return 0
    return sum(
        1
        for match_id in match_store.list_ids()
        if match_store.load(match_id).status is MatchStatus.COMPLETED
    )


def _count_distinct_providers(rating_store: RatingLedgerStore | None) -> int:
    if rating_store is None:
        return 0
    providers = {
        identity.provider
        for identity in rating_store.all_identities()
        if identity.kind is ParticipantKind.AGENT
    }
    return len(providers)
