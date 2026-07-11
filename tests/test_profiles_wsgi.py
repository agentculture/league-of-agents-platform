"""WSGI-level tests for ``league_site.profiles.wsgi.profile_app``.

Covers the acceptance criteria "every ranked identity in a seeded ledger gets
a working profile URL" (page shows rating, history, model+provider for
agents), "card.svg and badge.svg ... return valid, well-formed XML/SVG ...
with correct content types; badge contains the current rank; card contains
name + rating", the hostile-display-name XML-escaping regression across
every surface, "JSON endpoint returns the full profile with deterministic
field order", and "same ledger state -> byte-identical SVGs" at the HTTP
layer.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from league_site.matches import (
    AgentIdentity,
    InMemoryMatchStore,
    Participant,
    ParticipantKind,
)
from league_site.profiles.data import identity_slug
from league_site.profiles.wsgi import WSGIApp, _rank_of, profile_app
from league_site.ratings import (
    InMemoryRatingLedgerStore,
    IntegerEloRatingSystem,
    RatingIdentity,
    leaderboard,
    outcome_from_match,
)
from league_site.web import scripts
from league_site.web.shell import asset_url
from tests._profiles_support import (
    ADA_IDENTITY,
    RIVAL_IDENTITY,
    SONNET_IDENTITY,
    build_scenario,
    make_completed_match,
)

HOSTILE_NAME = """<script>&"'"""


def _get(app: WSGIApp, path: str, *, method: str = "GET") -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI test client: request *path*, return (status, headers, body)."""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": method, "PATH_INFO": path}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


def _app() -> tuple[WSGIApp, InMemoryRatingLedgerStore, InMemoryMatchStore]:
    scenario = build_scenario()
    return (
        profile_app(scenario.ledger_store, scenario.match_store),
        (scenario.ledger_store),
        scenario.match_store,
    )


# --- routing / not-found / method handling --------------------------------------


def test_unknown_path_outside_profiles_prefix_404s() -> None:
    app, _, _ = _app()
    status, _, _ = _get(app, "/leaderboard")
    assert status == "404 Not Found"


def test_bare_profiles_prefix_404s() -> None:
    app, _, _ = _app()
    status, _, _ = _get(app, "/profiles/")
    assert status == "404 Not Found"


def test_unknown_slug_404s_on_every_route() -> None:
    app, _, _ = _app()
    for suffix in ("", "/card.svg", "/badge.svg", ".json"):
        status, _, _ = _get(app, f"/profiles/does-not-exist{suffix}")
        assert status == "404 Not Found", suffix


def test_unknown_suffix_under_a_known_slug_404s() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    status, _, _ = _get(app, f"/profiles/{slug}/avatar.png")
    assert status == "404 Not Found"


def test_non_get_method_is_405() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    status, _, _ = _get(app, f"/profiles/{slug}", method="POST")
    assert status == "405 Method Not Allowed"


def test_empty_slug_segment_before_a_known_suffix_404s() -> None:
    """``/profiles//card.svg`` -- an empty slug segment, not a valid route."""
    app, _, _ = _app()
    status, _, _ = _get(app, "/profiles//card.svg")
    assert status == "404 Not Found"


def test_rank_of_returns_none_for_an_identity_absent_from_the_leaderboard() -> None:
    """Defensive: ``_rank_of`` never raises for an identity the given ledger
    doesn't know about -- it just reports "no rank"."""
    empty_ledger = InMemoryRatingLedgerStore()
    assert _rank_of(empty_ledger, ADA_IDENTITY) is None


# --- acceptance: every ranked identity has a working profile URL ---------------


def test_every_ranked_identity_has_a_working_profile_page() -> None:
    app, ledger_store, _ = _app()
    for identity in (ADA_IDENTITY, SONNET_IDENTITY, RIVAL_IDENTITY):
        slug = identity_slug(identity)
        status, headers, body = _get(app, f"/profiles/{slug}")
        assert status == "200 OK", slug
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        text = body.decode("utf-8")
        standing = ledger_store.get(identity)
        assert str(standing.rating) in text
        assert identity.display_name in text
        for entry in standing.history:
            assert entry.match_id in text


def test_agent_profile_page_shows_model_and_provider() -> None:
    app, _, _ = _app()
    slug = identity_slug(SONNET_IDENTITY)
    _, _, body = _get(app, f"/profiles/{slug}")
    text = body.decode("utf-8")
    assert "claude-sonnet-5" in text
    assert "anthropic" in text


def test_human_profile_page_does_not_show_a_model_or_provider_line() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    _, _, body = _get(app, f"/profiles/{slug}")
    text = body.decode("utf-8")
    assert "claude-sonnet-5" not in text
    assert "anthropic" not in text


def test_profile_page_shows_rank() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    _, _, body = _get(app, f"/profiles/{slug}")
    text = body.decode("utf-8")
    assert "Rank #" in text


def test_profile_page_is_a_self_contained_html_document() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    _, headers, body = _get(app, f"/profiles/{slug}")
    text = body.decode("utf-8")
    assert text.startswith("<!doctype html>")
    assert "<style>" in text and "</style>" in text
    assert "--accent" in text  # theme.STYLESHEET's custom properties, inlined
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    # Profiles render ahead of with_auth (no session plumbing) so they carry
    # the shared header's anonymous state: a GitHub sign-in entry, never a
    # Google login link (t8).
    assert 'href="/auth/login/github"' in text
    assert "/auth/login/google" not in text


# --- card.svg / badge.svg ------------------------------------------------------


def test_card_svg_is_well_formed_and_correctly_typed() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    status, headers, body = _get(app, f"/profiles/{slug}/card.svg")
    assert status == "200 OK"
    assert headers["Content-Type"] == "image/svg+xml; charset=utf-8"
    ET.fromstring(body)  # raises if malformed


def test_card_svg_contains_name_and_rating() -> None:
    app, ledger_store, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    _, _, body = _get(app, f"/profiles/{slug}/card.svg")
    text = body.decode("utf-8")
    assert "Ada" in text
    assert str(ledger_store.get(ADA_IDENTITY).rating) in text


def test_badge_svg_is_well_formed_correctly_typed_and_cacheable() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    status, headers, body = _get(app, f"/profiles/{slug}/badge.svg")
    assert status == "200 OK"
    assert headers["Content-Type"] == "image/svg+xml; charset=utf-8"
    assert "Cache-Control" in headers
    ET.fromstring(body)


def test_badge_svg_contains_the_current_rank() -> None:
    app, ledger_store, _ = _app()
    rows_by_identity = {row.identity: row.rank for row in leaderboard(ledger_store)}
    slug = identity_slug(ADA_IDENTITY)
    _, _, body = _get(app, f"/profiles/{slug}/badge.svg")
    text = body.decode("utf-8")
    assert f"#{rows_by_identity[ADA_IDENTITY]}" in text


def test_card_and_badge_svg_are_byte_identical_across_requests_for_the_same_state() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    _, _, first_card = _get(app, f"/profiles/{slug}/card.svg")
    _, _, second_card = _get(app, f"/profiles/{slug}/card.svg")
    _, _, first_badge = _get(app, f"/profiles/{slug}/badge.svg")
    _, _, second_badge = _get(app, f"/profiles/{slug}/badge.svg")
    assert first_card == second_card
    assert first_badge == second_badge


def test_card_and_badge_svg_are_byte_identical_across_two_apps_built_from_the_same_state() -> None:
    """ "Same ledger state -> byte-identical SVGs", exercised across two
    independently built apps rather than two requests to one app."""
    scenario_a = build_scenario()
    scenario_b = build_scenario()
    app_a = profile_app(scenario_a.ledger_store, scenario_a.match_store)
    app_b = profile_app(scenario_b.ledger_store, scenario_b.match_store)
    slug = identity_slug(ADA_IDENTITY)

    _, _, card_a = _get(app_a, f"/profiles/{slug}/card.svg")
    _, _, card_b = _get(app_b, f"/profiles/{slug}/card.svg")
    _, _, badge_a = _get(app_a, f"/profiles/{slug}/badge.svg")
    _, _, badge_b = _get(app_b, f"/profiles/{slug}/badge.svg")
    assert card_a == card_b
    assert badge_a == badge_b


# --- .json -----------------------------------------------------------------------


def test_json_endpoint_returns_the_full_profile() -> None:
    app, ledger_store, _ = _app()
    slug = identity_slug(SONNET_IDENTITY)
    status, headers, body = _get(app, f"/profiles/{slug}.json")
    assert status == "200 OK"
    assert headers["Content-Type"] == "application/json; charset=utf-8"

    payload = json.loads(body)
    standing = ledger_store.get(SONNET_IDENTITY)
    assert payload["slug"] == slug
    assert payload["display_name"] == "Sonnet"
    assert payload["kind"] == "agent"
    assert payload["model"] == "claude-sonnet-5"
    assert payload["provider"] == "anthropic"
    assert payload["rating"] == standing.rating
    assert payload["match_count"] == standing.match_count
    assert len(payload["rating_history"]) == len(standing.history)
    assert len(payload["recent_matches"]) == standing.match_count


def test_json_endpoint_field_order_is_deterministic() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    _, _, first = _get(app, f"/profiles/{slug}.json")
    _, _, second = _get(app, f"/profiles/{slug}.json")
    assert first == second

    keys = json.loads(first, object_pairs_hook=lambda pairs: [key for key, _ in pairs])
    assert keys == [
        "slug",
        "display_name",
        "kind",
        "model",
        "provider",
        "rating",
        "rank",
        "match_count",
        "rating_history",
        "recent_matches",
    ]


def test_json_endpoint_human_has_null_model_and_provider() -> None:
    app, _, _ = _app()
    slug = identity_slug(ADA_IDENTITY)
    _, _, body = _get(app, f"/profiles/{slug}.json")
    payload = json.loads(body)
    assert payload["model"] is None
    assert payload["provider"] is None


# --- XML-escaping regression: a hostile display name, everywhere it appears ----


def _hostile_scenario() -> tuple[InMemoryRatingLedgerStore, InMemoryMatchStore, RatingIdentity]:
    hostile = Participant(
        display_name=HOSTILE_NAME,
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model=HOSTILE_NAME, provider="anthropic"),
        participant_id="p-hostile",
    )
    opponent = Participant(display_name="Ada", kind=ParticipantKind.HUMAN, participant_id="p-ada")
    match = make_completed_match("m-hostile", (hostile, opponent), {"p-hostile": 9.0, "p-ada": 1.0})

    ledger_store = InMemoryRatingLedgerStore()
    match_store = InMemoryMatchStore()
    match_store.save(match)
    ledger_store.record_match(outcome_from_match(match), IntegerEloRatingSystem())

    identity = RatingIdentity.from_participant(hostile)
    return ledger_store, match_store, identity


def test_hostile_display_name_is_escaped_on_the_html_page() -> None:
    ledger_store, match_store, identity = _hostile_scenario()
    app = profile_app(ledger_store, match_store)
    slug = identity_slug(identity)

    status, _, body = _get(app, f"/profiles/{slug}")
    assert status == "200 OK"
    text = body.decode("utf-8")
    # The page legitimately carries the dazzle layer's two known scripts
    # (the inline pre-paint snippet and deferred /site.js) — the escape
    # proof is that the hostile payload itself appears ONLY entity-escaped,
    # never as markup: strip the two known tags and no <script> remains.
    sanitized = text.replace(f"<script>{scripts.PRE_PAINT_JS}</script>", "").replace(
        f'<script defer src="{asset_url("site.js")}"></script>', ""
    )
    assert "<script" not in sanitized
    assert "&lt;script&gt;" in text


def test_hostile_display_name_is_escaped_on_the_share_card() -> None:
    ledger_store, match_store, identity = _hostile_scenario()
    app = profile_app(ledger_store, match_store)
    slug = identity_slug(identity)

    _, _, body = _get(app, f"/profiles/{slug}/card.svg")
    ET.fromstring(body)  # would raise ParseError if the name broke the XML
    text = body.decode("utf-8")
    assert "<script>" not in text
    assert "&lt;script&gt;" in text


def test_hostile_display_name_does_not_break_the_badge() -> None:
    """The badge doesn't render the display name at all (see svg.py) -- but a
    hostile name anywhere in the profile must never break badge rendering."""
    ledger_store, match_store, identity = _hostile_scenario()
    app = profile_app(ledger_store, match_store)
    slug = identity_slug(identity)

    status, _, body = _get(app, f"/profiles/{slug}/badge.svg")
    assert status == "200 OK"
    ET.fromstring(body)


def test_hostile_display_name_round_trips_unescaped_through_json() -> None:
    """JSON needs JSON-string escaping, not XML/HTML escaping -- the raw name
    round-trips exactly through ``json.loads``."""
    ledger_store, match_store, identity = _hostile_scenario()
    app = profile_app(ledger_store, match_store)
    slug = identity_slug(identity)

    _, _, body = _get(app, f"/profiles/{slug}.json")
    payload = json.loads(body)
    assert payload["display_name"] == HOSTILE_NAME
    assert payload["model"] == HOSTILE_NAME
