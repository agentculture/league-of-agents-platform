"""Shared operator-store resolution for the ``ops``/``match``/``site`` noun groups.

Every verb added by this module's siblings needs the same answer to "which
``MatchStore``": the real DynamoDB-backed one when the platform is deployed
(``MATCHES_TABLE_NAME`` set in the environment — the same variable
:func:`league_site.aws_lambda.cleanup.handler` reads, see that module's
docstring), or a fresh, process-local
:class:`~league_site.matches.store.InMemoryMatchStore` otherwise. Centralised
here so ``ops telemetry``, ``ops capacity``, ``ops cleanup``, and
``match list|show|archive`` resolve it identically instead of five
copy-pasted env checks.

The in-memory fallback is unambiguously ephemeral: a fresh CLI invocation is
a fresh process, so state never survives between two ``league-site match
...`` calls unless ``MATCHES_TABLE_NAME`` points at a real table. Every
command that falls back to it surfaces that fact via :data:`EPHEMERAL_NOTE`
rather than staying silent about it — an operator who forgot to set the env
var should never mistake an empty in-memory store for "no matches exist".

Every function here is a thin, individually-monkeypatchable seam
(``_stores.resolve_match_store``, etc.) — CLI command tests patch these
directly instead of the real boto3-backed adapters, the same "inject every
dependency" discipline :mod:`league_site.aws_lambda.cleanup` documents for
:func:`~league_site.aws_lambda.cleanup.run_cleanup`.
"""

from __future__ import annotations

import os
from typing import Any, Callable, TypeVar

from league_site.accounts.store import AccountStore, InMemoryAccountStore
from league_site.auth.token_store import InMemoryTokenStore, TokenStore
from league_site.cli._errors import EXIT_ENV_ERROR, CliError
from league_site.matches.errors import MatchError
from league_site.matches.store import InMemoryMatchStore, MatchStore

#: Same env var name :mod:`league_site.aws_lambda.cleanup` and
#: ``infra/template.yaml`` use for the deployed matches table.
MATCHES_TABLE_ENV = "MATCHES_TABLE_NAME"

#: Same env var name the cleanup Lambda reads for its archive bucket.
ARCHIVE_BUCKET_ENV = "ARCHIVE_BUCKET_NAME"

#: Same env var name :mod:`league_site.aws_lambda.wiring` uses for the deployed
#: agent-tokens table — which the account store shares (accounts ride the
#: tokens table, see :mod:`league_site.accounts.aws`), so a single variable
#: resolves both the token and account operator stores.
TOKENS_TABLE_ENV = "TOKENS_TABLE_NAME"

#: Surfaced (as a diagnostic in text mode, a ``note`` field in JSON mode)
#: whenever a command falls back to the ephemeral in-memory store.
EPHEMERAL_NOTE = (
    f"{MATCHES_TABLE_ENV} is not set — using an ephemeral in-memory match store; "
    "state does not persist across CLI invocations"
)

#: The token/account counterpart of :data:`EPHEMERAL_NOTE`, surfaced by
#: ``tokens``/``accounts`` verbs when they fall back to the in-memory store —
#: an operator who forgot to set ``TOKENS_TABLE_NAME`` must never mistake an
#: empty in-memory store for "no tokens/accounts exist".
TOKENS_EPHEMERAL_NOTE = (
    f"{TOKENS_TABLE_ENV} is not set — using an ephemeral in-memory token/account store; "
    "state does not persist across CLI invocations"
)

#: Remediation hint surfaced whenever a boto3-backed adapter can't be built —
#: the import failed or its own boto3 guard fired. Collapsed to one constant
#: since the same instruction repeats across every store/archive constructor
#: below.
_BOTO3_INSTALL_HINT = "install boto3 with `uv sync --extra aws`"

_T = TypeVar("_T")


def resolve_match_store() -> tuple[MatchStore, bool]:
    """Return ``(store, ephemeral)`` per the env-driven rule in the module docstring."""
    table_name = os.environ.get(MATCHES_TABLE_ENV)
    if not table_name:
        return InMemoryMatchStore(), True
    return _dynamodb_store(table_name), False


def resolve_token_store() -> tuple[TokenStore, bool]:
    """Return ``(store, ephemeral)`` for the agent-token store, env-driven like matches.

    Real :class:`~league_site.auth.aws_tokens.DynamoDBTokenStore` when
    :data:`TOKENS_TABLE_ENV` is set (the same table the serving Lambda
    verifies against), else a fresh ephemeral
    :class:`~league_site.auth.token_store.InMemoryTokenStore`. Backs
    ``league-site tokens list|block|unblock`` — the operator kill-switch for
    agent credentials.
    """
    table_name = os.environ.get(TOKENS_TABLE_ENV)
    if not table_name:
        return InMemoryTokenStore(), True
    return _dynamodb_token_store(table_name), False


def resolve_account_store() -> tuple[AccountStore, bool]:
    """Return ``(store, ephemeral)`` for the account store, env-driven like matches.

    Real :class:`~league_site.accounts.aws.DynamoDBAccountStore` when
    :data:`TOKENS_TABLE_ENV` is set — accounts share the physical agent-tokens
    table, so there is no separate ``ACCOUNTS_TABLE_NAME`` (see
    :mod:`league_site.accounts.aws`) — else a fresh ephemeral
    :class:`~league_site.accounts.store.InMemoryAccountStore`. Backs
    ``league-site accounts list|block|unblock``.
    """
    table_name = os.environ.get(TOKENS_TABLE_ENV)
    if not table_name:
        return InMemoryAccountStore(), True
    return _dynamodb_account_store(table_name), False


def _dynamodb_store(table_name: str) -> MatchStore:
    try:
        from league_site.matches.aws import DynamoDBMatchStore
    except ImportError as exc:  # pragma: no cover - import machinery, not a runtime path
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot import the DynamoDB match store adapter: {exc}",
            remediation=_BOTO3_INSTALL_HINT,
        ) from exc
    try:
        return DynamoDBMatchStore(table_name)
    except RuntimeError as exc:
        # DynamoDBMatchStore's own _require_boto3() guard fires when boto3
        # isn't installed — translate it into the CLI's structured contract.
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=str(exc),
            remediation=_BOTO3_INSTALL_HINT,
        ) from exc


def _dynamodb_token_store(table_name: str) -> TokenStore:
    try:
        from league_site.auth.aws_tokens import DynamoDBTokenStore
    except ImportError as exc:  # pragma: no cover - import machinery, not a runtime path
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot import the DynamoDB token store adapter: {exc}",
            remediation=_BOTO3_INSTALL_HINT,
        ) from exc
    try:
        return DynamoDBTokenStore(table_name)
    except RuntimeError as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=str(exc),
            remediation=_BOTO3_INSTALL_HINT,
        ) from exc


def _dynamodb_account_store(table_name: str) -> AccountStore:
    try:
        from league_site.accounts.aws import DynamoDBAccountStore
    except ImportError as exc:  # pragma: no cover - import machinery, not a runtime path
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot import the DynamoDB account store adapter: {exc}",
            remediation=_BOTO3_INSTALL_HINT,
        ) from exc
    try:
        return DynamoDBAccountStore(table_name)
    except RuntimeError as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=str(exc),
            remediation=_BOTO3_INSTALL_HINT,
        ) from exc


def resolve_archive_bucket_name() -> str:
    """Return ``ARCHIVE_BUCKET_NAME``, or raise a clear :class:`CliError` if unset."""
    bucket_name = os.environ.get(ARCHIVE_BUCKET_ENV)
    if not bucket_name:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"{ARCHIVE_BUCKET_ENV} is not set",
            remediation=(
                f"export {ARCHIVE_BUCKET_ENV}=<bucket-name> (see infra/template.yaml's "
                "ArchiveBucket resource) before running --apply"
            ),
        )
    return bucket_name


def resolve_archive(bucket_name: str) -> Any:
    """Return a :class:`~league_site.matches.aws.S3MatchArchive` for ``bucket_name``."""
    try:
        from league_site.matches.aws import S3MatchArchive
    except ImportError as exc:  # pragma: no cover - import machinery, not a runtime path
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"cannot import the S3 archive adapter: {exc}",
            remediation=_BOTO3_INSTALL_HINT,
        ) from exc
    try:
        return S3MatchArchive(bucket_name)
    except RuntimeError as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=str(exc),
            remediation=_BOTO3_INSTALL_HINT,
        ) from exc


def resolve_s3_client() -> Any:
    """Return a raw ``boto3`` S3 client, used for the archive-bucket listing pass."""
    try:
        import boto3
    except ImportError as exc:
        raise CliError(
            code=EXIT_ENV_ERROR,
            message="boto3 is required to talk to the S3 archive bucket",
            remediation="install it with `uv sync --extra aws`",
        ) from exc
    return boto3.client("s3")


def guard_aws_call(context: str, fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
    """Run ``fn(*args, **kwargs)``, wrapping any AWS failure into a :class:`CliError`.

    boto3/botocore failures (missing credentials, network errors, an
    unreachable table/bucket) surface as plain exceptions from a real
    ``DynamoDBMatchStore``/``S3MatchArchive`` call, not a ``CliError`` — this
    narrows them to the documented env-error contract (exit 2, a remediation
    hint) instead of falling through to ``_dispatch``'s generic "unexpected:
    ..." catch-all, which carries no AWS-specific guidance.

    :class:`~league_site.matches.errors.MatchError` (e.g. ``match not
    found``) is a *user* error, not an environment one — callers should
    catch it themselves and raise a ``CliError`` with
    :data:`~league_site.cli._errors.EXIT_USER_ERROR`, so it is deliberately
    let through unwrapped here rather than mapped to exit 2.
    """
    try:
        return fn(*args, **kwargs)
    except (CliError, MatchError):
        raise
    except Exception as exc:  # noqa: BLE001 - narrows arbitrary boto3/botocore errors
        raise CliError(
            code=EXIT_ENV_ERROR,
            message=f"{context}: {exc.__class__.__name__}: {exc}",
            remediation=(
                "check AWS credentials/region, or unset "
                f"{MATCHES_TABLE_ENV}/{ARCHIVE_BUCKET_ENV} to use the ephemeral in-memory store"
            ),
        ) from exc
