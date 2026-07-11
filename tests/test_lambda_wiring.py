"""Tests for :mod:`league_site.aws_lambda.wiring` — env-driven store selection.

``build_site_app()`` is what the Lambda entrypoint serves: the full composed
arena (:func:`league_site.web.http.site_app`) over DynamoDB-backed stores
when the ``*_TABLE_NAME`` variables are present, and over the same in-memory
defaults as the local dev server when they are absent. Every test injects a
fake DynamoDB resource so nothing here ever touches real AWS, needs
credentials, or needs a region configured.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import pytest

from league_site.accounts.aws import DynamoDBAccountStore
from league_site.auth import tokens
from league_site.auth.aws_tokens import DynamoDBTokenStore
from league_site.aws_lambda import wiring
from league_site.matches.aws import DynamoDBMatchStore
from league_site.ratings.aws import DynamoDBRatingLedgerStore
from league_site.web.http import site_app
from tests._api_support import bearer, call

FULL_ENV = {
    "MATCHES_TABLE_NAME": "league-matches",
    "ARCHIVE_BUCKET_NAME": "league-archive",
    "TOKENS_TABLE_NAME": "league-tokens",
    "RATINGS_TABLE_NAME": "league-ratings",
}


class FakeTable:
    """Stand-in for a boto3 DynamoDB Table: enough surface for every adapter.

    Serves the three stores at once — ``get_item``/``put_item``/
    ``delete_item`` for matches and tokens, a generic single-``eq`` ``query``
    (resolved via the condition object's own ``get_expression()``) for both
    the match store's ``by-status-updated`` GSI queries and the rating
    ledger's ``PK``-partition queries, and an ``ADD``-only ``update_item``
    for the ledger's insertion-order counter and token revocation. No
    pagination (single-page responses) — the per-adapter unit suites prove
    the ``LastEvaluatedKey`` loops; this fake proves the composed wiring.
    """

    def __init__(self) -> None:
        self.items: dict[tuple[str, str], dict[str, Any]] = {}

    def put_item(
        self, *, Item: dict[str, Any], ConditionExpression: Any = None
    ) -> None:  # noqa: N803 - matches boto3's kwarg casing
        key = (Item["PK"], Item["SK"])
        if ConditionExpression is not None and key in self.items:
            raise AssertionError(f"conditional put would overwrite {key}")
        self.items[key] = Item

    def get_item(self, *, Key: dict[str, str]) -> dict[str, Any]:  # noqa: N803
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item is not None else {}

    def delete_item(self, *, Key: dict[str, str]) -> None:  # noqa: N803
        self.items.pop((Key["PK"], Key["SK"]), None)

    def query(self, **kwargs: Any) -> dict[str, Any]:
        expression = kwargs["KeyConditionExpression"].get_expression()
        assert expression["operator"] == "="
        key_name = expression["values"][0].name
        key_value = expression["values"][1]
        matching = [item for item in self.items.values() if item.get(key_name) == key_value]
        matching.sort(key=lambda item: str(item["SK"]))
        return {"Items": matching}

    def update_item(
        self,
        *,
        Key: dict[str, str],  # noqa: N803
        UpdateExpression: str,  # noqa: N803
        ExpressionAttributeValues: dict[str, Any],  # noqa: N803
        ReturnValues: str = "NONE",  # noqa: N803
    ) -> dict[str, Any]:
        key = (Key["PK"], Key["SK"])
        added = re.fullmatch(r"ADD (\w+) (:\w+)", UpdateExpression)
        if added is not None:
            attr, placeholder = added.group(1), added.group(2)
            item = self.items.setdefault(key, {"PK": Key["PK"], "SK": Key["SK"]})
            item[attr] = item.get(attr, 0) + ExpressionAttributeValues[placeholder]
            if ReturnValues == "UPDATED_NEW":
                return {"Attributes": {attr: item[attr]}}
            return {}
        assigned = re.fullmatch(r"SET (\w+) = (:\w+)", UpdateExpression)
        assert assigned is not None, f"fake cannot apply {UpdateExpression!r}"
        self.items[key][assigned.group(1)] = ExpressionAttributeValues[assigned.group(2)]
        return {}


class FakeDynamoDBServiceResource:
    """Stand-in for ``boto3.resource("dynamodb")``: one :class:`FakeTable` per name."""

    def __init__(self) -> None:
        self.tables: dict[str, FakeTable] = {}

    def Table(self, name: str) -> FakeTable:  # noqa: N802 - matches boto3's method casing
        return self.tables.setdefault(name, FakeTable())


@pytest.fixture()
def recorded_site_app(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Route ``wiring.site_app`` through a recorder capturing its kwargs."""
    captured: dict[str, Any] = {}

    def recording_site_app(**kwargs: Any) -> Any:
        captured["kwargs"] = kwargs
        captured["app"] = site_app(**kwargs)
        return captured["app"]

    monkeypatch.setattr(wiring, "site_app", recording_site_app)
    return captured


# --- store selection ---------------------------------------------------------


def test_empty_env_builds_the_same_bare_site_app_as_today(recorded_site_app) -> None:
    """No table env vars -> no store kwargs at all: byte-identical to a bare
    ``site_app()`` (fresh in-memory defaults), exactly what local dev and the
    existing Lambda behavior rely on. With a session secret present, the app
    object is returned unwrapped."""
    app = wiring.build_site_app({"LEAGUE_SESSION_SECRET": "s"})

    assert recorded_site_app["kwargs"] == {}
    assert app is recorded_site_app["app"]


def test_full_env_selects_dynamodb_backed_stores(recorded_site_app) -> None:
    resource = FakeDynamoDBServiceResource()
    wiring.build_site_app({**FULL_ENV, "LEAGUE_SESSION_SECRET": "s"}, dynamodb_resource=resource)

    kwargs = recorded_site_app["kwargs"]
    assert set(kwargs) == {"match_store", "token_store", "ledger_store", "account_store"}
    assert isinstance(kwargs["match_store"], DynamoDBMatchStore)
    assert isinstance(kwargs["token_store"], DynamoDBTokenStore)
    assert isinstance(kwargs["ledger_store"], DynamoDBRatingLedgerStore)
    assert isinstance(kwargs["account_store"], DynamoDBAccountStore)
    # each store bound to its own env-named table; accounts ride the tokens table
    assert set(resource.tables) == {"league-matches", "league-tokens", "league-ratings"}
    assert kwargs["account_store"]._table_name == "league-tokens"


def test_each_table_env_var_is_wired_independently(recorded_site_app) -> None:
    resource = FakeDynamoDBServiceResource()
    wiring.build_site_app(
        {"MATCHES_TABLE_NAME": "league-matches", "LEAGUE_SESSION_SECRET": "s"},
        dynamodb_resource=resource,
    )

    kwargs = recorded_site_app["kwargs"]
    assert set(kwargs) == {"match_store"}
    assert isinstance(kwargs["match_store"], DynamoDBMatchStore)


def test_env_var_names_match_the_deploy_contract() -> None:
    """The exact names ``infra/template.yaml`` sets on the serving Lambda."""
    assert wiring.MATCHES_TABLE_ENV == "MATCHES_TABLE_NAME"
    assert wiring.ARCHIVE_BUCKET_ENV == "ARCHIVE_BUCKET_NAME"
    assert wiring.TOKENS_TABLE_ENV == "TOKENS_TABLE_NAME"
    assert wiring.RATINGS_TABLE_ENV == "RATINGS_TABLE_NAME"
    assert wiring.SESSION_SECRET_ENV == "LEAGUE_SESSION_SECRET"


# --- missing-secret degradation ----------------------------------------------


def test_without_a_session_secret_a_stale_session_cookie_stays_anonymous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pre-OAuth launch: ``LEAGUE_SESSION_SECRET`` is unset. A request that
    carries a (stale/garbage) ``league_session`` cookie must browse
    anonymously — not crash the whole site with ``MissingSecretError``."""
    monkeypatch.delenv("LEAGUE_SESSION_SECRET", raising=False)
    app = wiring.build_site_app({})

    status, headers, body = call(
        app, "GET", "/", headers={"Cookie": "league_session=stale.garbage"}
    )

    assert status == "200 OK"
    assert headers["Content-Type"] == "text/html; charset=utf-8"
    # Pre-OAuth prod still shows the sign-in entry — the flow itself keeps its
    # existing disabled behavior, but the header link is present (t8).
    text = body.decode("utf-8")
    assert 'href="/auth/login/github"' in text
    assert "/auth/login/google" not in text


def test_without_a_session_secret_other_cookies_pass_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LEAGUE_SESSION_SECRET", raising=False)
    app = wiring.build_site_app({})

    status, _, _ = call(
        app, "GET", "/", headers={"Cookie": "theme=dark; league_session=x.y; lang=en"}
    )

    assert status == "200 OK"


def test_without_a_session_secret_the_bearer_token_api_still_works(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LEAGUE_SESSION_SECRET", raising=False)
    resource = FakeDynamoDBServiceResource()
    app = wiring.build_site_app(FULL_ENV, dynamodb_resource=resource)
    token_store = DynamoDBTokenStore("league-tokens", resource=resource)
    issued = tokens.issue(
        token_store,
        agent_name="probe-bot",
        model="claude-sonnet-5",
        provider="anthropic",
        owner_account_id="github:probe-owner",
    )

    status, _, created = call(
        app,
        "POST",
        "/api/v1/matches",
        body={},
        headers={**bearer(issued.token), "Cookie": "league_session=stale.garbage"},
    )

    assert status == "201 Created"
    assert created["match_id"]


def test_anonymous_pages_are_byte_identical_with_and_without_a_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The degradation wrapper only strips the session cookie: a cookie-less
    request gets exactly the same bytes either way."""
    monkeypatch.setenv("LEAGUE_SESSION_SECRET", "test-session-secret")
    with_secret = wiring.build_site_app({"LEAGUE_SESSION_SECRET": "test-session-secret"})
    without_secret = wiring.build_site_app({})
    for path in ("/", "/index.md", "/llms.txt"):
        secret_status, secret_headers, secret_body = call(with_secret, "GET", path)
        bare_status, bare_headers, bare_body = call(without_secret, "GET", path)
        assert bare_status == secret_status, path
        assert bare_body == secret_body, path
        assert bare_headers["Content-Type"] == secret_headers["Content-Type"], path


# --- the full arena over the fake AWS stores ---------------------------------


def _issue_agent(resource: FakeDynamoDBServiceResource, agent_name: str) -> str:
    token_store = DynamoDBTokenStore("league-tokens", resource=resource)
    issued = tokens.issue(
        token_store,
        agent_name=agent_name,
        model="claude-sonnet-5",
        provider="anthropic",
        owner_account_id=f"github:{agent_name.lower()}-owner",
    )
    return issued.token


def test_full_arena_round_trip_persists_through_the_dynamodb_stores() -> None:
    """Play a whole stub-duel through the composed app: every layer (auth,
    API, viewer, profiles, leaderboard) reads and writes the same
    DynamoDB-backed stores, and a second app built over the same tables — a
    fresh Lambda cold start — sees all of it."""
    resource = FakeDynamoDBServiceResource()
    app = wiring.build_site_app(FULL_ENV, dynamodb_resource=resource)
    creator_token = _issue_agent(resource, "probe-bot")
    rival_token = _issue_agent(resource, "rival-bot")

    status, _, created = call(
        app,
        "POST",
        "/api/v1/matches",
        body={
            "opponent": {
                "kind": "agent",
                "display_name": "Rival",
                "agent_name": "rival-bot",
                "model": "claude-sonnet-5",
                "provider": "anthropic",
            }
        },
        headers=bearer(creator_token),
    )
    assert status == "201 Created"
    match_id = created["match_id"]
    # the match landed in the fake DynamoDB table, not a process-local dict
    assert (f"MATCH#{match_id}", "METADATA") in resource.tables["league-matches"].items

    # alternate turns until the creator's score reaches the stub-duel target
    turn_tokens = [creator_token, rival_token]
    for turn in range(7):
        status, _, payload = call(
            app,
            "POST",
            f"/api/v1/matches/{match_id}/turns",
            body={"action": {"points": 3}},
            headers=bearer(turn_tokens[turn % 2]),
        )
        assert status == "200 OK", payload
    assert payload["status"] == "completed"

    # the completed match wrote one rating entry per identity to DynamoDB
    ratings_items = resource.tables["league-ratings"].items
    entry_items = [
        item for item in ratings_items.values() if item.get("entity_type") == "rating_entry"
    ]
    assert len(entry_items) == 2

    # a fresh app over the same tables — a new Lambda cold start — sees it all
    cold_start = wiring.build_site_app(FULL_ENV, dynamodb_resource=resource)
    status, _, reloaded = call(cold_start, "GET", f"/api/v1/matches/{match_id}")
    assert status == "200 OK"
    assert reloaded["status"] == "completed"

    status, _, board = call(cold_start, "GET", "/api/v1/leaderboard")
    assert status == "200 OK"
    names = {row["display_name"] for row in board["leaderboard"]}
    assert names == {"probe-bot", "Rival"}

    # viewer + profiles render from those same stores
    status, headers, _ = call(cold_start, "GET", f"/matches/{match_id}/watch")
    assert status == "200 OK"
    assert headers["Content-Type"].startswith("text/html")

    from league_site.matches import ParticipantKind
    from league_site.profiles.data import identity_slug
    from league_site.ratings import RatingIdentity

    creator = RatingIdentity(
        kind=ParticipantKind.AGENT,
        display_name="probe-bot",
        model="claude-sonnet-5",
        provider="anthropic",
    )
    status, headers, _ = call(cold_start, "GET", f"/profiles/{identity_slug(creator)}")
    assert status == "200 OK"
    assert headers["Content-Type"].startswith("text/html")


# --- Lambda game-CLI resolution (live-prod finding: FileNotFoundError 'league')


_GAME_CLI_ENV_KEYS = (
    "LEAGUE_CLI",
    "LEAGUE_CLI_MODULE",
    "AWS_LAMBDA_FUNCTION_NAME",
    "PYTHONPATH",
)


@pytest.fixture
def _game_cli_env():
    """Save/clear/restore the game-CLI env keys around a test.

    monkeypatch.delenv(raising=False) on an *absent* key registers no undo,
    so keys the code under test writes (LEAGUE_CLI_MODULE, PYTHONPATH) would
    leak into the rest of the suite — found as an order-dependent failure in
    test_web_http_grid's real-CLI test.
    """
    saved = {key: os.environ.get(key) for key in _GAME_CLI_ENV_KEYS}
    for key in _GAME_CLI_ENV_KEYS:
        os.environ.pop(key, None)
    yield
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def test_on_lambda_the_game_cli_resolves_as_a_module(_game_cli_env) -> None:
    """On Lambda (AWS_LAMBDA_FUNCTION_NAME set) with no explicit LEAGUE_CLI*,

    cold start must select module-mode resolution (sys.executable -m league)
    and make the artifact root importable for the child process — there is
    no `league` console script on Lambda's PATH.
    """
    os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "league-of-agents-prod-http"

    wiring.build_site_app(environ={})

    assert os.environ["LEAGUE_CLI_MODULE"] == "league"
    import league_site

    artifact_root = str(Path(league_site.__file__).resolve().parents[1])
    assert artifact_root in os.environ["PYTHONPATH"].split(os.pathsep)


def test_on_lambda_an_explicit_league_cli_is_left_alone(_game_cli_env) -> None:
    os.environ["AWS_LAMBDA_FUNCTION_NAME"] = "league-of-agents-prod-http"
    os.environ["LEAGUE_CLI"] = "/opt/custom/league"

    wiring.build_site_app(environ={})

    assert "LEAGUE_CLI_MODULE" not in os.environ
    assert os.environ["LEAGUE_CLI"] == "/opt/custom/league"


def test_off_lambda_the_game_cli_env_is_untouched(_game_cli_env) -> None:
    wiring.build_site_app(environ={})

    assert "LEAGUE_CLI_MODULE" not in os.environ
