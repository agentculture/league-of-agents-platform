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
    """Matches league_site/matches/serialization.py's documented PK/SK key scheme."""
    table = template["Resources"]["MatchesTable"]["Properties"]
    key_names = {attr["AttributeName"] for attr in table["AttributeDefinitions"]}
    assert key_names == {"PK", "SK"}
    key_schema = {entry["AttributeName"]: entry["KeyType"] for entry in table["KeySchema"]}
    assert key_schema == {"PK": "HASH", "SK": "RANGE"}
    assert table["BillingMode"] == "PAY_PER_REQUEST"


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
        ["sam", "validate", "--template-file", str(_TEMPLATE_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
