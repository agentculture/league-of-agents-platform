"""Tests for ``infra/template.yaml`` — the AWS SAM deploy stack.

These tests do not touch AWS: they parse the template as plain YAML and
assert its shape (resource list, types, and the Budget's 20 USD ceiling).
``sam validate`` is invoked only when the ``sam`` CLI is present on the
machine running the tests; see :func:`test_sam_validate_if_sam_cli_present`
for why deploy-from-scratch / no-op-redeploy behavior itself is *not*, and
cannot be, proven here — that is documented in ``docs/deploy.md`` and left
to the live-launch-checklist task.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from league_site.capacity.config import CapacityConfig

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "infra" / "template.yaml"

_EXPECTED_RESOURCES: dict[str, str] = {
    "HttpApi": "AWS::Serverless::HttpApi",
    "HttpHandlerFunction": "AWS::Serverless::Function",
    "HttpHandlerFunctionLogGroup": "AWS::Logs::LogGroup",
    "CleanupFunction": "AWS::Serverless::Function",
    "CleanupFunctionLogGroup": "AWS::Logs::LogGroup",
    "MatchesTable": "AWS::DynamoDB::Table",
    "TokensTable": "AWS::DynamoDB::Table",
    "RatingsTable": "AWS::DynamoDB::Table",
    "ArchiveBucket": "AWS::S3::Bucket",
    "MonthlyBudget": "AWS::Budgets::Budget",
}


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    """Parse ``infra/template.yaml`` with a plain YAML loader.

    The template is written entirely with long-form intrinsic functions
    (``Fn::Sub``/``Ref``/``Fn::GetAtt`` instead of the ``!Sub``/``!Ref``/
    ``!GetAtt`` short-hand tags) specifically so this parses with
    ``yaml.safe_load`` and no CloudFormation-tag-aware loader is needed.
    """
    with _TEMPLATE_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_template_file_exists() -> None:
    assert _TEMPLATE_PATH.is_file()


def test_template_parses_as_valid_yaml(template: dict[str, Any]) -> None:
    assert isinstance(template, dict)
    assert template["AWSTemplateFormatVersion"] == "2010-09-09"
    assert template["Transform"] == "AWS::Serverless-2016-10-31"


def test_template_declares_exactly_the_expected_resources(template: dict[str, Any]) -> None:
    resources = template["Resources"]
    assert set(resources.keys()) == set(_EXPECTED_RESOURCES.keys())
    for logical_id, expected_type in _EXPECTED_RESOURCES.items():
        assert resources[logical_id]["Type"] == expected_type


def test_lambda_is_python312_arm64() -> None:
    """arm64 + python3.12 are set once in Globals.Function and apply to the one function."""
    with _TEMPLATE_PATH.open(encoding="utf-8") as handle:
        template = yaml.safe_load(handle)
    function_defaults = template["Globals"]["Function"]
    assert function_defaults["Runtime"] == "python3.12"
    assert function_defaults["Architectures"] == ["arm64"]


def test_lambda_handler_points_at_the_new_handler_module(template: dict[str, Any]) -> None:
    function = template["Resources"]["HttpHandlerFunction"]["Properties"]
    assert function["Handler"] == "league_site.aws_lambda.handler.handler"


def test_dynamodb_table_uses_pk_sk_single_table_design(template: dict[str, Any]) -> None:
    """Matches league_site/matches/serialization.py's documented PK/SK key scheme.

    ``AttributeDefinitions`` is checked as a superset, not an exact set: it is
    table-wide (DynamoDB requires every key attribute used by the base table
    *or* any GSI declared exactly once, in this one list — see
    ``test_matches_table_has_by_status_updated_gsi``), while the base table's
    own primary key — the thing this test is about — is ``KeySchema``.
    """
    table = template["Resources"]["MatchesTable"]["Properties"]
    attribute_names = {attr["AttributeName"] for attr in table["AttributeDefinitions"]}
    assert {"PK", "SK"} <= attribute_names
    key_schema = {entry["AttributeName"]: entry["KeyType"] for entry in table["KeySchema"]}
    assert key_schema == {"PK": "HASH", "SK": "RANGE"}
    assert table["BillingMode"] == "PAY_PER_REQUEST"


def test_matches_table_name_and_billing_mode_are_unchanged(template: dict[str, Any]) -> None:
    """The stack is live; TableName/BillingMode must never change (that forces replacement).

    Only additive changes (like the GSI below) are allowed on this table.
    """
    table = template["Resources"]["MatchesTable"]["Properties"]
    assert table["TableName"] == {"Fn::Sub": "league-of-agents-${StageName}-matches"}
    assert table["BillingMode"] == "PAY_PER_REQUEST"


def test_matches_table_has_by_status_updated_gsi(template: dict[str, Any]) -> None:
    """The handler-wiring task's ``list_ids`` target: an exact-named GSI, not a scan.

    See league_site/matches/aws.py's ``DynamoDBMatchStore.list_ids`` docstring
    for the "suggested access pattern" this GSI finally provisions.
    """
    table = template["Resources"]["MatchesTable"]["Properties"]
    attribute_types = {
        attr["AttributeName"]: attr["AttributeType"] for attr in table["AttributeDefinitions"]
    }
    assert attribute_types["status"] == "S"
    assert attribute_types["updated_at"] == "S"
    # PK/SK must still be there too — this is an addition, not a replacement.
    assert attribute_types["PK"] == "S"
    assert attribute_types["SK"] == "S"

    indexes = table["GlobalSecondaryIndexes"]
    assert len(indexes) == 1
    index = indexes[0]
    assert index["IndexName"] == "by-status-updated"
    key_schema = {entry["AttributeName"]: entry["KeyType"] for entry in index["KeySchema"]}
    assert key_schema == {"status": "HASH", "updated_at": "RANGE"}
    assert index["Projection"]["ProjectionType"] == "ALL"


def test_tokens_table_uses_the_repo_pk_sk_convention(template: dict[str, Any]) -> None:
    """league_site/auth/aws_tokens.py reads/writes PK="TOKEN#<hash>", SK="METADATA" —
    the same single-table PK/SK convention as the matches table. The table's key
    schema must match the store's item layout or every get_item raises
    ValidationException (found live: the first prod deploy keyed this table
    token_hash and reads failed)."""
    table = template["Resources"]["TokensTable"]["Properties"]
    attribute_types = {
        attr["AttributeName"]: attr["AttributeType"] for attr in table["AttributeDefinitions"]
    }
    assert attribute_types == {"PK": "S", "SK": "S"}
    key_schema = {entry["AttributeName"]: entry["KeyType"] for entry in table["KeySchema"]}
    assert key_schema == {"PK": "HASH", "SK": "RANGE"}
    assert table["BillingMode"] == "PAY_PER_REQUEST"
    assert table["TableName"] == {"Fn::Sub": "league-of-agents-${StageName}-agent-tokens"}


def test_ratings_table_uses_the_repo_pk_sk_convention(template: dict[str, Any]) -> None:
    """league_site/ratings/aws.py writes PK="LEDGER#<identity>"/"IDENTITIES",
    SK="ENTRY#<seq>"/"ORDER#<n>" — same PK/SK convention as the matches table; see
    league_site/ratings/ledger.py's RatingLedgerStore/RatingEntry docstrings.
    """
    table = template["Resources"]["RatingsTable"]["Properties"]
    attribute_types = {
        attr["AttributeName"]: attr["AttributeType"] for attr in table["AttributeDefinitions"]
    }
    assert attribute_types == {"PK": "S", "SK": "S"}
    key_schema = {entry["AttributeName"]: entry["KeyType"] for entry in table["KeySchema"]}
    assert key_schema == {"PK": "HASH", "SK": "RANGE"}
    assert table["BillingMode"] == "PAY_PER_REQUEST"
    assert table["TableName"] == {"Fn::Sub": "league-of-agents-${StageName}-rating-ledger"}


def test_session_secret_parameter_is_a_noecho_string_defaulting_empty(
    template: dict[str, Any],
) -> None:
    """Empty default means sessions stay disabled pre-OAuth (see league_site/auth/sessions.py)."""
    parameter = template["Parameters"]["SessionSecretValue"]
    assert parameter["Type"] == "String"
    assert parameter["NoEcho"] is True
    assert parameter["Default"] == ""


def test_monthly_budget_parameter_defaults_to_20_usd(template: dict[str, Any]) -> None:
    assert template["Parameters"]["MonthlyBudgetUsd"]["Default"] == 20


def test_budget_resource_is_wired_to_the_20_usd_parameter(template: dict[str, Any]) -> None:
    budget = template["Resources"]["MonthlyBudget"]["Properties"]["Budget"]
    assert budget["BudgetLimit"]["Amount"] == {"Ref": "MonthlyBudgetUsd"}
    assert budget["BudgetLimit"]["Unit"] == "USD"
    assert budget["BudgetType"] == "COST"
    assert budget["TimeUnit"] == "MONTHLY"


def test_log_group_has_bounded_retention(template: dict[str, Any]) -> None:
    log_group = template["Resources"]["HttpHandlerFunctionLogGroup"]["Properties"]
    assert isinstance(log_group["RetentionInDays"], int)
    assert log_group["RetentionInDays"] > 0


def test_archive_bucket_blocks_public_access(template: dict[str, Any]) -> None:
    bucket = template["Resources"]["ArchiveBucket"]["Properties"]
    block = bucket["PublicAccessBlockConfiguration"]
    assert all(block.values())


def test_cleanup_function_points_at_the_cleanup_handler_module(template: dict[str, Any]) -> None:
    function = template["Resources"]["CleanupFunction"]["Properties"]
    assert function["Handler"] == "league_site.aws_lambda.cleanup.handler"


def test_cleanup_function_is_triggered_by_a_daily_schedule_event(template: dict[str, Any]) -> None:
    events = template["Resources"]["CleanupFunction"]["Properties"]["Events"]
    schedule_events = [event for event in events.values() if event["Type"] == "Schedule"]
    assert len(schedule_events) == 1
    schedule = schedule_events[0]["Properties"]
    assert schedule["Schedule"] == "rate(1 day)"
    assert schedule["Enabled"] is True


def test_cleanup_function_has_dynamodb_and_s3_policies(template: dict[str, Any]) -> None:
    policies = template["Resources"]["CleanupFunction"]["Properties"]["Policies"]
    policy_keys = {key for policy in policies for key in policy}
    assert "DynamoDBCrudPolicy" in policy_keys
    assert "S3CrudPolicy" in policy_keys


def test_cleanup_function_keeps_its_existing_matches_and_archive_grants(
    template: dict[str, Any],
) -> None:
    """Additive only: cleanup must still CRUD MatchesTable/ArchiveBucket exactly as before."""
    policies = template["Resources"]["CleanupFunction"]["Properties"]["Policies"]
    crud_table_refs = [
        policy["DynamoDBCrudPolicy"]["TableName"]
        for policy in policies
        if "DynamoDBCrudPolicy" in policy
    ]
    assert {"Ref": "MatchesTable"} in crud_table_refs
    s3_bucket_refs = [
        policy["S3CrudPolicy"]["BucketName"] for policy in policies if "S3CrudPolicy" in policy
    ]
    assert {"Ref": "ArchiveBucket"} in s3_bucket_refs


def _inline_statements(policies: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten every ``Statement`` entry from raw (non-template) IAM policy documents."""
    statements: list[dict[str, Any]] = []
    for policy in policies:
        if "Statement" in policy:
            statements.extend(policy["Statement"])
    return statements


def _grants_query_on_status_updated_index(policies: list[dict[str, Any]]) -> bool:
    for statement in _inline_statements(policies):
        actions = statement["Action"]
        actions = [actions] if isinstance(actions, str) else actions
        if "dynamodb:Query" not in actions:
            continue
        resource = statement["Resource"]
        resource_str = resource.get("Fn::Sub", "") if isinstance(resource, dict) else str(resource)
        if "MatchesTable" in resource_str and "by-status-updated" in resource_str:
            return True
    return False


def test_cleanup_function_grants_query_on_the_status_updated_gsi(template: dict[str, Any]) -> None:
    """Cleanup keeps what it has (above) plus least-privilege Query on the new GSI."""
    policies = template["Resources"]["CleanupFunction"]["Properties"]["Policies"]
    assert _grants_query_on_status_updated_index(policies)


def test_cleanup_function_env_vars_reference_the_matches_table_and_archive_bucket(
    template: dict[str, Any],
) -> None:
    env = template["Resources"]["CleanupFunction"]["Properties"]["Environment"]["Variables"]
    assert env["MATCHES_TABLE_NAME"] == {"Ref": "MatchesTable"}
    assert env["ARCHIVE_BUCKET_NAME"] == {"Ref": "ArchiveBucket"}


@pytest.mark.parametrize(
    ("env_var", "parameter"),
    [
        ("LEAGUE_CAPACITY_MAX_CONCURRENT_MATCHES", "MaxConcurrentMatches"),
        ("LEAGUE_CAPACITY_MAX_STORED_MATCHES", "MaxStoredMatches"),
        ("LEAGUE_CAPACITY_MAX_MATCH_AGE_DAYS_HOT", "MaxMatchAgeDaysHot"),
        ("LEAGUE_CAPACITY_MAX_ARCHIVE_AGE_DAYS", "MaxArchiveAgeDays"),
    ],
)
def test_capacity_env_vars_are_wired_on_both_functions(
    template: dict[str, Any], env_var: str, parameter: str
) -> None:
    for logical_id in ("HttpHandlerFunction", "CleanupFunction"):
        env = template["Resources"][logical_id]["Properties"]["Environment"]["Variables"]
        assert env[env_var] == {"Ref": parameter}


def test_http_handler_function_has_two_http_api_routes(template: dict[str, Any]) -> None:
    """API Gateway HTTP API's greedy ``{proxy+}`` does not match the bare root path — a second,
    explicit ``/`` route is required or GET / 404s at API Gateway before the handler ever runs
    (verified against the live deployed stack).
    """
    events = template["Resources"]["HttpHandlerFunction"]["Properties"]["Events"]
    http_api_events = [event for event in events.values() if event["Type"] == "HttpApi"]
    paths = {event["Properties"]["Path"] for event in http_api_events}
    assert paths == {"/{proxy+}", "/"}
    for event in http_api_events:
        properties = event["Properties"]
        assert properties["Method"] == "ANY"
        assert properties["ApiId"] == {"Ref": "HttpApi"}


def test_http_handler_function_has_dynamodb_and_s3_policies_for_all_three_tables(
    template: dict[str, Any],
) -> None:
    policies = template["Resources"]["HttpHandlerFunction"]["Properties"]["Policies"]
    crud_table_refs = [
        policy["DynamoDBCrudPolicy"]["TableName"]
        for policy in policies
        if "DynamoDBCrudPolicy" in policy
    ]
    assert {"Ref": "MatchesTable"} in crud_table_refs
    assert {"Ref": "TokensTable"} in crud_table_refs
    assert {"Ref": "RatingsTable"} in crud_table_refs
    s3_bucket_refs = [
        policy["S3CrudPolicy"]["BucketName"] for policy in policies if "S3CrudPolicy" in policy
    ]
    assert {"Ref": "ArchiveBucket"} in s3_bucket_refs


def test_http_handler_function_grants_query_on_the_status_updated_gsi(
    template: dict[str, Any],
) -> None:
    policies = template["Resources"]["HttpHandlerFunction"]["Properties"]["Policies"]
    assert _grants_query_on_status_updated_index(policies)


@pytest.mark.parametrize(
    ("env_var", "expected_ref"),
    [
        ("MATCHES_TABLE_NAME", {"Ref": "MatchesTable"}),
        ("ARCHIVE_BUCKET_NAME", {"Ref": "ArchiveBucket"}),
        ("TOKENS_TABLE_NAME", {"Ref": "TokensTable"}),
        ("RATINGS_TABLE_NAME", {"Ref": "RatingsTable"}),
        ("LEAGUE_SESSION_SECRET", {"Ref": "SessionSecretValue"}),
    ],
)
def test_http_handler_env_vars_match_the_persistence_env_contract(
    template: dict[str, Any], env_var: str, expected_ref: dict[str, str]
) -> None:
    """The exact env-name contract the handler-wiring task (a separate task) relies on."""
    env = template["Resources"]["HttpHandlerFunction"]["Properties"]["Environment"]["Variables"]
    assert env[env_var] == expected_ref


def test_http_handler_env_names_match_python_source_constants(template: dict[str, Any]) -> None:
    """Cross-check env var *names* against their Python-source constants so the template and the
    code that will read them cannot silently drift apart — the same guard
    test_capacity_parameter_defaults_match_the_python_capacity_config applies to the capacity caps.
    """
    from league_site.auth.sessions import SESSION_SECRET_ENV
    from league_site.cli._commands._stores import ARCHIVE_BUCKET_ENV, MATCHES_TABLE_ENV

    env = template["Resources"]["HttpHandlerFunction"]["Properties"]["Environment"]["Variables"]
    assert MATCHES_TABLE_ENV in env
    assert ARCHIVE_BUCKET_ENV in env
    assert SESSION_SECRET_ENV in env


def test_outputs_include_tokens_and_ratings_table_names(template: dict[str, Any]) -> None:
    outputs = template["Outputs"]
    assert outputs["TokensTableName"]["Value"] == {"Ref": "TokensTable"}
    assert outputs["RatingsTableName"]["Value"] == {"Ref": "RatingsTable"}


def test_capacity_parameter_defaults_match_the_python_capacity_config(
    template: dict[str, Any],
) -> None:
    """The template's capacity defaults and CapacityConfig.default() must never drift apart."""
    defaults = CapacityConfig.default()
    parameters = template["Parameters"]
    assert parameters["MaxConcurrentMatches"]["Default"] == defaults.max_concurrent_matches
    assert parameters["MaxStoredMatches"]["Default"] == defaults.max_stored_matches
    assert parameters["MaxMatchAgeDaysHot"]["Default"] == defaults.max_match_age_days_hot
    assert parameters["MaxArchiveAgeDays"]["Default"] == defaults.max_archive_age_days


@pytest.mark.skipif(shutil.which("sam") is None, reason="AWS SAM CLI not installed")
def test_sam_validate_if_sam_cli_present() -> None:
    """Run `sam validate` when the CLI happens to be available; skip otherwise.

    A green run here is a bonus sanity check, not something this task's
    acceptance criteria depend on — the CLI is not assumed to exist in the
    environment running this test suite. Actually deploying (or proving a
    no-op redeploy) against a live AWS account is out of scope for this
    task's tests entirely; see the module docstring and docs/deploy.md.
    """
    result = subprocess.run(
        ["sam", "validate", "--region", "us-east-1", "--template-file", str(_TEMPLATE_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
