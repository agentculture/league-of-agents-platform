"""Route-level tests for :func:`league_site.play.wsgi.with_play`.

Exercises the play surface directly (session injected into ``environ`` under
``SESSION_ENVIRON_KEY``, the way ``with_auth`` populates it), against an
injected in-memory match store and the :class:`tests._play_support.
PlayableEngine` registered under ``"solo-vs-bot"``. The full composed-site
flow (real signed cookie, ``site_app``) lives in
``tests/test_play_http_site.py``.
"""

from __future__ import annotations

from typing import Any

from league_site.capacity.config import CapacityConfig
from league_site.matches import InMemoryMatchStore, Match
from league_site.play.wsgi import with_play
from tests._play_support import (
    PlayableEngine,
    WSGIApp,
    call_page,
    human_participant,
    session_for,
)

_REGISTRY = {"solo-vs-bot": PlayableEngine}


def _inner_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    start_response("200 OK", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"inner"]


def _build(
    *,
    capacity_config: CapacityConfig | None = None,
) -> tuple[WSGIApp, InMemoryMatchStore]:
    store = InMemoryMatchStore()
    app = with_play(
        _inner_app,
        match_store=store,
        engine_registry=_REGISTRY,
        capacity_config=capacity_config,
    )
    return app, store


def _create_match(store: InMemoryMatchStore, *participants: Any, target: int = 6) -> Match:
    engine = PlayableEngine(target=target)
    match = Match.create(game_id="solo-vs-bot", participants=list(participants))
    match.start(engine)
    store.save(match)
    return match


# --- routing ------------------------------------------------------------------


def test_non_play_paths_pass_through_to_the_wrapped_app() -> None:
    app, _ = _build()
    for path in ("/", "/index", "/api/v1/leaderboard", "/playground"):
        status, _, body = call_page(app, "GET", path)
        assert status == "200 OK", path
        assert body == "inner", path


def test_unknown_play_subpath_404s_as_html() -> None:
    app, _ = _build()
    status, headers, body = call_page(app, "GET", "/play/nope", session=session_for())
    assert status == "404 Not Found"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "<!doctype html>" in body


def test_wrong_methods_are_405() -> None:
    app, store = _build()
    session = session_for()
    match = _create_match(store, human_participant(session))
    for method, path in (
        ("POST", "/play"),
        ("GET", "/play/matches"),
        ("POST", f"/play/matches/{match.match_id}"),
        ("GET", f"/play/matches/{match.match_id}/turns"),
    ):
        status, _, _ = call_page(app, method, path, session=session)
        assert status == "405 Method Not Allowed", (method, path)


# --- GET /play ------------------------------------------------------------------


def test_hub_signed_out_invites_sign_in_and_offers_no_form() -> None:
    app, _ = _build()
    status, headers, body = call_page(app, "GET", "/play", session=None)
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert "/auth/login/github" in body
    assert "<form" not in body


def test_hub_signed_in_offers_the_start_form() -> None:
    app, _ = _build()
    status, _, body = call_page(app, "GET", "/play", session=session_for(display="Ada"))
    assert status == "200 OK"
    assert '<form method="post" action="/play/matches"' in body
    assert "solo-vs-bot" in body
    assert "/auth/login/github" not in body


def test_hub_lists_only_the_humans_own_live_matches() -> None:
    app, store = _build()
    session = session_for(subject="42")
    other = session_for(subject="99", display="Eve")
    mine = _create_match(store, human_participant(session))
    theirs = _create_match(store, human_participant(other))
    finished = _create_match(store, human_participant(session), target=1)
    finished.take_turn(
        PlayableEngine(target=1), human_participant(session).participant_id, {"points": 1}
    )
    finished.complete(PlayableEngine(target=1))
    store.save(finished)

    _, _, body = call_page(app, "GET", "/play", session=session)
    assert f"/play/matches/{mine.match_id}" in body
    assert theirs.match_id not in body
    assert finished.match_id not in body


# --- POST /play/matches ---------------------------------------------------------


def test_create_signed_out_is_401() -> None:
    app, store = _build()
    status, _, body = call_page(
        app, "POST", "/play/matches", form={"mode": "solo-vs-bot"}, session=None
    )
    assert status == "401 Unauthorized"
    assert "/auth/login/github" in body
    assert store.list_ids() == []


def test_create_redirects_to_the_new_matchs_play_view() -> None:
    app, store = _build()
    session = session_for(subject="42", display="Ada")
    status, headers, _ = call_page(
        app, "POST", "/play/matches", form={"mode": "solo-vs-bot"}, session=session
    )
    assert status == "303 See Other"
    (match_id,) = store.list_ids()
    assert headers["Location"] == f"/play/matches/{match_id}"

    match = store.load(match_id)
    assert match.game_id == "solo-vs-bot"
    assert match.status.value == "active"
    (participant,) = match.participants
    assert participant.participant_id == "human:github:42"
    assert participant.kind.value == "human"
    assert participant.display_name == "Ada"


def test_create_with_an_unoffered_mode_is_400_and_creates_nothing() -> None:
    app, store = _build()
    for mode in ("stub-duel", "team-vs-team", "", "no-such-mode"):
        status, _, _ = call_page(
            app, "POST", "/play/matches", form={"mode": mode}, session=session_for()
        )
        assert status == "400 Bad Request", mode
    assert store.list_ids() == []


def test_create_over_capacity_is_429() -> None:
    config = CapacityConfig(
        max_concurrent_matches=1,
        max_stored_matches=100,
        max_match_age_days_hot=3,
        max_archive_age_days=180,
    )
    app, store = _build(capacity_config=config)
    _create_match(store, human_participant(session_for(subject="99")))
    status, _, body = call_page(
        app, "POST", "/play/matches", form={"mode": "solo-vs-bot"}, session=session_for()
    )
    assert status == "429 Too Many Requests"
    assert "limit reached" in body  # the same shared refusal message the API renders
    assert len(store.list_ids()) == 1


# --- GET /play/matches/<id> ------------------------------------------------------


def test_play_view_shows_board_and_legal_action_form_on_your_turn() -> None:
    app, store = _build()
    session = session_for(subject="42", display="Ada")
    match = _create_match(store, human_participant(session))

    status, headers, body = call_page(
        app, "GET", f"/play/matches/{match.match_id}", session=session
    )
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    # the reused viewer board rendering
    assert "Participants" in body
    assert "Transcript" in body
    # the legal-actions form
    assert f'<form method="post" action="/play/matches/{match.match_id}/turns"' in body
    assert body.count("<option") == 3
    assert "&quot;points&quot;" in body
    # it's the human's turn: no auto-refresh under them
    assert 'http-equiv="refresh"' not in body


def test_play_view_refreshes_while_waiting_for_the_other_side() -> None:
    app, store = _build()
    ada = session_for(subject="42", display="Ada")
    eve = session_for(subject="99", display="Eve")
    match = _create_match(store, human_participant(ada), human_participant(eve))

    _, _, waiting_body = call_page(app, "GET", f"/play/matches/{match.match_id}", session=eve)
    assert 'http-equiv="refresh" content="5"' in waiting_body
    assert "<option" not in waiting_body

    _, _, acting_body = call_page(app, "GET", f"/play/matches/{match.match_id}", session=ada)
    assert 'http-equiv="refresh"' not in acting_body
    assert "<option" in acting_body


def test_play_view_redirects_non_participants_to_the_spectate_page() -> None:
    app, store = _build()
    match = _create_match(store, human_participant(session_for(subject="42")))
    for session in (session_for(subject="99"), None):
        status, headers, _ = call_page(
            app, "GET", f"/play/matches/{match.match_id}", session=session
        )
        assert status == "302 Found"
        assert headers["Location"] == f"/matches/{match.match_id}/watch"


def test_play_view_unknown_match_404s() -> None:
    app, _ = _build()
    status, _, _ = call_page(app, "GET", "/play/matches/nope", session=session_for())
    assert status == "404 Not Found"


def test_play_view_of_a_completed_match_links_the_replay_and_offers_no_form() -> None:
    app, store = _build()
    session = session_for(subject="42", display="Ada")
    participant = human_participant(session)
    engine = PlayableEngine(target=1)
    match = _create_match(store, participant, target=1)
    match.take_turn(engine, participant.participant_id, {"points": 1})
    match.complete(engine)
    store.save(match)

    status, _, body = call_page(app, "GET", f"/play/matches/{match.match_id}", session=session)
    assert status == "200 OK"
    assert f"/matches/{match.match_id}/watch" in body
    assert "Final score" in body
    assert "<form" not in body
    assert 'http-equiv="refresh"' not in body


# --- POST /play/matches/<id>/turns ------------------------------------------------


def test_submit_turn_applies_the_action_and_redirects_back() -> None:
    app, store = _build()
    session = session_for(subject="42")
    participant = human_participant(session)
    match = _create_match(store, participant)
    _, _, body = call_page(app, "GET", f"/play/matches/{match.match_id}", session=session)
    value = '{"points": 3}'
    assert "points&quot;: 3" in body

    status, headers, _ = call_page(
        app,
        "POST",
        f"/play/matches/{match.match_id}/turns",
        form={"action": value},
        session=session,
    )
    assert status == "303 See Other"
    assert headers["Location"] == f"/play/matches/{match.match_id}"

    reloaded = store.load(match.match_id)
    assert len(reloaded.turns) == 1
    assert reloaded.turns[0].action == {"points": 3}
    assert reloaded.game_state["scores"][participant.participant_id] == 3


def test_submit_turn_not_in_legal_actions_redirects_with_a_notice_and_no_state_change() -> None:
    """The usual cause is a double-tap racing the turn it already played
    (2026-07-11 feedback round: a raw 400 read as breakage), so the refusal
    is a redirect back to the fresh board with an honest notice — the
    engine still never sees the string."""
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))

    for value in ('{"points": 99}', "not json", ""):
        status, headers, _ = call_page(
            app,
            "POST",
            f"/play/matches/{match.match_id}/turns",
            form={"action": value},
            session=session,
        )
        assert status == "303 See Other", value
        assert headers["Location"] == f"/play/matches/{match.match_id}?notice=illegal", value

    assert store.load(match.match_id).turns == []


def test_submit_turn_signed_out_is_401_with_no_state_change() -> None:
    app, store = _build()
    match = _create_match(store, human_participant(session_for(subject="42")))
    status, _, body = call_page(
        app,
        "POST",
        f"/play/matches/{match.match_id}/turns",
        form={"action": '{"points": 1}'},
        session=None,
    )
    assert status == "401 Unauthorized"
    assert "/auth/login/github" in body
    assert store.load(match.match_id).turns == []


def test_submit_turn_as_non_participant_is_403_with_no_state_change() -> None:
    app, store = _build()
    match = _create_match(store, human_participant(session_for(subject="42")))
    status, _, body = call_page(
        app,
        "POST",
        f"/play/matches/{match.match_id}/turns",
        form={"action": '{"points": 1}'},
        session=session_for(subject="99", display="Eve"),
    )
    assert status == "403 Forbidden"
    assert f"/matches/{match.match_id}/watch" in body
    assert store.load(match.match_id).turns == []


def test_submit_turn_on_a_completed_match_redirects_to_the_final_view() -> None:
    """Most commonly a double-tap on the final turn: the first submit
    finished the match. The play view (final score, replay link) is the
    honest answer, not a conflict page — and nothing plays twice."""
    app, store = _build()
    session = session_for(subject="42")
    participant = human_participant(session)
    engine = PlayableEngine(target=1)
    match = _create_match(store, participant, target=1)
    match.take_turn(engine, participant.participant_id, {"points": 1})
    match.complete(engine)
    store.save(match)

    status, headers, _ = call_page(
        app,
        "POST",
        f"/play/matches/{match.match_id}/turns",
        form={"action": '{"points": 1}'},
        session=session,
    )
    assert status == "303 See Other"
    assert headers["Location"] == f"/play/matches/{match.match_id}"
    assert len(store.load(match.match_id).turns) == 1


def test_submit_turn_completes_the_match_once_the_engine_reports_over() -> None:
    app, store = _build()
    session = session_for(subject="42")
    match = _create_match(store, human_participant(session))  # target 6

    for _ in range(2):
        status, _, _ = call_page(
            app,
            "POST",
            f"/play/matches/{match.match_id}/turns",
            form={"action": '{"points": 3}'},
            session=session,
        )
        assert status == "303 See Other"

    assert store.load(match.match_id).status.value == "completed"


# --- CSRF stance -----------------------------------------------------------------


def test_session_cookie_is_samesite_lax_the_plays_csrf_boundary() -> None:
    """The play surface's CSRF stance (documented in league_site.play):
    state-changing form POSTs are authenticated solely by the session
    cookie, and that cookie is SameSite=Lax + HttpOnly, so a cross-site
    form POST arrives without it and is refused as anonymous (401). This
    test pins the cookie attributes the stance depends on — if the cookie
    ever stops being SameSite=Lax, the play surface needs a CSRF token."""
    from league_site.auth.wsgi import _set_cookie_header

    header = _set_cookie_header({"wsgi.url_scheme": "https"}, "token-value")
    assert "SameSite=Lax" in header
    assert "HttpOnly" in header
    assert "Secure" in header
