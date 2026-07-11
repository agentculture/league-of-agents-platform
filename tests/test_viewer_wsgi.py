"""WSGI-level tests for :func:`league_site.viewer.wsgi.viewer_app`.

Covers the task's acceptance criteria directly against the standalone
viewer app (bound to a plain :class:`~league_site.matches.store.
InMemoryMatchStore`, no auth involved anywhere in this module — the viewer
is a public, zero-auth surface):

* a finished match renders the full transcript with no login and no
  meta-refresh;
* an in-progress match carries the live refresh + indicator and reflects a
  newly recorded turn on the very next ``GET``;
* markdown in a turn message renders while hostile HTML/script anywhere is
  escaped;
* page weight for a 20-turn match stays under the 60KB budget and the page
  carries lang/viewport/meta;
* ``GET /leaderboard`` (platform#11) renders the current standings, ranked
  by rating, or a welcoming zero-state when no rated matches exist yet —
  see :mod:`tests.test_viewer_leaderboard` for the render-level coverage of
  the ordering/escaping rules this module only exercises through the route.
"""

from __future__ import annotations

from typing import Any

from league_site.matches import AgentIdentity, InMemoryMatchStore, Participant, ParticipantKind
from league_site.ratings import (
    InMemoryRatingLedgerStore,
    IntegerEloRatingSystem,
    MatchOutcome,
    OutcomeEntry,
    RatingIdentity,
)
from league_site.viewer.wsgi import WSGIApp, viewer_app
from league_site.web.shell import asset_url
from tests._viewer_support import start_match

HOSTILE = """<script>alert('x')</script>"""
_PAGE_WEIGHT_BUDGET_BYTES = 60 * 1024

_RATING_HUMAN = RatingIdentity(kind=ParticipantKind.HUMAN, display_name="Ada")
_RATING_AGENT = RatingIdentity(
    kind=ParticipantKind.AGENT,
    display_name="Sonnet",
    model="claude-sonnet-5",
    provider="anthropic",
)
_RATING_SYSTEM = IntegerEloRatingSystem(k_factor=32)


def _get(app: WSGIApp, path: str, *, method: str = "GET") -> tuple[str, dict[str, str], bytes]:
    """Minimal WSGI test client: request *path*, return (status, headers, body)."""
    captured: dict[str, Any] = {}

    def start_response(status: str, headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(headers)

    environ = {"REQUEST_METHOD": method, "PATH_INFO": path}
    body = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], body


# --- routing / not-found / method handling ----------------------------------


def test_path_not_matching_the_watch_route_404s_as_html() -> None:
    app = viewer_app(InMemoryMatchStore())
    status, headers, body = _get(app, "/matches/m1")
    assert status == "404 Not Found"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert body.startswith(b"<!doctype html>")


def test_unknown_match_id_404s_as_html_not_json() -> None:
    app = viewer_app(InMemoryMatchStore())
    status, headers, body = _get(app, "/matches/does-not-exist/watch")
    assert status == "404 Not Found"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8")
    assert "{" not in text  # not a JSON error envelope
    assert "does-not-exist" in text


def test_non_get_method_is_405() -> None:
    match_store = InMemoryMatchStore()
    match, _ = start_match()
    match_store.save(match)
    app = viewer_app(match_store)
    status, _, _ = _get(app, f"/matches/{match.match_id}/watch", method="POST")
    assert status == "405 Method Not Allowed"


# --- live vs. finished --------------------------------------------------------


def test_in_progress_match_carries_refresh_meta_and_a_live_indicator() -> None:
    match_store = InMemoryMatchStore()
    match, _ = start_match()
    match_store.save(match)
    app = viewer_app(match_store)

    status, headers, body = _get(app, f"/matches/{match.match_id}/watch")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8")
    assert '<meta http-equiv="refresh" content="5">' in text
    assert "LIVE" in text


def test_finished_match_has_no_meta_refresh_and_shows_full_transcript_no_login() -> None:
    match_store = InMemoryMatchStore()
    match, engine = start_match()
    human, agent = match.participants
    match.take_turn(engine, human.participant_id, {"delta": 3, "message": "opening move"})
    match.take_turn(engine, agent.participant_id, {"delta": 9})
    match.complete(engine)
    match_store.save(match)
    app = viewer_app(match_store)

    # No auth header, no session cookie, nothing -- this is the "readable
    # without logging in" acceptance criterion.
    status, headers, body = _get(app, f"/matches/{match.match_id}/watch")
    assert status == "200 OK"
    text = body.decode("utf-8")
    assert '<meta http-equiv="refresh"' not in text
    assert "LIVE" not in text or "FINISHED" in text
    assert human.display_name in text
    assert agent.display_name in text
    assert "opening move" in text
    assert "turn 1" in text
    assert "turn 2" in text
    # scores are shown once completed
    assert "score 3" in text
    assert "score 9" in text


def test_two_request_live_page_reflects_a_newly_recorded_turn() -> None:
    match_store = InMemoryMatchStore()
    match, engine = start_match()
    human, _agent = match.participants
    match_store.save(match)
    app = viewer_app(match_store)

    first_status, _, first_body = _get(app, f"/matches/{match.match_id}/watch")
    assert first_status == "200 OK"
    first_text = first_body.decode("utf-8")
    assert "No turns yet." in first_text
    assert "marker-turn-content" not in first_text

    match.take_turn(engine, human.participant_id, {"delta": 1, "message": "marker-turn-content"})
    match_store.save(match)

    second_status, _, second_body = _get(app, f"/matches/{match.match_id}/watch")
    assert second_status == "200 OK"
    second_text = second_body.decode("utf-8")
    assert "No turns yet." not in second_text
    assert "marker-turn-content" in second_text
    # Still in progress -- the live refresh survives the new turn.
    assert '<meta http-equiv="refresh" content="5">' in second_text


# --- markdown rendering + XSS escaping ----------------------------------------


def test_markdown_in_a_turn_message_renders_as_real_tags() -> None:
    match_store = InMemoryMatchStore()
    match, engine = start_match()
    human, _agent = match.participants
    match.take_turn(engine, human.participant_id, {"message": "**bold** and `code`"})
    match_store.save(match)
    app = viewer_app(match_store)

    _, _, body = _get(app, f"/matches/{match.match_id}/watch")
    text = body.decode("utf-8")
    assert "<strong>bold</strong>" in text
    assert "<code>code</code>" in text


def test_hostile_html_in_message_and_display_name_is_escaped_not_executed() -> None:
    hostile_human = Participant(
        display_name=HOSTILE, kind=ParticipantKind.HUMAN, participant_id="p-hostile"
    )
    agent = Participant(
        display_name="Sonnet",
        kind=ParticipantKind.AGENT,
        agent_identity=AgentIdentity(model="claude-sonnet-5", provider="anthropic"),
        participant_id="p-agent",
    )
    match_store = InMemoryMatchStore()
    match, engine = start_match(participants=(hostile_human, agent))
    match.take_turn(engine, hostile_human.participant_id, {"message": HOSTILE})
    match_store.save(match)
    app = viewer_app(match_store)

    _, _, body = _get(app, f"/matches/{match.match_id}/watch")
    text = body.decode("utf-8")
    assert "<script>alert" not in text
    assert "&lt;script&gt;alert" in text


# --- page weight budget --------------------------------------------------------


def test_twenty_turn_match_page_stays_under_the_60kb_budget_and_carries_lang_viewport_meta() -> (
    None
):
    match_store = InMemoryMatchStore()
    match, engine = start_match()
    human, agent = match.participants
    for i in range(20):
        actor = human if i % 2 == 0 else agent
        match.take_turn(
            engine, actor.participant_id, {"delta": 1, "message": f"turn {i} commentary"}
        )
    match_store.save(match)
    app = viewer_app(match_store)

    status, _, body = _get(app, f"/matches/{match.match_id}/watch")
    assert status == "200 OK"
    assert len(body) < _PAGE_WEIGHT_BUDGET_BYTES, len(body)

    text = body.decode("utf-8")
    assert '<html lang="en">' in text
    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in text
    assert '<meta charset="utf-8">' in text
    assert len(match.turns) == 20


# --- GET /leaderboard (platform#11) --------------------------------------------


def test_get_leaderboard_returns_200_html_ordered_by_rating() -> None:
    ledger_store = InMemoryRatingLedgerStore()
    ledger_store.record_match(
        MatchOutcome(
            match_id="m1",
            entries=(
                OutcomeEntry(identity=_RATING_HUMAN, score=10),
                OutcomeEntry(identity=_RATING_AGENT, score=3),
            ),
        ),
        _RATING_SYSTEM,
    )
    app = viewer_app(InMemoryMatchStore(), ledger_store)

    status, headers, body = _get(app, "/leaderboard")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    text = body.decode("utf-8")
    assert '<html lang="en">' in text
    assert "Ada" in text
    assert "Sonnet" in text
    # Winner (Ada) outranks the loser (Sonnet) -- Ada's row appears first.
    assert text.index("Ada") < text.index("Sonnet")
    # Agent identity carries its benchmark chips.
    assert "claude-sonnet-5" in text
    assert "anthropic" in text


def test_get_leaderboard_links_rows_to_profile_pages() -> None:
    ledger_store = InMemoryRatingLedgerStore()
    ledger_store.record_match(
        MatchOutcome(
            match_id="m1",
            entries=(
                OutcomeEntry(identity=_RATING_HUMAN, score=10),
                OutcomeEntry(identity=_RATING_AGENT, score=3),
            ),
        ),
        _RATING_SYSTEM,
    )
    app = viewer_app(InMemoryMatchStore(), ledger_store)

    _, _, body = _get(app, "/leaderboard")
    text = body.decode("utf-8")
    assert 'href="/profiles/' in text


def test_get_leaderboard_with_no_rated_matches_is_a_welcoming_zero_state_not_an_error() -> None:
    app = viewer_app(InMemoryMatchStore(), InMemoryRatingLedgerStore())

    status, headers, body = _get(app, "/leaderboard")
    assert status == "200 OK"
    text = body.decode("utf-8")
    assert "no rated matches yet" in text.lower()
    assert "be the first" in text.lower()


def test_get_leaderboard_without_a_ledger_store_is_a_zero_state_not_a_crash() -> None:
    # viewer_app's ledger_store is optional -- /leaderboard must degrade to
    # the same welcoming zero-state rather than raising when it's omitted.
    app = viewer_app(InMemoryMatchStore())

    status, _, body = _get(app, "/leaderboard")
    assert status == "200 OK"
    assert "no rated matches yet" in body.decode("utf-8").lower()


def test_leaderboard_non_get_method_is_405() -> None:
    app = viewer_app(InMemoryMatchStore(), InMemoryRatingLedgerStore())
    status, _, _ = _get(app, "/leaderboard", method="POST")
    assert status == "405 Method Not Allowed"


def test_viewer_pages_carry_the_full_dazzle_shell() -> None:
    """The standalone viewer shell keeps parity with ``with_shell`` pages:
    canonical header (nav + theme toggle), the pre-paint theme snippet, and
    ``/site.js`` — so a visitor's explicit theme choice follows them onto
    the leaderboard and watch pages (spec c6/c12)."""
    app = viewer_app(InMemoryMatchStore(), InMemoryRatingLedgerStore())
    _, _, body = _get(app, "/leaderboard")
    text = body.decode("utf-8")
    assert 'nav aria-label="Primary"' in text
    assert 'id="theme-toggle"' in text
    assert "dataset.js" in text  # the pre-paint snippet, before first paint
    assert f'<script defer src="{asset_url("site.js")}"></script>' in text
    assert '<a class="skip-link" href="#main">' in text
    assert 'id="main"' in text
