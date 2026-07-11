"""Human account domain: durable identity for GitHub sign-in.

An :class:`AccountRecord` is the accountability unit every agent token will
be anchored to (a later task adds ``owner_account_id`` to
:mod:`league_site.auth.token_store`) — the thing an operator can block, and
one day bill. This package holds the record shape, the
:class:`AccountStore` interface, and :class:`InMemoryAccountStore`, the
reference implementation used by default in local dev and the test suite.

:mod:`league_site.accounts.aws` adds :class:`~league_site.accounts.aws.
DynamoDBAccountStore` — imported separately (not re-exported here) since it
is the only module in this package that touches ``boto3``, mirroring
:mod:`league_site.auth.aws_tokens`'s and :mod:`league_site.ratings.aws`'s
own guarded-import convention. See that module's docstring for the
DynamoDB single-table item shape and why accounts share the existing
agent-tokens table rather than a dedicated one.
"""

from __future__ import annotations

from league_site.accounts.store import (
    AccountNotFoundError,
    AccountRecord,
    AccountStore,
    InMemoryAccountStore,
    account_id_for,
)

__all__ = [
    "AccountNotFoundError",
    "AccountRecord",
    "AccountStore",
    "InMemoryAccountStore",
    "account_id_for",
]
