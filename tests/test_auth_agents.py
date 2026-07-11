"""Tests for self-serve agent token onboarding (POST /auth/agents).

Two layers, mirroring the split in the code:

* ``league_site.auth.tokens.issue_self_serve`` — the guarded issuance
  primitive: per-name uniqueness against live tokens, plus a rolling
  one-hour issuance cap, both deterministic via an injected ``now``.
* ``league_site.auth.wsgi.with_auth`` — the ``POST /auth/agents`` route:
  JSON in, ``201 {"token", "identity"}`` out, with the guard surfaced as
  ``409``/``429`` and validation as ``400``. The acceptance-critical test
  composes ``with_auth(with_api(...))`` over one shared store and proves a
  token minted over HTTP authenticates ``POST /api/v1/matches``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Any

import pytest

from league_site.accounts.store import account_id_for
from league_site.api.wsgi import with_api
from league_site.auth import sessions, tokens
from league_site.auth.token_store import InMemoryTokenStore, TokenRecord, TokenStore
from league_site.auth.tokens import (
    DEFAULT_ISSUE_HOURLY_CAP,
    ISSUE_HOURLY_CAP_ENV,
    AgentNameTakenError,
    IssueCapExceededError,
    issue_hourly_cap_from_env,
    issue_self_serve,
)
from league_site.auth.wsgi import SESSION_COOKIE_NAME, with_auth

T0 = datetime(2026, 7, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """A signing secret so ``POST /auth/agents`` can read (and this module can
    mint) the human session cookie every mint now requires — see
    :func:`_signed_in`."""
    monkeypatch.setenv(sessions.SESSION_SECRET_ENV, "test-session-secret")


def _signed_in(
    subject: str = "42", provider: str = "github", display: str = "Ada"
) -> dict[str, str]:
    """``headers=`` value carrying a valid human session cookie.

    The mint is now human-gated: without one of these, ``POST /auth/agents``
    is a 401. The resulting token's ``owner_account_id`` is
    ``account_id_for(provider, subject)``.
    """
    token = sessions.issue({"subject": subject, "provider": provider, "display_name": display})
    return {"HTTP_COOKIE": f"{SESSION_COOKIE_NAME}={token}"}


# --- WSGI plumbing -----------------------------------------------------------


def _leaf_app(environ: dict[str, Any], start_response: Any) -> list[bytes]:
    start_response("404 Not Found", [("Content-Type", "text/plain; charset=utf-8")])
    return [b"leaf"]


def _call(
    app: Any,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[str, dict[str, str], bytes]:
    captured: dict[str, Any] = {}

    def start_response(status: str, response_headers: list[tuple[str, str]]) -> None:
        captured["status"] = status
        captured["headers"] = dict(response_headers)

    raw = body if body is not None else b""
    environ: dict[str, Any] = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "wsgi.url_scheme": "https",
        "HTTP_HOST": "league-of-agents.ai",
        "wsgi.input": BytesIO(raw),
        "CONTENT_LENGTH": str(len(raw)),
    }
    if headers:
        environ.update(headers)
    out = b"".join(app(environ, start_response))
    return captured["status"], captured["headers"], out


def _post_json(app: Any, path: str, payload: Any, **kwargs: Any) -> tuple[str, dict[str, str], Any]:
    status, headers, body = _call(
        app, "POST", path, body=json.dumps(payload).encode("utf-8"), **kwargs
    )
    return status, headers, json.loads(body) if body else None


def _mint_body(name: str = "probe-bot") -> dict[str, str]:
    return {"name": name, "model": "claude-sonnet-5", "provider": "anthropic"}


class _Clock:
    """A settable clock: ``with_auth(clock=...)`` takes any ``() -> datetime``."""

    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now = self.now + delta


# --- the guarded issuance primitive ------------------------------------------


def test_issue_self_serve_mints_a_verifiable_owned_token() -> None:
    store = InMemoryTokenStore()
    issued = issue_self_serve(
        store,
        agent_name="probe-bot",
        model="claude-sonnet-5",
        provider="anthropic",
        owner_account_id="github:owner",
        now=T0,
    )
    assert issued.token.startswith(tokens.TOKEN_PREFIX)
    identity = tokens.verify(store, issued.token)
    assert identity is not None
    assert identity.agent_name == "probe-bot"
    assert identity.model == "claude-sonnet-5"
    assert identity.provider == "anthropic"
    assert identity.created_at == T0
    # The token is anchored to the minting account — retrievable off the record.
    assert identity.owner_account_id == "github:owner"
    (record,) = store.list_all()
    assert record.owner_account_id == "github:owner"


def test_issue_self_serve_cap_is_per_account_not_store_wide() -> None:
    """The rolling cap is counted per owning account: one account hitting its
    cap must not lock out a different account (minting is human-gated, so the
    cap's job is to bound one account, not the whole store)."""
    store = InMemoryTokenStore()
    for index in range(2):
        issue_self_serve(
            store,
            agent_name=f"alice-bot-{index}",
            model="m",
            provider="p",
            owner_account_id="github:alice",
            hourly_cap=2,
            now=T0,
        )
    # Alice is now at her cap.
    with pytest.raises(IssueCapExceededError):
        issue_self_serve(
            store,
            agent_name="alice-bot-2",
            model="m",
            provider="p",
            owner_account_id="github:alice",
            hourly_cap=2,
            now=T0,
        )
    # ...but Bob's budget is untouched.
    issued = issue_self_serve(
        store,
        agent_name="bob-bot-0",
        model="m",
        provider="p",
        owner_account_id="github:bob",
        hourly_cap=2,
        now=T0,
    )
    assert tokens.verify(store, issued.token) is not None


def test_issue_self_serve_rejects_a_name_with_a_live_token() -> None:
    store = InMemoryTokenStore()
    issue_self_serve(
        store,
        owner_account_id="github:owner",
        agent_name="probe-bot",
        model="m",
        provider="p",
        now=T0,
    )
    with pytest.raises(AgentNameTakenError) as excinfo:
        issue_self_serve(
            store,
            owner_account_id="github:owner",
            agent_name="probe-bot",
            model="other",
            provider="other",
            now=T0,
        )
    assert excinfo.value.agent_name == "probe-bot"
    # A different name is unaffected.
    issue_self_serve(
        store,
        owner_account_id="github:owner",
        agent_name="other-bot",
        model="m",
        provider="p",
        now=T0,
    )


def test_revoking_a_token_frees_its_name() -> None:
    store = InMemoryTokenStore()
    first = issue_self_serve(
        store,
        owner_account_id="github:owner",
        agent_name="probe-bot",
        model="m",
        provider="p",
        now=T0,
    )
    tokens.revoke(store, first.identity.token_id)
    second = issue_self_serve(
        store,
        owner_account_id="github:owner",
        agent_name="probe-bot",
        model="m",
        provider="p",
        now=T0,
    )
    assert tokens.verify(store, second.token) is not None


def test_hourly_cap_blocks_issuance_past_the_cap() -> None:
    store = InMemoryTokenStore()
    for index in range(3):
        issue_self_serve(
            store,
            owner_account_id="github:owner",
            agent_name=f"bot-{index}",
            model="m",
            provider="p",
            now=T0,
        )
    with pytest.raises(IssueCapExceededError) as excinfo:
        issue_self_serve(
            store,
            owner_account_id="github:owner",
            agent_name="bot-3",
            model="m",
            provider="p",
            hourly_cap=3,
            now=T0,
        )
    assert excinfo.value.cap == 3


def test_cap_window_rolls_so_old_issuances_stop_counting() -> None:
    store = InMemoryTokenStore()
    issue_self_serve(
        store,
        owner_account_id="github:owner",
        agent_name="bot-0",
        model="m",
        provider="p",
        hourly_cap=1,
        now=T0,
    )
    # Still inside the rolling hour: blocked.
    with pytest.raises(IssueCapExceededError):
        issue_self_serve(
            store,
            owner_account_id="github:owner",
            agent_name="bot-1",
            model="m",
            provider="p",
            hourly_cap=1,
            now=T0 + timedelta(minutes=59),
        )
    # Past the window: the first issuance no longer counts.
    issue_self_serve(
        store,
        owner_account_id="github:owner",
        agent_name="bot-1",
        model="m",
        provider="p",
        hourly_cap=1,
        now=T0 + timedelta(minutes=61),
    )


def test_revoked_tokens_still_count_against_the_cap() -> None:
    """Revoking a token must not refund abuse budget inside the window."""
    store = InMemoryTokenStore()
    issued = issue_self_serve(
        store, owner_account_id="github:owner", agent_name="bot-0", model="m", provider="p", now=T0
    )
    tokens.revoke(store, issued.identity.token_id)
    with pytest.raises(IssueCapExceededError):
        issue_self_serve(
            store,
            owner_account_id="github:owner",
            agent_name="bot-1",
            model="m",
            provider="p",
            hourly_cap=1,
            now=T0,
        )


# --- the env-configured cap ---------------------------------------------------


def test_issue_hourly_cap_defaults_to_twenty() -> None:
    assert DEFAULT_ISSUE_HOURLY_CAP == 20
    assert issue_hourly_cap_from_env({}) == 20


def test_issue_hourly_cap_env_override() -> None:
    assert issue_hourly_cap_from_env({ISSUE_HOURLY_CAP_ENV: "5"}) == 5


def test_issue_hourly_cap_blank_env_value_keeps_default() -> None:
    assert issue_hourly_cap_from_env({ISSUE_HOURLY_CAP_ENV: ""}) == DEFAULT_ISSUE_HOURLY_CAP


@pytest.mark.parametrize("raw", ["abc", "0", "-3"])
def test_issue_hourly_cap_invalid_env_value_fails_loudly(raw: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        issue_hourly_cap_from_env({ISSUE_HOURLY_CAP_ENV: raw})
    assert ISSUE_HOURLY_CAP_ENV in str(excinfo.value)


# --- the store surface the guard relies on ------------------------------------


def test_in_memory_list_all_returns_every_record_including_revoked() -> None:
    store = InMemoryTokenStore()
    tokens.issue(store, agent_name="a", model="m", provider="p")
    issued_b = tokens.issue(store, agent_name="b", model="m", provider="p")
    tokens.revoke(store, issued_b.identity.token_id)
    revoked_by_name = {record.agent_name: record.revoked for record in store.list_all()}
    assert revoked_by_name == {"a": False, "b": True}


def test_token_store_list_all_default_raises_not_implemented() -> None:
    """The base-class default documents the contract stores must grow (see aws_tokens)."""

    class _Bare(TokenStore):
        def save(self, record: TokenRecord) -> None:  # pragma: no cover - unused
            raise AssertionError

        def get_by_hash(self, token_hash: str) -> TokenRecord | None:  # pragma: no cover
            raise AssertionError

        def revoke(self, token_id: str) -> None:  # pragma: no cover - unused
            raise AssertionError

    with pytest.raises(NotImplementedError):
        _Bare().list_all()


# --- POST /auth/agents ---------------------------------------------------------


def test_post_auth_agents_mints_a_token_and_identity() -> None:
    store = InMemoryTokenStore()
    app = with_auth(_leaf_app, token_store=store)
    status, headers, payload = _post_json(app, "/auth/agents", _mint_body(), headers=_signed_in())
    assert status == "201 Created"
    assert headers["Content-Type"].startswith("application/json")
    assert payload["token"].startswith(tokens.TOKEN_PREFIX)
    assert payload["identity"] == "agent:probe-bot:claude-sonnet-5:anthropic"
    # The token landed in the injected store.
    assert tokens.verify(store, payload["token"]) is not None


def test_post_auth_agents_without_a_session_is_401() -> None:
    """The human gate (task t6): no live session -> refused, with a
    machine-readable error whose message names the new onboarding path so the
    agent's operator knows a human must sign in and mint the token."""
    store = InMemoryTokenStore()
    app = with_auth(_leaf_app, token_store=store)
    status, _, payload = _post_json(app, "/auth/agents", _mint_body())  # no session cookie
    assert status == "401 Unauthorized"
    assert payload["code"] == "authentication_required"
    assert tokens.ONBOARDING_URL in payload["message"]
    # Nothing was minted.
    assert store.list_all() == []


def test_post_auth_agents_anchors_the_token_to_the_signed_in_account() -> None:
    """A session-authed mint stores ``owner_account_id`` on the record —
    retrievable, and equal to the signed-in human's account id."""
    store = InMemoryTokenStore()
    app = with_auth(_leaf_app, token_store=store)
    status, _, payload = _post_json(
        app, "/auth/agents", _mint_body(), headers=_signed_in(subject="4242")
    )
    assert status == "201 Created"
    (record,) = store.list_all()
    assert record.owner_account_id == account_id_for("github", "4242")
    # And it verifies (an owned token is not caught by the anonymous cutoff).
    assert tokens.verify(store, payload["token"]) is not None


def test_minted_token_authenticates_the_match_api() -> None:
    """The acceptance path: mint over HTTP, then create a match on /api/v1 with it."""
    store = InMemoryTokenStore()
    app = with_auth(with_api(_leaf_app, token_store=store), token_store=store)

    status, _, minted = _post_json(app, "/auth/agents", _mint_body(), headers=_signed_in())
    assert status == "201 Created"

    status, _, match = _post_json(
        app,
        "/api/v1/matches",
        {},
        headers={"HTTP_AUTHORIZATION": f"Bearer {minted['token']}"},
    )
    assert status == "201 Created"
    assert match["participants"][0]["participant_id"] == minted["identity"]


def test_get_auth_agents_is_method_not_allowed() -> None:
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore())
    status, _, body = _call(app, "GET", "/auth/agents")
    assert status == "405 Method Not Allowed"
    assert json.loads(body)["code"] == "method_not_allowed"


def test_post_auth_agents_without_a_store_is_503() -> None:
    app = with_auth(_leaf_app)
    status, _, payload = _post_json(app, "/auth/agents", _mint_body())
    assert status == "503 Service Unavailable"
    assert payload["code"] == "not_configured"


@pytest.mark.parametrize("missing", ["name", "model", "provider"])
def test_post_auth_agents_missing_field_is_400(missing: str) -> None:
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore())
    body = _mint_body()
    del body[missing]
    status, _, payload = _post_json(app, "/auth/agents", body, headers=_signed_in())
    assert status == "400 Bad Request"
    assert payload["code"] == "bad_request"
    assert missing in payload["message"]


@pytest.mark.parametrize("blank", ["", "   "])
def test_post_auth_agents_blank_name_is_400(blank: str) -> None:
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore())
    status, _, payload = _post_json(
        app, "/auth/agents", _mint_body(name=blank), headers=_signed_in()
    )
    assert status == "400 Bad Request"
    assert payload["code"] == "bad_request"


def test_post_auth_agents_non_string_field_is_400() -> None:
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore())
    body: dict[str, Any] = dict(_mint_body(), model=42)
    status, _, payload = _post_json(app, "/auth/agents", body, headers=_signed_in())
    assert status == "400 Bad Request"
    assert "model" in payload["message"]


def test_post_auth_agents_malformed_json_is_400() -> None:
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore())
    status, _, body = _call(app, "POST", "/auth/agents", body=b"{not json", headers=_signed_in())
    assert status == "400 Bad Request"
    assert json.loads(body)["code"] == "bad_request"


def test_post_auth_agents_non_object_body_is_400() -> None:
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore())
    status, _, payload = _post_json(
        app, "/auth/agents", ["not", "an", "object"], headers=_signed_in()
    )
    assert status == "400 Bad Request"


def test_post_auth_agents_duplicate_name_is_409() -> None:
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore())
    status, _, _ = _post_json(app, "/auth/agents", _mint_body(), headers=_signed_in())
    assert status == "201 Created"
    status, _, payload = _post_json(app, "/auth/agents", _mint_body(), headers=_signed_in())
    assert status == "409 Conflict"
    assert payload["code"] == "name_taken"


def test_post_auth_agents_over_cap_is_429_until_the_window_rolls() -> None:
    clock = _Clock(T0)
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore(), issue_hourly_cap=2, clock=clock)
    session = _signed_in()  # one account mints repeatedly -> hits its per-account cap
    for name in ("bot-0", "bot-1"):
        status, _, _ = _post_json(app, "/auth/agents", _mint_body(name=name), headers=session)
        assert status == "201 Created"

    status, _, payload = _post_json(app, "/auth/agents", _mint_body(name="bot-2"), headers=session)
    assert status == "429 Too Many Requests"
    assert payload["code"] == "issue_cap_exceeded"

    clock.advance(timedelta(minutes=61))
    status, _, _ = _post_json(app, "/auth/agents", _mint_body(name="bot-2"), headers=session)
    assert status == "201 Created"


def test_post_auth_agents_cap_reads_env_at_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ISSUE_HOURLY_CAP_ENV, "1")
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore())
    session = _signed_in()
    status, _, _ = _post_json(app, "/auth/agents", _mint_body(name="bot-0"), headers=session)
    assert status == "201 Created"
    status, _, payload = _post_json(app, "/auth/agents", _mint_body(name="bot-1"), headers=session)
    assert status == "429 Too Many Requests"
    assert payload["code"] == "issue_cap_exceeded"


def test_non_agents_auth_paths_still_pass_through() -> None:
    """Adding /auth/agents must not disturb the pass-through contract."""
    app = with_auth(_leaf_app, token_store=InMemoryTokenStore())
    status, _, body = _call(app, "GET", "/anything-else")
    assert status == "404 Not Found"
    assert body == b"leaf"


def test_site_app_ships_self_serve_issuance_wired() -> None:
    """The shipped composition must answer /auth/agents itself (no 503).

    Integration seam: ``site_app`` resolves one token store and hands the
    same instance to both ``with_auth`` (issuance) and ``with_api``
    (authentication) — a token minted over HTTP by a signed-in human must
    authenticate API calls.
    """
    from league_site.web.http import site_app

    app = site_app()
    status, headers, body = _call(
        app,
        "POST",
        "/auth/agents",
        body=json.dumps({"name": "sitewired", "model": "m", "provider": "p"}).encode("utf-8"),
        headers=_signed_in(),
    )
    assert status == "201 Created"
    payload = json.loads(body)
    assert payload["token"].startswith("loa_")


def test_site_app_refuses_anonymous_minting() -> None:
    """The shipped composition enforces the human gate: no session -> 401
    naming the onboarding path (the anonymous self-serve path is closed)."""
    from league_site.web.http import site_app

    app = site_app()
    status, _, body = _call(
        app,
        "POST",
        "/auth/agents",
        body=json.dumps({"name": "sitewired", "model": "m", "provider": "p"}).encode("utf-8"),
    )
    assert status == "401 Unauthorized"
    payload = json.loads(body)
    assert payload["code"] == "authentication_required"
    assert tokens.ONBOARDING_URL in payload["message"]
