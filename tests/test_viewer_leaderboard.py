"""Tests for :mod:`league_site.viewer.leaderboard` — the render-ready view + HTML

fragment builder behind the public ``/leaderboard`` page (see
:mod:`league_site.viewer.wsgi`).

Covers: row ordering mirrors :func:`league_site.ratings.leaderboard.leaderboard`
exactly (integer Elo, no floats), each row links to the identity's
``/profiles/<slug>`` page, agent rows carry model/provider chips while human
rows don't, hostile display names/models are escaped, and an empty ledger
renders a welcoming zero-state rather than an error.
"""

from __future__ import annotations

from league_site.matches import ParticipantKind
from league_site.profiles.data import identity_slug
from league_site.ratings import (
    InMemoryRatingLedgerStore,
    IntegerEloRatingSystem,
    MatchOutcome,
    OutcomeEntry,
    RatingIdentity,
    leaderboard,
)
from league_site.viewer.leaderboard import build_leaderboard_rows, render_leaderboard_body

HUMAN = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Ada")
AGENT = RatingIdentity(
    kind=ParticipantKind.AGENT,
    display_name="Sonnet",
    model="claude-sonnet-5",
    provider="anthropic",
)
HOSTILE = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="""<script>alert('x')</script>""")
SYSTEM = IntegerEloRatingSystem(k_factor=32)


def _outcome(match_id: str, *pairs: tuple[RatingIdentity, int]) -> MatchOutcome:
    return MatchOutcome(
        match_id=match_id,
        entries=tuple(OutcomeEntry(identity=identity, score=score) for identity, score in pairs),
    )


# --- build_leaderboard_rows ---------------------------------------------------


def test_build_leaderboard_rows_is_empty_for_none_ledger() -> None:
    assert build_leaderboard_rows(None) == ()


def test_build_leaderboard_rows_is_empty_for_a_fresh_store() -> None:
    assert build_leaderboard_rows(InMemoryRatingLedgerStore()) == ()


def test_build_leaderboard_rows_orders_exactly_like_ratings_leaderboard() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)

    rows = build_leaderboard_rows(store)
    reference = leaderboard(store)

    assert len(rows) == len(reference) == 2
    for view, row in zip(rows, reference):
        assert view.rank == row.rank
        assert view.rating == row.rating
        assert view.match_count == row.match_count
        assert isinstance(view.rating, int)
        assert view.profile_href == f"/profiles/{identity_slug(row.identity)}"


def test_build_leaderboard_rows_ratings_are_integers_never_floats() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)

    rows = build_leaderboard_rows(store)
    assert all(isinstance(row.rating, int) for row in rows)


def test_build_leaderboard_rows_carries_model_provider_for_agents_and_none_for_humans() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)

    rows = build_leaderboard_rows(store)
    agent_row = next(row for row in rows if row.kind == "agent")
    human_row = next(row for row in rows if row.kind == "human")

    assert agent_row.model_html == "claude-sonnet-5"
    assert agent_row.provider_html == "anthropic"
    assert human_row.model_html is None
    assert human_row.provider_html is None


# --- render_leaderboard_body ---------------------------------------------------


def test_render_leaderboard_body_zero_state_is_welcoming_not_an_error() -> None:
    html_text = render_leaderboard_body(build_leaderboard_rows(None))
    assert "no rated matches yet" in html_text.lower()
    assert "be the first" in html_text.lower()
    assert "error" not in html_text.lower()
    assert "404" not in html_text
    assert 'href="/' in html_text  # links out to the docs


def test_render_leaderboard_body_zero_state_for_empty_store_too() -> None:
    html_text = render_leaderboard_body(build_leaderboard_rows(InMemoryRatingLedgerStore()))
    assert "no rated matches yet" in html_text.lower()


def test_render_leaderboard_body_lists_rank_identity_rating_matches() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)
    rows = build_leaderboard_rows(store)

    html_text = render_leaderboard_body(rows)
    for row in rows:
        assert f">{row.rank}<" in html_text
        assert row.display_name_html in html_text
        assert f">{row.rating}<" in html_text
        assert f">{row.match_count}<" in html_text


def test_render_leaderboard_body_links_each_row_to_its_profile_page() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)
    rows = build_leaderboard_rows(store)

    html_text = render_leaderboard_body(rows)
    for row in rows:
        assert f'href="{row.profile_href}"' in html_text


def test_render_leaderboard_body_shows_model_provider_chips_for_agents_only() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HUMAN, 10), (AGENT, 3)), SYSTEM)
    rows = build_leaderboard_rows(store)

    html_text = render_leaderboard_body(rows)
    assert "claude-sonnet-5" in html_text
    assert "anthropic" in html_text


def test_render_leaderboard_body_escapes_hostile_display_names() -> None:
    store = InMemoryRatingLedgerStore()
    store.record_match(_outcome("m1", (HOSTILE, 10), (HUMAN, 3)), SYSTEM)
    rows = build_leaderboard_rows(store)

    html_text = render_leaderboard_body(rows)
    assert "<script>alert" not in html_text
    assert "&lt;script&gt;alert" in html_text
