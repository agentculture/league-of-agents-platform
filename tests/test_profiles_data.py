"""Tests for ``league_site.profiles.data``: slugs, profile assembly, recent matches.

Covers the acceptance criterion "every ranked identity in a seeded ledger
gets a working profile" at the data layer (the WSGI-level version of this
criterion lives in ``test_profiles_wsgi.py``), plus the slug round-trip
contract documented in that module's docstring.
"""

from __future__ import annotations

import re

from league_site.matches import InMemoryMatchStore, ParticipantKind
from league_site.profiles.data import (
    Profile,
    RecentMatch,
    build_profile,
    identity_slug,
    slug_index,
    slugify,
)
from league_site.ratings import (
    InMemoryRatingLedgerStore,
    IntegerEloRatingSystem,
    RatingIdentity,
    outcome_from_match,
)
from tests._profiles_support import (
    ADA,
    ADA_IDENTITY,
    RIVAL,
    RIVAL_IDENTITY,
    SONNET_IDENTITY,
    build_scenario,
    make_completed_match,
)

HOSTILE_NAME = """<script>&"'"""

# --- slugify -----------------------------------------------------------------


def test_slugify_lowercases_and_replaces_unsafe_characters() -> None:
    assert slugify("Ada Lovelace") == "ada-lovelace"


def test_slugify_collapses_runs_and_strips_leading_trailing_dashes() -> None:
    assert slugify("  --Ada!!  Lovelace--  ") == "ada-lovelace"


def test_slugify_never_raises_and_never_returns_empty_string() -> None:
    assert slugify(HOSTILE_NAME) != ""
    assert slugify("") == "x"
    assert slugify("!!!???***") == "x"


def test_slugify_output_is_url_safe_alphabet_only() -> None:
    result = slugify(HOSTILE_NAME + " 日本語 emoji \U0001f600")
    assert result != ""
    assert all(char.isalnum() and char.isascii() or char == "-" for char in result)


# --- identity_slug / slug_index (round-trip contract) -------------------------


def test_identity_slug_is_pure_and_deterministic() -> None:
    assert identity_slug(ADA_IDENTITY) == identity_slug(ADA_IDENTITY)


def test_identity_slug_differs_for_distinct_identities() -> None:
    slugs = {
        identity_slug(ADA_IDENTITY),
        identity_slug(SONNET_IDENTITY),
        identity_slug(RIVAL_IDENTITY),
    }
    assert len(slugs) == 3


def test_identity_slug_distinguishes_same_display_name_different_model() -> None:
    """Two agents that could plausibly share a display name must not share a slug."""
    sonnet_opus = RatingIdentity(
        kind=ParticipantKind.AGENT,
        display_name="Sonnet",
        model="claude-opus-4",
        provider="anthropic",
    )
    assert identity_slug(SONNET_IDENTITY) != identity_slug(sonnet_opus)


def test_identity_slug_is_url_safe_even_for_a_hostile_display_name() -> None:
    hostile = RatingIdentity(kind=ParticipantKind.HUMAN, display_name=HOSTILE_NAME)
    slug = identity_slug(hostile)
    assert re.fullmatch(r"[a-z0-9-]+", slug)


def test_identity_slug_carries_kind_and_readable_name_prefix() -> None:
    assert identity_slug(ADA_IDENTITY).startswith("human-ada-")
    assert identity_slug(SONNET_IDENTITY).startswith("agent-sonnet-claude-sonnet-5-anthropic-")


def test_slug_index_round_trips_every_ledgered_identity() -> None:
    scenario = build_scenario()
    index = slug_index(scenario.ledger_store)

    assert set(index) == {
        identity_slug(ADA_IDENTITY),
        identity_slug(SONNET_IDENTITY),
        identity_slug(RIVAL_IDENTITY),
    }
    for identity in (ADA_IDENTITY, SONNET_IDENTITY, RIVAL_IDENTITY):
        assert index[identity_slug(identity)] == identity


def test_slug_index_is_empty_for_a_fresh_ledger() -> None:
    assert slug_index(InMemoryRatingLedgerStore()) == {}


def test_slug_index_reflects_a_match_recorded_immediately_after() -> None:
    """No cache: an identity recorded a moment ago resolves on the very next call."""
    store = InMemoryRatingLedgerStore()
    assert identity_slug(RIVAL_IDENTITY) not in slug_index(store)

    match = make_completed_match("m-fresh", (ADA, RIVAL), {"p-ada": 1.0, "p-rival": 9.0})
    store.record_match(outcome_from_match(match), IntegerEloRatingSystem())

    after = slug_index(store)
    assert identity_slug(RIVAL_IDENTITY) in after
    assert after[identity_slug(RIVAL_IDENTITY)] == RIVAL_IDENTITY


# --- build_profile -------------------------------------------------------------


def test_build_profile_for_an_unrated_identity_returns_initial_standing() -> None:
    store = InMemoryRatingLedgerStore()
    matches = InMemoryMatchStore()
    profile = build_profile(ADA_IDENTITY, store, matches)

    assert profile.rating == 1500
    assert profile.match_count == 0
    assert profile.history == ()
    assert profile.recent_matches == ()


def test_build_profile_exposes_identity_fields_for_a_human() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    assert profile.display_name == "Ada"
    assert profile.kind == "human"
    assert profile.is_agent is False
    assert profile.model is None
    assert profile.provider is None
    assert profile.slug == identity_slug(ADA_IDENTITY)


def test_build_profile_exposes_model_and_provider_for_an_agent() -> None:
    scenario = build_scenario()
    profile = build_profile(SONNET_IDENTITY, scenario.ledger_store, scenario.match_store)

    assert profile.is_agent is True
    assert profile.model == "claude-sonnet-5"
    assert profile.provider == "anthropic"


def test_build_profile_rating_and_match_count_match_the_ledger() -> None:
    scenario = build_scenario()
    standing = scenario.ledger_store.get(ADA_IDENTITY)
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    assert profile.rating == standing.rating
    assert profile.match_count == standing.match_count == 2
    assert profile.history == standing.history


def test_build_profile_recent_matches_are_most_recent_first() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    assert [recent.match_id for recent in profile.recent_matches] == ["m3", "m1"]


def test_build_profile_recent_match_outcomes_are_win_loss_and_draw() -> None:
    scenario = build_scenario()

    ada = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)
    sonnet = build_profile(SONNET_IDENTITY, scenario.ledger_store, scenario.match_store)
    rival = build_profile(RIVAL_IDENTITY, scenario.ledger_store, scenario.match_store)

    ada_by_match = {recent.match_id: recent.outcome for recent in ada.recent_matches}
    sonnet_by_match = {recent.match_id: recent.outcome for recent in sonnet.recent_matches}
    rival_by_match = {recent.match_id: recent.outcome for recent in rival.recent_matches}

    assert ada_by_match == {"m1": "win", "m3": "draw"}
    assert sonnet_by_match == {"m1": "loss", "m2": "win"}
    assert rival_by_match == {"m2": "loss", "m3": "draw"}


def test_build_profile_recent_match_carries_opponent_display_names() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)
    by_match = {recent.match_id: recent.opponents for recent in profile.recent_matches}

    assert by_match["m1"] == ("Sonnet",)
    assert by_match["m3"] == ("Rival",)


def test_build_profile_respects_recent_limit() -> None:
    scenario = build_scenario()
    profile = build_profile(
        ADA_IDENTITY, scenario.ledger_store, scenario.match_store, recent_limit=1
    )
    assert len(profile.recent_matches) == 1
    assert profile.recent_matches[0].match_id == "m3"


def test_build_profile_skips_a_ledgered_match_missing_from_the_match_store() -> None:
    """The ledger and match store are independent -- a pruned/archived match
    that the ledger still remembers must not break profile assembly."""
    scenario = build_scenario()
    sparse_matches = InMemoryMatchStore()
    sparse_matches.save(scenario.match_m1)  # m3 deliberately not saved here

    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, sparse_matches)
    assert [recent.match_id for recent in profile.recent_matches] == ["m1"]
    # rating/history are unaffected -- they come from the ledger, not the match store
    assert profile.match_count == 2


def test_build_profile_marks_a_match_unscored_when_this_identity_has_no_recorded_score() -> None:
    """Defensive branch: if the match store's copy of a ledgered match no
    longer carries this identity's score (e.g. edited/re-saved out of band),
    the summary degrades to "unscored" rather than raising."""
    scenario = build_scenario()
    rescored = scenario.match_store.load("m1")
    rescored.result.scores = {"p-sonnet": 3.0}  # p-ada's own score is now missing
    scenario.match_store.save(rescored)

    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)
    by_match = {recent.match_id: recent.outcome for recent in profile.recent_matches}
    assert by_match["m1"] == "unscored"


def test_profile_and_recent_match_are_frozen_dataclasses() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)
    assert isinstance(profile, Profile)
    assert profile.recent_matches and isinstance(profile.recent_matches[0], RecentMatch)
