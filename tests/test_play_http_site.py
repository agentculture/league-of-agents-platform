"""Integration tests: the browser play surface through the full composed ``site_app``.

Drives the whole signed-in human loop with a *real* signed session cookie —
create a solo-vs-bot match from the play hub, see the board + legal actions,
submit form-encoded turns through to completion, then land on the shareable
spectate replay — proving the play surface is mounted where ``with_auth``
has already resolved the session, that its pages carry the session-aware
header, and that the same shared stores back the play, API, and viewer
surfaces.
"""

from __future__ import annotations

import html as html_mod
import json
import re
import shutil

import pytest

from league_site.auth import sessions
from league_site.auth.token_store import InMemoryTokenStore
from league_site.auth.wsgi import SESSION_COOKIE_NAME
from league_site.matches import InMemoryMatchStore
from league_site.ratings.ledger import InMemoryRatingLedgerStore
from league_site.web.http import site_app
from tests._api_support import call
from tests._play_support import GridBoardEngine, PlayableEngine, call_page

_REGISTRY = {"solo-vs-bot": PlayableEngine}


@pytest.fixture(autouse=True)
def _session_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LEAGUE_SESSION_SECRET", "test-session-secret")


def _cookie(subject: str = "42", display_name: str = "Ada") -> dict[str, str]:
    token = sessions.issue({"subject": subject, "provider": "github", "display_name": display_name})
    return {"Cookie": f"{SESSION_COOKIE_NAME}={token}"}


def _build() -> tuple[object, InMemoryMatchStore]:
    store = InMemoryMatchStore()
    app = site_app(
        match_store=store,
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
        engine_registry=_REGISTRY,
    )
    return app, store


def test_signed_in_human_plays_a_solo_vs_bot_match_to_completion_in_the_browser() -> None:
    app, store = _build()
    cookie = _cookie(subject="42", display_name="Ada")

    # The hub offers the start affordance under the session-aware header.
    status, headers, hub = call_page(app, "GET", "/play", headers=cookie, session_key_present=False)
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    assert '<form method="post" action="/play/matches"' in hub
    assert "Ada" in hub and "/auth/logout" in hub  # session-aware header chip

    # Start a solo-vs-bot match with the session identity as participant.
    status, headers, _ = call_page(
        app,
        "POST",
        "/play/matches",
        form={"mode": "solo-vs-bot"},
        headers=cookie,
        session_key_present=False,
    )
    assert status == "303 See Other"
    play_url = headers["Location"]
    match_id = re.fullmatch(r"/play/matches/(?P<id>[^/]+)", play_url)["id"]
    (participant,) = store.load(match_id).participants
    assert participant.participant_id == "human:github:42"

    # Board + legal actions render; submit turns through to completion.
    for _ in range(10):
        status, _, page = call_page(app, "GET", play_url, headers=cookie, session_key_present=False)
        assert status == "200 OK"
        if store.load(match_id).status.value == "completed":
            break
        assert "<option" in page, "expected a legal-actions form while the match is live"
        status, headers, _ = call_page(
            app,
            "POST",
            f"{play_url}/turns",
            form={"action": '{"points": 3}'},
            headers=cookie,
            session_key_present=False,
        )
        assert status == "303 See Other"
        assert headers["Location"] == play_url

    assert store.load(match_id).status.value == "completed"

    # The finished play view shows the final score and links the replay.
    status, _, page = call_page(app, "GET", play_url, headers=cookie, session_key_present=False)
    assert status == "200 OK"
    assert "Final score" in page
    assert "<form" not in page
    assert f"/matches/{match_id}/watch" in page

    # The shareable spectate replay is live for anyone, no login.
    status, headers, watch = call(app, "GET", f"/matches/{match_id}/watch")
    assert status == "200 OK"
    watch_text = watch.decode("utf-8") if isinstance(watch, bytes) else watch
    assert "FINISHED" in watch_text

    # The hub no longer lists the finished match as resumable.
    _, _, hub = call_page(app, "GET", "/play", headers=cookie, session_key_present=False)
    assert match_id not in hub


def test_signed_out_visitors_are_invited_to_sign_in_and_cannot_post() -> None:
    app, store = _build()

    status, _, page = call_page(app, "GET", "/play", session_key_present=False)
    assert status == "200 OK"
    assert "/auth/login/github" in page

    status, _, _ = call_page(
        app, "POST", "/play/matches", form={"mode": "solo-vs-bot"}, session_key_present=False
    )
    assert status == "401 Unauthorized"
    assert store.list_ids() == []


def test_non_participant_session_is_redirected_to_spectate_and_cannot_move() -> None:
    app, store = _build()
    owner = _cookie(subject="42", display_name="Ada")
    intruder = _cookie(subject="99", display_name="Eve")

    _, headers, _ = call_page(
        app,
        "POST",
        "/play/matches",
        form={"mode": "solo-vs-bot"},
        headers=owner,
        session_key_present=False,
    )
    play_url = headers["Location"]
    match_id = play_url.rsplit("/", 1)[-1]

    status, headers, _ = call_page(
        app, "GET", play_url, headers=intruder, session_key_present=False
    )
    assert status == "302 Found"
    assert headers["Location"] == f"/matches/{match_id}/watch"

    status, _, _ = call_page(
        app,
        "POST",
        f"{play_url}/turns",
        form={"action": '{"points": 3}'},
        headers=intruder,
        session_key_present=False,
    )
    assert status == "403 Forbidden"
    assert store.load(match_id).turns == []


_STYLE_RE = re.compile(r"<style>.*?</style>", re.S)


def _markup(body: str) -> str:
    """*body* without its inline ``<style>`` blocks — the stylesheet names the
    board classes too, so markup assertions must not match CSS text."""
    return _STYLE_RE.sub("", body)


def _request_target(href: str) -> str:
    """*href* without its ``#fragment`` — what a browser actually requests."""
    return html_mod.unescape(href).split("#", 1)[0]


def _plan_order_values(body: str) -> list[str]:
    """The (unescaped) hidden ``order`` payloads in the End-turn form."""
    return [html_mod.unescape(value) for value in re.findall(r'name="order" value="([^"]*)"', body)]


def _play_board_flow(app: object, store: InMemoryMatchStore, cookie: dict[str, str]) -> None:
    """Create a grid match and play one turn purely through the board:
    select a unit, follow a staging link, then ack the plan (End turn)."""
    status, headers, _ = call_page(
        app,
        "POST",
        "/play/matches",
        form={"mode": "solo-vs-bot"},
        headers=cookie,
        session_key_present=False,
    )
    assert status == "303 See Other"
    play_url = headers["Location"]
    match_id = play_url.rsplit("/", 1)[-1]

    # Step 1 — the board itself offers the human's units as selection links.
    status, _, page = call_page(app, "GET", play_url, headers=cookie, session_key_present=False)
    assert status == "200 OK"
    markup = _markup(page)
    assert 'class="board"' in markup
    unit_hrefs = re.findall(rf'href="({re.escape(play_url)}\?unit=[^"#]+)', markup)
    assert unit_hrefs, "expected at least one selectable unit on the board"
    assert "board-target" not in markup

    # Step 2 — selecting a unit turns its legal targets into staging links.
    status, _, selected_page = call_page(
        app, "GET", _request_target(unit_hrefs[0]), headers=cookie, session_key_present=False
    )
    assert status == "200 OK"
    selected_markup = _markup(selected_page)
    assert "board-unit-selected" in selected_markup
    staging_hrefs = re.findall(r'href="([^"]*staged=[^"]*)"', selected_markup)
    assert staging_hrefs, "expected staging links for the selected unit"

    # Step 3 — following one stages the order (a GET: nothing submitted yet)...
    status, _, planned_page = call_page(
        app, "GET", _request_target(staging_hrefs[0]), headers=cookie, session_key_present=False
    )
    assert status == "200 OK"
    assert store.load(match_id).turns == []
    order_values = _plan_order_values(planned_page)
    assert order_values, "expected the staged order in the End-turn form"
    turn_value = re.search(r'name="turn" value="([^"]*)"', planned_page).group(1)

    # Step 4 — ...and the End-turn POST plays the whole plan as one turn.
    status, headers, _ = call_page(
        app,
        "POST",
        f"{play_url}/turns",
        form={"order": order_values, "turn": turn_value},
        headers=cookie,
        session_key_present=False,
    )
    assert status == "303 See Other"
    assert headers["Location"] == play_url
    reloaded = store.load(match_id)
    assert len(reloaded.turns) == 1
    assert reloaded.turns[0].action == {"actions": [json.loads(v) for v in order_values]}


def test_board_controls_drive_a_grid_match_through_the_composed_site() -> None:
    store = InMemoryMatchStore()
    app = site_app(
        match_store=store,
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
        engine_registry={"solo-vs-bot": GridBoardEngine},
    )
    _play_board_flow(app, store, _cookie(subject="42", display_name="Ada"))


@pytest.mark.skipif(shutil.which("league") is None, reason="league CLI is not installed")
def test_board_controls_drive_a_real_cli_grid_match_through_the_composed_site() -> None:
    """The same two-step board flow against the real ``league`` subprocess,
    via the production ``default_engine_registry()`` (no injected fake)."""
    store = InMemoryMatchStore()
    app = site_app(
        match_store=store,
        token_store=InMemoryTokenStore(),
        ledger_store=InMemoryRatingLedgerStore(),
    )
    _play_board_flow(app, store, _cookie(subject="42", display_name="Ada"))
    (match_id,) = store.list_ids()
    assert store.load(match_id).game_state["turn"] == 1


def test_api_json_and_docs_pages_still_route_with_play_mounted() -> None:
    app, _ = _build()

    status, headers, payload = call(app, "GET", "/api/v1/leaderboard")
    assert status == "200 OK"
    assert headers["Content-Type"] == "application/json; charset=utf-8"
    assert payload == {"leaderboard": []}

    status, headers, _ = call(app, "GET", "/")
    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
