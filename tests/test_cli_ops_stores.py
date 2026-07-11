"""Unit tests for :mod:`league_site.cli._commands._stores`.

Constructing a real ``boto3`` resource/client (``DynamoDBMatchStore``,
``S3MatchArchive``, ``boto3.client("s3")``) never makes a network call —
boto3 only reaches out on the first actual API call — so the "happy path"
here exercises the real adapters end to end (no fakes needed) rather than
duplicating the fakes ``tests/test_matches_aws.py``/
``tests/test_cleanup_handler.py`` already use for the request/response
cycle itself.

Client/resource *construction* does eagerly resolve a credential provider
chain, though, and a machine with an unusual local AWS config (e.g. an SSO
"login_session" profile needing an optional dependency this repo doesn't
install) can make that raise even though no request was ever sent. The
``_isolated_aws_config`` fixture below points ``AWS_CONFIG_FILE``/
``AWS_SHARED_CREDENTIALS_FILE`` at nothing so construction is deterministic
regardless of the host's own ``~/.aws`` setup.
"""

from __future__ import annotations

import sys

import pytest

from league_site.cli._commands import _stores
from league_site.cli._errors import EXIT_ENV_ERROR, EXIT_USER_ERROR, CliError
from league_site.matches.errors import MatchNotFoundError
from league_site.matches.store import InMemoryMatchStore


@pytest.fixture()
def _isolated_aws_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_CONFIG_FILE", "/dev/null")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/dev/null")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")


def test_resolve_match_store_defaults_to_ephemeral_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_stores.MATCHES_TABLE_ENV, raising=False)
    store, ephemeral = _stores.resolve_match_store()
    assert ephemeral is True
    assert isinstance(store, InMemoryMatchStore)


@pytest.mark.usefixtures("_isolated_aws_config")
def test_resolve_match_store_uses_dynamodb_when_table_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from league_site.matches.aws import DynamoDBMatchStore

    monkeypatch.setenv(_stores.MATCHES_TABLE_ENV, "league-matches")
    store, ephemeral = _stores.resolve_match_store()
    assert ephemeral is False
    assert isinstance(store, DynamoDBMatchStore)


def test_resolve_match_store_translates_missing_boto3_to_env_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import league_site.matches.aws as matches_aws

    monkeypatch.setenv(_stores.MATCHES_TABLE_ENV, "league-matches")
    monkeypatch.setattr(matches_aws, "boto3", None)
    with pytest.raises(CliError) as exc_info:
        _stores.resolve_match_store()
    assert exc_info.value.code == EXIT_ENV_ERROR
    assert "boto3" in exc_info.value.remediation


def test_resolve_archive_translates_missing_boto3_to_env_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import league_site.matches.aws as matches_aws

    monkeypatch.setattr(matches_aws, "boto3", None)
    with pytest.raises(CliError) as exc_info:
        _stores.resolve_archive("league-archive")
    assert exc_info.value.code == EXIT_ENV_ERROR
    assert "boto3" in exc_info.value.remediation


def test_resolve_archive_bucket_name_unset_raises_env_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(_stores.ARCHIVE_BUCKET_ENV, raising=False)
    with pytest.raises(CliError) as exc_info:
        _stores.resolve_archive_bucket_name()
    assert exc_info.value.code == EXIT_ENV_ERROR
    assert _stores.ARCHIVE_BUCKET_ENV in exc_info.value.message
    assert exc_info.value.remediation


def test_resolve_archive_bucket_name_set_returns_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_stores.ARCHIVE_BUCKET_ENV, "league-archive")
    assert _stores.resolve_archive_bucket_name() == "league-archive"


@pytest.mark.usefixtures("_isolated_aws_config")
def test_resolve_archive_returns_s3_match_archive() -> None:
    from league_site.matches.aws import S3MatchArchive

    archive = _stores.resolve_archive("league-archive")
    assert isinstance(archive, S3MatchArchive)


@pytest.mark.usefixtures("_isolated_aws_config")
def test_resolve_s3_client_returns_a_client() -> None:
    client = _stores.resolve_s3_client()
    assert client.meta.service_model.service_name == "s3"


def test_resolve_s3_client_translates_missing_boto3_to_env_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Setting sys.modules["boto3"] = None makes `import boto3` raise
    # ImportError (a documented CPython import-system mechanism) without
    # actually uninstalling the package for the rest of the suite.
    monkeypatch.setitem(sys.modules, "boto3", None)
    with pytest.raises(CliError) as exc_info:
        _stores.resolve_s3_client()
    assert exc_info.value.code == EXIT_ENV_ERROR
    assert "boto3" in exc_info.value.message
    assert "uv sync --extra aws" in exc_info.value.remediation


def test_guard_aws_call_passes_through_success() -> None:
    assert _stores.guard_aws_call("adding", lambda a, b: a + b, 1, 2) == 3


def test_guard_aws_call_wraps_generic_exceptions_as_env_error() -> None:
    def _boom() -> None:
        raise RuntimeError("no route to host")

    with pytest.raises(CliError) as exc_info:
        _stores.guard_aws_call("talking to DynamoDB", _boom)
    assert exc_info.value.code == EXIT_ENV_ERROR
    assert "talking to DynamoDB" in exc_info.value.message
    assert "no route to host" in exc_info.value.message


def test_guard_aws_call_reraises_cli_error_unwrapped() -> None:
    original = CliError(code=EXIT_USER_ERROR, message="bad input", remediation="fix it")

    def _raise_cli_error() -> None:
        raise original

    with pytest.raises(CliError) as exc_info:
        _stores.guard_aws_call("doing a thing", _raise_cli_error)
    assert exc_info.value is original


def test_guard_aws_call_reraises_match_errors_unwrapped() -> None:
    """A missing match is a *user* error (exit 1), not an AWS/env error (exit 2)."""

    def _raise_not_found() -> None:
        raise MatchNotFoundError("m1")

    with pytest.raises(MatchNotFoundError):
        _stores.guard_aws_call("loading match 'm1'", _raise_not_found)


def test_ephemeral_note_mentions_the_env_var() -> None:
    assert _stores.MATCHES_TABLE_ENV in _stores.EPHEMERAL_NOTE


# --- token / account store resolution (t4) -----------------------------------


def test_resolve_token_store_defaults_to_ephemeral_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from league_site.auth.token_store import InMemoryTokenStore

    monkeypatch.delenv(_stores.TOKENS_TABLE_ENV, raising=False)
    store, ephemeral = _stores.resolve_token_store()
    assert ephemeral is True
    assert isinstance(store, InMemoryTokenStore)


@pytest.mark.usefixtures("_isolated_aws_config")
def test_resolve_token_store_uses_dynamodb_when_table_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from league_site.auth.aws_tokens import DynamoDBTokenStore

    monkeypatch.setenv(_stores.TOKENS_TABLE_ENV, "league-agent-tokens")
    store, ephemeral = _stores.resolve_token_store()
    assert ephemeral is False
    assert isinstance(store, DynamoDBTokenStore)


def test_resolve_token_store_translates_missing_boto3_to_env_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import league_site.auth.aws_tokens as aws_tokens

    monkeypatch.setenv(_stores.TOKENS_TABLE_ENV, "league-agent-tokens")
    monkeypatch.setattr(aws_tokens, "boto3", None)
    with pytest.raises(CliError) as exc_info:
        _stores.resolve_token_store()
    assert exc_info.value.code == EXIT_ENV_ERROR
    assert "boto3" in exc_info.value.remediation


def test_resolve_account_store_defaults_to_ephemeral_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from league_site.accounts.store import InMemoryAccountStore

    monkeypatch.delenv(_stores.TOKENS_TABLE_ENV, raising=False)
    store, ephemeral = _stores.resolve_account_store()
    assert ephemeral is True
    assert isinstance(store, InMemoryAccountStore)


@pytest.mark.usefixtures("_isolated_aws_config")
def test_resolve_account_store_uses_dynamodb_when_table_env_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from league_site.accounts.aws import DynamoDBAccountStore

    monkeypatch.setenv(_stores.TOKENS_TABLE_ENV, "league-agent-tokens")
    store, ephemeral = _stores.resolve_account_store()
    assert ephemeral is False
    assert isinstance(store, DynamoDBAccountStore)


def test_resolve_account_store_translates_missing_boto3_to_env_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import league_site.accounts.aws as accounts_aws

    monkeypatch.setenv(_stores.TOKENS_TABLE_ENV, "league-agent-tokens")
    monkeypatch.setattr(accounts_aws, "boto3", None)
    with pytest.raises(CliError) as exc_info:
        _stores.resolve_account_store()
    assert exc_info.value.code == EXIT_ENV_ERROR
    assert "boto3" in exc_info.value.remediation


def test_tokens_ephemeral_note_mentions_the_env_var() -> None:
    assert _stores.TOKENS_TABLE_ENV in _stores.TOKENS_EPHEMERAL_NOTE
