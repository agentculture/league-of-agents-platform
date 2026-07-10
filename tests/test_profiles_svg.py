"""Tests for ``league_site.profiles.svg``: sparkline, og-image card, rank badge.

Covers the acceptance criteria "card.svg and badge.svg ... return valid,
well-formed XML/SVG" (parsed here with :mod:`xml.etree.ElementTree`, exactly
as the acceptance criterion specifies), "card contains name + rating" /
"badge contains the current rank", the XML-escaping regression for a hostile
display name, and "same ledger state -> byte-identical SVGs" (determinism).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from league_site.matches import ParticipantKind
from league_site.profiles.data import Profile, build_profile
from league_site.profiles.svg import rank_badge, rating_sparkline, share_card
from league_site.ratings import RatingIdentity
from league_site.ratings.ledger import RatingEntry
from tests._profiles_support import ADA_IDENTITY, RIVAL_IDENTITY, SONNET_IDENTITY, build_scenario

_SVG_NS = "{http://www.w3.org/2000/svg}"

HOSTILE_NAME = """<script>&"'"""


def _local_tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


# --- rating_sparkline ----------------------------------------------------------


def test_rating_sparkline_of_empty_history_is_well_formed_and_flat() -> None:
    svg = rating_sparkline(())
    root = ET.fromstring(svg)  # raises ParseError if malformed
    assert _local_tag(root) == "g"
    line = root.find("line")
    assert line is not None


def test_rating_sparkline_polyline_has_one_point_per_history_entry() -> None:
    history = (
        RatingEntry(match_id="m1", delta=10, resulting_rating=1510),
        RatingEntry(match_id="m2", delta=-4, resulting_rating=1506),
        RatingEntry(match_id="m3", delta=7, resulting_rating=1513),
    )
    svg = rating_sparkline(history)
    root = ET.fromstring(svg)
    polyline = root.find("polyline")
    assert polyline is not None
    points = polyline.get("points", "").split()
    assert len(points) == len(history)


def test_rating_sparkline_single_point_history_is_still_well_formed() -> None:
    history = (RatingEntry(match_id="m1", delta=10, resulting_rating=1510),)
    svg = rating_sparkline(history)
    root = ET.fromstring(svg)
    polyline = root.find("polyline")
    assert polyline is not None
    assert len(polyline.get("points", "").split()) == 1


def test_rating_sparkline_flat_history_does_not_divide_by_zero() -> None:
    """Every rating identical -> span would be 0 without the ``or 1`` guard."""
    history = tuple(RatingEntry(match_id=f"m{i}", delta=0, resulting_rating=1500) for i in range(3))
    svg = rating_sparkline(history)
    ET.fromstring(svg)  # must not raise (ZeroDivisionError or ParseError)


def test_rating_sparkline_is_deterministic() -> None:
    history = (
        RatingEntry(match_id="m1", delta=10, resulting_rating=1510),
        RatingEntry(match_id="m2", delta=-4, resulting_rating=1506),
    )
    assert rating_sparkline(history) == rating_sparkline(history)


# --- share_card ------------------------------------------------------------------


def test_share_card_is_well_formed_svg_with_documented_dimensions() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    svg = share_card(profile, rank=1)
    root = ET.fromstring(svg)
    assert _local_tag(root) == "svg"
    assert root.get("width") == "1200"
    assert root.get("height") == "630"


def test_share_card_contains_display_name_and_rating() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    svg = share_card(profile, rank=1)
    assert profile.display_name in svg
    assert str(profile.rating) in svg


def test_share_card_contains_model_and_provider_for_an_agent() -> None:
    scenario = build_scenario()
    profile = build_profile(SONNET_IDENTITY, scenario.ledger_store, scenario.match_store)

    svg = share_card(profile, rank=2)
    assert profile.model in svg
    assert profile.provider in svg


def test_share_card_omits_model_provider_line_for_a_human() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    svg = share_card(profile, rank=1)
    assert "anthropic" not in svg
    assert "openai" not in svg


def test_share_card_renders_without_a_rank() -> None:
    scenario = build_scenario()
    profile = build_profile(RIVAL_IDENTITY, scenario.ledger_store, scenario.match_store)

    svg = share_card(profile, rank=None)
    ET.fromstring(svg)
    assert "RANK" not in svg


def test_share_card_escapes_a_hostile_display_name() -> None:
    hostile_identity = RatingIdentity(kind=ParticipantKind.HUMAN, display_name=HOSTILE_NAME)
    hostile_profile = Profile(
        identity=hostile_identity,
        slug="human-x-00000000",
        rating=1500,
        match_count=0,
        history=(),
        recent_matches=(),
    )

    svg = share_card(hostile_profile, rank=1)
    ET.fromstring(svg)  # would raise ParseError if the name broke the XML
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_share_card_is_deterministic_for_the_same_profile_and_rank() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)
    assert share_card(profile, rank=1) == share_card(profile, rank=1)


def test_share_card_differs_when_rank_differs() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)
    assert share_card(profile, rank=1) != share_card(profile, rank=2)


# --- rank_badge -------------------------------------------------------------------


def test_rank_badge_is_well_formed_svg() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    svg = rank_badge(profile, 3)
    root = ET.fromstring(svg)
    assert _local_tag(root) == "svg"


def test_rank_badge_contains_the_current_rank_and_rating() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    svg = rank_badge(profile, 3)
    assert "#3" in svg
    assert str(profile.rating) in svg


def test_rank_badge_width_grows_with_rank_and_rating_text_length() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    narrow = ET.fromstring(rank_badge(profile, 3))
    wide = ET.fromstring(rank_badge(profile, 300000))
    assert int(wide.get("width")) > int(narrow.get("width"))


def test_rank_badge_is_deterministic_for_the_same_profile_and_rank() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)
    assert rank_badge(profile, 1) == rank_badge(profile, 1)


def test_rank_badge_escapes_a_hostile_label() -> None:
    scenario = build_scenario()
    profile = build_profile(ADA_IDENTITY, scenario.ledger_store, scenario.match_store)

    svg = rank_badge(profile, 1, label=HOSTILE_NAME)
    ET.fromstring(svg)  # would raise ParseError if the label broke the XML
    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
