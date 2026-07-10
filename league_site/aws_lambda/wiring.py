"""Env-driven store wiring for the serving Lambda's cold start.

:func:`build_site_app` is the one place the deployed platform decides which
stores back :func:`league_site.web.http.site_app`:

* **Deployed** (the ``*_TABLE_NAME`` variables present, set on the Lambda by
  ``infra/template.yaml``): each named table becomes its DynamoDB-backed
  adapter — :class:`~league_site.matches.aws.DynamoDBMatchStore`,
  :class:`~league_site.auth.aws_tokens.DynamoDBTokenStore`,
  :class:`~league_site.ratings.aws.DynamoDBRatingLedgerStore` — all sharing
  one lazily-built ``boto3`` DynamoDB resource, so state persists across
  invocations, cold starts, and deploys.
* **Local dev / tests** (variables absent): no store kwargs are passed at
  all, so ``site_app()`` composes exactly its in-memory defaults —
  byte-identical to serving ``site_app()`` bare.

Each variable is honoured independently (a partially-configured environment
wires exactly the stores it names), read once at construction — i.e. once
per Lambda cold start — not per request.

Env contract (names must match ``infra/template.yaml`` exactly):

* :data:`MATCHES_TABLE_ENV` (``MATCHES_TABLE_NAME``) — matches table.
* :data:`TOKENS_TABLE_ENV` (``TOKENS_TABLE_NAME``) — agent-tokens table.
* :data:`RATINGS_TABLE_ENV` (``RATINGS_TABLE_NAME``) — rating-ledger table.
* :data:`ARCHIVE_BUCKET_ENV` (``ARCHIVE_BUCKET_NAME``) — S3 archive bucket.
  Listed here because it is part of the same deploy contract, but *not*
  consumed by the serving app: archiving belongs to the cleanup Lambda
  (:mod:`league_site.aws_lambda.cleanup`) and the operator CLI
  (:mod:`league_site.cli._commands._stores`), which read the same name.
* :data:`SESSION_SECRET_ENV` (``LEAGUE_SESSION_SECRET``) — optional. The
  launch is pre-OAuth: when the secret is unset, cookie sessions degrade
  gracefully instead of failing — see :func:`_without_session_cookies`.

``boto3`` is imported *only* inside the branch that actually builds AWS
stores (see :func:`_dynamodb_resource`), never at module import time — this
module sits on :mod:`league_site.aws_lambda.handler`'s import path, and an
env-less cold start (local dev, the test suite) must not pay the AWS SDK
import cost. Tests inject ``dynamodb_resource`` to keep even the env-set
path fake-backed.
"""

from __future__ import annotations

import os
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable, Mapping

from league_site.auth.sessions import SESSION_SECRET_ENV
from league_site.auth.wsgi import SESSION_COOKIE_NAME
from league_site.web.http import site_app

WSGIApp = Callable[[dict[str, Any], Callable[..., Any]], list[bytes]]

#: Same names :mod:`league_site.aws_lambda.cleanup` and
#: :mod:`league_site.cli._commands._stores` read — one deploy contract.
MATCHES_TABLE_ENV = "MATCHES_TABLE_NAME"
ARCHIVE_BUCKET_ENV = "ARCHIVE_BUCKET_NAME"
TOKENS_TABLE_ENV = "TOKENS_TABLE_NAME"
RATINGS_TABLE_ENV = "RATINGS_TABLE_NAME"


def _dynamodb_resource() -> Any:
    """Build the default ``boto3`` DynamoDB resource, with a clear error without the extra."""
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover - deploy misconfiguration path
        raise RuntimeError(
            "boto3 is required to build the AWS-backed stores named by the "
            "environment; install it with `uv sync --extra aws`"
        ) from exc
    return boto3.resource("dynamodb")


def _without_session_cookies(inner: WSGIApp) -> WSGIApp:
    """Wrap *inner* so the ``league_session`` cookie never reaches it.

    The pre-OAuth degradation path: :func:`league_site.auth.wsgi.with_auth`
    verifies any presented session cookie via
    :mod:`league_site.auth.sessions`, which *raises*
    :class:`~league_site.auth._signing.MissingSecretError` when
    ``LEAGUE_SESSION_SECRET`` is unset — turning one stale or hostile cookie
    into a 500 on every page. With no secret there is no way any session
    cookie could verify anyway, so stripping exactly that cookie (other
    cookies pass through untouched) makes every such request cleanly
    anonymous instead: pages, spectating, and the bearer-token API all keep
    working. Requests without a ``league_session`` cookie are forwarded with
    their environ untouched — byte-identical behavior.
    """

    def application(environ: dict[str, Any], start_response: Any) -> list[bytes]:
        header = environ.get("HTTP_COOKIE")
        if header:
            cookie: SimpleCookie = SimpleCookie()
            cookie.load(header)
            if SESSION_COOKIE_NAME in cookie:
                del cookie[SESSION_COOKIE_NAME]
                environ = dict(environ)
                remaining = "; ".join(
                    f"{name}={morsel.coded_value}" for name, morsel in cookie.items()
                )
                if remaining:
                    environ["HTTP_COOKIE"] = remaining
                else:
                    del environ["HTTP_COOKIE"]
        return inner(environ, start_response)

    return application


def _configure_game_cli_for_lambda() -> None:
    """On Lambda, default the game CLI to module-mode resolution.

    The build (repo-root ``Makefile``) ``pip install --target``\\ s the
    ``league-of-agents`` package into the artifact, so there is no
    ``league`` console script anywhere on Lambda's ``PATH`` — the runner's
    default resolution raised ``FileNotFoundError: 'league'`` on the first
    production match (live finding). Module mode
    (``sys.executable -m league`` — see
    :data:`league_site.game.runner.LEAGUE_CLI_MODULE_ENV_VAR`) needs two
    things, both set here once per cold start:

    * ``LEAGUE_CLI_MODULE=league`` — unless the operator already pinned an
      explicit ``LEAGUE_CLI`` or module override, which always wins.
    * the artifact root (the directory holding both ``league_site`` and the
      installed ``league`` package) on ``PYTHONPATH``, so the *child*
      interpreter can import the module — Lambda's own ``sys.path`` entry
      for ``/var/task`` is runtime bootstrap state the subprocess never
      inherits.

    Off Lambda (no ``AWS_LAMBDA_FUNCTION_NAME``) this is a no-op: local dev
    and the test suite keep resolving the CLI from ``PATH``/venv exactly as
    before.
    """
    if "AWS_LAMBDA_FUNCTION_NAME" not in os.environ:
        return
    from league_site.game.runner import LEAGUE_CLI_ENV_VAR, LEAGUE_CLI_MODULE_ENV_VAR

    if os.environ.get(LEAGUE_CLI_ENV_VAR) or os.environ.get(LEAGUE_CLI_MODULE_ENV_VAR):
        return
    os.environ[LEAGUE_CLI_MODULE_ENV_VAR] = "league"
    artifact_root = str(Path(__file__).resolve().parents[2])
    existing = os.environ.get("PYTHONPATH", "")
    if artifact_root not in existing.split(os.pathsep):
        os.environ["PYTHONPATH"] = (
            f"{artifact_root}{os.pathsep}{existing}" if existing else artifact_root
        )


def build_site_app(
    environ: Mapping[str, str] | None = None, *, dynamodb_resource: Any | None = None
) -> WSGIApp:
    """Compose :func:`~league_site.web.http.site_app` over env-selected stores.

    *environ* defaults to ``os.environ``; tests pass a plain dict to pin the
    decision inputs. *dynamodb_resource* injects a pre-built (or fake)
    ``boto3`` DynamoDB resource shared by every store built here; when
    ``None`` and at least one table variable is set, one real resource is
    built lazily via :func:`_dynamodb_resource` (constructing it performs no
    network I/O — only store *operations* do).

    With no table variables set this passes **no** store kwargs, so the
    result serves exactly what a bare ``site_app()`` serves today. Missing
    ``LEAGUE_SESSION_SECRET`` additionally wraps the app with
    :func:`_without_session_cookies` (the pre-OAuth graceful-degradation
    path); with the secret present the composed app is returned as-is.
    """
    env = os.environ if environ is None else environ
    _configure_game_cli_for_lambda()

    store_kwargs: dict[str, Any] = {}
    matches_table = env.get(MATCHES_TABLE_ENV)
    tokens_table = env.get(TOKENS_TABLE_ENV)
    ratings_table = env.get(RATINGS_TABLE_ENV)

    if (matches_table or tokens_table or ratings_table) and dynamodb_resource is None:
        dynamodb_resource = _dynamodb_resource()

    if matches_table:
        from league_site.matches.aws import DynamoDBMatchStore

        store_kwargs["match_store"] = DynamoDBMatchStore(matches_table, resource=dynamodb_resource)
    if tokens_table:
        from league_site.auth.aws_tokens import DynamoDBTokenStore

        store_kwargs["token_store"] = DynamoDBTokenStore(tokens_table, resource=dynamodb_resource)
    if ratings_table:
        from league_site.ratings.aws import DynamoDBRatingLedgerStore

        store_kwargs["ledger_store"] = DynamoDBRatingLedgerStore(
            ratings_table, resource=dynamodb_resource
        )

    app = site_app(**store_kwargs)
    if not env.get(SESSION_SECRET_ENV):
        app = _without_session_cookies(app)
    return app
