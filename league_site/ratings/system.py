"""Deterministic, integer-only rating engine.

Everything below the module-level table lookup is plain integer
arithmetic — addition, subtraction, multiplication, and floor/rounding
division — on purpose: floating point transcendental functions
(``pow``/``exp``/``log``) are not guaranteed bit-identical across
platforms or ``libm`` versions, so a rating computed with them could
silently drift between two machines replaying the exact same match
history. An integer-only engine sidesteps that entirely: the same
``(current_ratings, outcome)`` input always produces the exact same
``dict`` of deltas, on any machine, forever.

:class:`RatingIdentity` is the ledger key. Humans are identified by
display name alone; agents are identified by display name *and* model
*and* provider (the same triple :class:`~league_site.matches.models.AgentIdentity`
carries), since two different models could plausibly share a display
name. :func:`outcome_from_match` is the one place this package touches
:mod:`league_site.matches` — it reads a completed
:class:`~league_site.matches.match.Match` and its
:class:`~league_site.matches.models.MatchResult` and maps them onto the
identity + hard-score shape :class:`RatingSystem` implementations consume.
That mapping is also the one place a ``float`` (the engine's
``scores: dict[str, float]``) is allowed to exist on the way in — it is
converted to ``int`` immediately via :func:`round`, before anything is
computed or stored.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass

from league_site.matches.match import Match, MatchStatus
from league_site.matches.models import Participant, ParticipantKind

#: Starting rating assigned to an identity with no recorded match history.
#: 1500 is the conventional Elo midpoint (USCF/FIDE use the same default).
INITIAL_RATING = 1500


@dataclass(frozen=True)
class RatingIdentity:
    """Ledger key for one human or agent identity.

    Two agent :class:`Participant`\\ s with the same ``display_name`` but a
    different model or provider are deliberately *different* identities —
    the ledger tracks model/provider performance, not display-name
    performance.
    """

    kind: ParticipantKind
    display_name: str
    model: str | None = None
    provider: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ParticipantKind.AGENT and (self.model is None or self.provider is None):
            raise ValueError("agent identities require both model and provider")
        if self.kind is ParticipantKind.HUMAN and (
            self.model is not None or self.provider is not None
        ):
            raise ValueError("human identities must not carry model/provider")

    @classmethod
    def from_participant(cls, participant: Participant) -> RatingIdentity:
        """Build the ledger identity for a :class:`Participant`."""
        agent_identity = participant.agent_identity
        return cls(
            kind=participant.kind,
            display_name=participant.display_name,
            model=agent_identity.model if agent_identity is not None else None,
            provider=agent_identity.provider if agent_identity is not None else None,
        )

    def sort_key(self) -> tuple[str, str, str, str]:
        """Stable, total ordering key: ascending by ``(kind, display_name, model, provider)``.

        ``kind`` is compared as its plain string value (``"agent"`` sorts
        before ``"human"``); ``model``/``provider`` are ``None`` for humans
        and compare as ``""``. Because those four fields are exactly the
        fields that make two identities distinct (see the dataclass
        equality), this key is injective over distinct identities — no two
        different identities ever produce the same key, so sorting by it
        alone is a total order with no further tie-break needed.
        """
        return (self.kind.value, self.display_name, self.model or "", self.provider or "")


@dataclass(frozen=True)
class OutcomeEntry:
    """One participant's hard score within a :class:`MatchOutcome`."""

    identity: RatingIdentity
    score: int


@dataclass(frozen=True)
class MatchOutcome:
    """The ordered outcome of one completed match, ready for rating.

    ``entries`` need not be sorted or deduplicated by the caller — every
    :class:`RatingSystem` implementation in this module is required to
    produce identical output regardless of the order ``entries`` is given
    in, since the platform has no reason to guarantee a stable participant
    order when it builds this from a :class:`~league_site.matches.match.Match`.
    """

    match_id: str
    entries: tuple[OutcomeEntry, ...]


def outcome_from_match(match: Match) -> MatchOutcome:
    """Map a completed :class:`Match` onto a :class:`MatchOutcome`.

    Requires ``match.status is MatchStatus.COMPLETED`` and a populated
    ``match.result``. Each scored participant's hard score is
    ``round(match.result.scores[participant.participant_id])`` — the one
    spot a ``float`` (the engine's ``scores: dict[str, float]``) is
    converted to the ``int`` every downstream rating computation requires.

    A :class:`~league_site.matches.engine.GameEngine` is not required to
    score every participant (e.g. a game may only score the eventual
    winner) — see :meth:`~league_site.matches.match.Match.complete`, which
    stores whatever ``dict`` the engine's ``score()`` returns verbatim.
    Participants absent from ``match.result.scores`` are therefore skipped
    rather than treated as an error; it is
    :meth:`~league_site.ratings.system.RatingSystem.compute_deltas`'s job to
    reject an outcome that ends up with fewer than two scored participants.
    """
    if match.status is not MatchStatus.COMPLETED or match.result is None:
        raise ValueError(
            f"match {match.match_id!r} is not completed; cannot derive a rating outcome"
        )
    scores = match.result.scores
    entries = [
        OutcomeEntry(
            identity=RatingIdentity.from_participant(participant),
            score=round(scores[participant.participant_id]),
        )
        for participant in match.participants
        if participant.participant_id in scores
    ]
    return MatchOutcome(match_id=match.match_id, entries=tuple(entries))


class RatingSystem(ABC):
    """A pure function from ``(current ratings, match outcome)`` to rating deltas.

    Implementations MUST be deterministic: the same ``current_ratings``
    mapping and the same ``outcome`` (regardless of ``outcome.entries``
    order or ``current_ratings`` iteration order) must always produce a
    ``dict`` that compares equal by value.
    """

    @abstractmethod
    def compute_deltas(
        self, current_ratings: Mapping[RatingIdentity, int], outcome: MatchOutcome
    ) -> dict[RatingIdentity, int]:
        """Return ``identity -> integer rating delta`` for every identity in ``outcome``.

        ``current_ratings`` need not contain every identity in ``outcome`` —
        an identity absent from the mapping is treated as being at
        :data:`INITIAL_RATING`.
        """


# --- integer-only Elo -------------------------------------------------------

# A fixed-point sampling of the logistic Elo expected-score curve
# E(d) = 1 / (1 + 10 ** (-d / 400)), taken every 25 rating points from
# d = -400 to d = +400 and expressed in "millipoints" (0..1000, where 1000
# is a full point). Computed once, offline, with floating point, and then
# frozen here as integer literals — the table itself is the only place
# anything transcendental happens; every runtime lookup below is table
# indexing plus linear interpolation, i.e. integer +, -, *, // only.
_ANCHOR_STEP = 25
_ANCHOR_MAX = 400
_EXPECTED_SCORE_TABLE: tuple[int, ...] = (
    91,
    104,
    118,
    133,
    151,
    170,
    192,
    215,
    240,
    267,
    297,
    327,
    360,
    394,
    429,
    464,
    500,
    536,
    571,
    606,
    640,
    673,
    703,
    733,
    760,
    785,
    808,
    830,
    849,
    867,
    882,
    896,
    909,
)


def _expected_score_millipoints(rating_diff: int) -> int:
    """Expected score (0..1000) for a player ``rating_diff`` points above their opponent.

    Clamps ``rating_diff`` to ``[-400, 400]`` (the conventional Elo cap,
    beyond which the curve is treated as flat) and linearly interpolates
    between the two nearest entries of :data:`_EXPECTED_SCORE_TABLE` using
    only integer multiplication and floor division.
    """
    clamped = max(-_ANCHOR_MAX, min(_ANCHOR_MAX, rating_diff))
    shifted = clamped + _ANCHOR_MAX
    low_index, remainder = divmod(shifted, _ANCHOR_STEP)
    low = _EXPECTED_SCORE_TABLE[low_index]
    if remainder == 0:
        return low
    high = _EXPECTED_SCORE_TABLE[low_index + 1]
    return low + (high - low) * remainder // _ANCHOR_STEP


def _round_half_away_from_zero(numerator: int, denominator: int) -> int:
    """Round ``numerator / denominator`` to the nearest integer, ties away from zero.

    ``denominator`` must be positive. Using this (rather than plain ``//``,
    which floors toward negative infinity) keeps two-participant deltas
    exactly zero-sum: since the two participants' numerators are always
    exact negatives of each other (see :meth:`IntegerEloRatingSystem.compute_deltas`),
    and this function satisfies ``round(-x) == -round(x)`` exactly, the two
    resulting deltas are also exact negatives of each other.
    """
    if numerator >= 0:
        return (numerator + denominator // 2) // denominator
    return -((-numerator + denominator // 2) // denominator)


class IntegerEloRatingSystem(RatingSystem):
    """Elo rating, extended to N-participant matches, integer arithmetic only.

    For a two-participant match this is textbook Elo: the winner's delta is
    ``round(k_factor * (1 - expected))``, the loser's is
    ``round(k_factor * (0 - expected))``, a draw uses ``0.5`` (500
    millipoints) for both. For a match with more than two participants,
    every unordered pair is scored as its own one-on-one comparison (hard
    score determines win/draw/loss for that pair) and each identity's
    per-pair numerators are summed and then divided once by
    ``1000 * (n - 1)`` — so a 1-on-1 match and every pairing inside a
    multiway match use exactly the same per-pair formula, just normalized
    by the number of opponents faced.
    """

    def __init__(self, k_factor: int = 32) -> None:
        if k_factor <= 0:
            raise ValueError("k_factor must be positive")
        self.k_factor = k_factor

    def compute_deltas(
        self, current_ratings: Mapping[RatingIdentity, int], outcome: MatchOutcome
    ) -> dict[RatingIdentity, int]:
        entries = sorted(outcome.entries, key=lambda entry: entry.identity.sort_key())
        participant_count = len(entries)
        if participant_count < 2:
            raise ValueError("a match outcome needs at least two participants to be rated")

        numerators: dict[RatingIdentity, int] = {entry.identity: 0 for entry in entries}
        for i in range(participant_count):
            for j in range(i + 1, participant_count):
                left, right = entries[i], entries[j]
                left_rating = current_ratings.get(left.identity, INITIAL_RATING)
                right_rating = current_ratings.get(right.identity, INITIAL_RATING)

                if left.score > right.score:
                    left_actual, right_actual = 1000, 0
                elif left.score < right.score:
                    left_actual, right_actual = 0, 1000
                else:
                    left_actual, right_actual = 500, 500

                left_expected = _expected_score_millipoints(left_rating - right_rating)
                right_expected = _expected_score_millipoints(right_rating - left_rating)

                numerators[left.identity] += self.k_factor * (left_actual - left_expected)
                numerators[right.identity] += self.k_factor * (right_actual - right_expected)

        opponents_faced = participant_count - 1
        denominator = 1000 * opponents_faced
        return {
            identity: _round_half_away_from_zero(numerator, denominator)
            for identity, numerator in numerators.items()
        }
