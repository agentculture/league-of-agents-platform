"""Profile assembly: identity + rating + history + recent matches, from stores.

:func:`build_profile` is the one place this package joins
:mod:`league_site.ratings` (an identity's current standing and its
append-only ``(match_id, delta, resulting_rating)`` ledger history — see
:class:`~league_site.ratings.ledger.IdentityRating`) with
:mod:`league_site.matches` (the actual :class:`~league_site.matches.match.Match`
records the ledger's ``match_id``\\ s point at) into one read-only view a page,
an og-image card, a badge, or a JSON endpoint can render without knowing
about either store's internals.

Identity slugs
--------------
:func:`identity_slug` is the URL-safe, deterministic name for a
:class:`~league_site.ratings.system.RatingIdentity`. The scheme is::

    <kind>-<slug(display_name)>[-<slug(model)>-<slug(provider)>]-<hash8>

``hash8`` is the first 8 hex characters of ``sha256`` over a canonical,
field-separated encoding of ``(kind, display_name, model, provider)`` — the
exact tuple that makes two :class:`RatingIdentity`\\ s distinct (see that
class's docstring). Two distinct identities therefore get two different
hashes for all practical purposes (a ``sha256`` collision is not a real
concern at any realistic roster size), so a slug always round-trips back to
exactly one identity: build ``{identity_slug(identity): identity for identity
in store.all_identities()}`` once and look the URL segment up in it (see
:mod:`league_site.profiles.wsgi`). The human-readable ``<kind>-<name>[...]``
prefix exists only for readability/SEO (it slugifies hostile or unicode
display names down to ``a-z0-9-``, falling back to ``x`` if nothing
survives) — it plays no role in uniqueness, so it never needs to be
"perfect."
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from league_site.matches.errors import MatchNotFoundError
from league_site.matches.match import Match, MatchStatus
from league_site.matches.models import ParticipantKind
from league_site.matches.store import MatchStore
from league_site.ratings.ledger import RatingEntry, RatingLedgerStore
from league_site.ratings.system import RatingIdentity

#: Number of hex characters of the identity hash kept in a slug. 8 hex
#: characters is 32 bits of entropy over an already-injective input (see
#: module docstring) — comfortably collision-free for any real roster.
_SLUG_HASH_LENGTH = 8

#: Default cap on how many recent matches :func:`build_profile` resolves.
_DEFAULT_RECENT_LIMIT = 10

_SLUG_UNSAFE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Lossy, URL-safe rendering of *text*: lowercase ``a-z0-9`` joined by ``-``.

    Never raises and never returns an empty string — a *text* with no
    surviving characters (all punctuation/emoji/whitespace, e.g. ``"<3>"``)
    falls back to ``"x"`` so callers can always join it into a non-empty URL
    segment.
    """
    collapsed = _SLUG_UNSAFE.sub("-", text.strip().lower()).strip("-")
    return collapsed or "x"


def _canonical_identity_key(identity: RatingIdentity) -> str:
    """Field-separated encoding of the exact tuple that makes identities distinct.

    Uses ``"\\x1f"`` (ASCII unit separator, vanishingly unlikely to appear in
    a display name) between fields so e.g. ``("ab", "c")`` and ``("a", "bc")``
    never collapse to the same string before hashing.
    """
    fields = (
        identity.kind.value,
        identity.display_name,
        identity.model or "",
        identity.provider or "",
    )
    return "\x1f".join(fields)


def identity_slug(identity: RatingIdentity) -> str:
    """Deterministic, URL-safe slug for *identity*. See module docstring for the scheme."""
    parts = [identity.kind.value, slugify(identity.display_name)]
    if identity.kind is ParticipantKind.AGENT:
        parts.append(slugify(identity.model or ""))
        parts.append(slugify(identity.provider or ""))
    digest = hashlib.sha256(_canonical_identity_key(identity).encode("utf-8")).hexdigest()
    parts.append(digest[:_SLUG_HASH_LENGTH])
    return "-".join(parts)


def slug_index(ledger_store: RatingLedgerStore) -> dict[str, RatingIdentity]:
    """Return ``{identity_slug(identity): identity}`` for every ledgered identity.

    Built fresh from :meth:`~league_site.ratings.ledger.RatingLedgerStore.all_identities`
    on every call — there is no cache, so a newly recorded match's identity is
    resolvable immediately.
    """
    return {identity_slug(identity): identity for identity in ledger_store.all_identities()}


@dataclass(frozen=True)
class RecentMatch:
    """One completed-or-in-progress match from an identity's point of view."""

    match_id: str
    game_id: str
    status: str
    opponents: tuple[str, ...]
    outcome: str
    """``"win"`` | ``"loss"`` | ``"draw"`` | ``"unscored"`` (not completed, or
    this identity had no recorded score)."""


@dataclass(frozen=True)
class Profile:
    """A read-only, render-ready view of one identity: standing + history + recent matches."""

    identity: RatingIdentity
    slug: str
    rating: int
    match_count: int
    history: tuple[RatingEntry, ...]
    recent_matches: tuple[RecentMatch, ...]

    @property
    def display_name(self) -> str:
        return self.identity.display_name

    @property
    def kind(self) -> str:
        return self.identity.kind.value

    @property
    def is_agent(self) -> bool:
        return self.identity.kind is ParticipantKind.AGENT

    @property
    def model(self) -> str | None:
        return self.identity.model

    @property
    def provider(self) -> str | None:
        return self.identity.provider


def build_profile(
    identity: RatingIdentity,
    ledger_store: RatingLedgerStore,
    match_store: MatchStore,
    *,
    recent_limit: int = _DEFAULT_RECENT_LIMIT,
) -> Profile:
    """Assemble the full :class:`Profile` for *identity*.

    Reads current standing + history from *ledger_store*
    (:meth:`~league_site.ratings.ledger.RatingLedgerStore.get` — never raises,
    an identity with no history gets the initial-rating profile). Recent
    matches are resolved from *ledger_store*'s own history (most-recent-first,
    capped at *recent_limit*) via *match_store* — a ``match_id`` the ledger
    remembers but *match_store* no longer has (e.g. pruned/archived) is
    skipped rather than treated as an error, since the ledger and match store
    are independent stores with no referential-integrity guarantee between
    them.
    """
    standing = ledger_store.get(identity)
    recent = _recent_matches(identity, standing.history, match_store, limit=recent_limit)
    return Profile(
        identity=identity,
        slug=identity_slug(identity),
        rating=standing.rating,
        match_count=standing.match_count,
        history=standing.history,
        recent_matches=recent,
    )


def _recent_matches(
    identity: RatingIdentity,
    history: tuple[RatingEntry, ...],
    match_store: MatchStore,
    *,
    limit: int,
) -> tuple[RecentMatch, ...]:
    results: list[RecentMatch] = []
    for entry in reversed(history):
        if len(results) >= limit:
            break
        try:
            match = match_store.load(entry.match_id)
        except MatchNotFoundError:
            continue
        results.append(_summarize_match(identity, match))
    return tuple(results)


def _summarize_match(identity: RatingIdentity, match: Match) -> RecentMatch:
    own_ids = {
        participant.participant_id
        for participant in match.participants
        if RatingIdentity.from_participant(participant) == identity
    }
    opponents = tuple(
        participant.display_name
        for participant in match.participants
        if participant.participant_id not in own_ids
    )
    outcome = "unscored"
    if match.status is MatchStatus.COMPLETED and match.result is not None:
        scores = match.result.scores
        own_scores = [scores[pid] for pid in own_ids if pid in scores]
        other_scores = [value for pid, value in scores.items() if pid not in own_ids]
        if own_scores and other_scores:
            best_own = max(own_scores)
            best_other = max(other_scores)
            if best_own > best_other:
                outcome = "win"
            elif best_own < best_other:
                outcome = "loss"
            else:
                outcome = "draw"
    return RecentMatch(
        match_id=match.match_id,
        game_id=match.game_id,
        status=match.status.value,
        opponents=opponents,
        outcome=outcome,
    )
