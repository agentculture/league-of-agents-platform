"""Tests for the live-evals infrastructure in ``infra/template.yaml``.

Covers the dedicated ``EvalsBucket`` (public read on ``evals/*`` only,
versioned, encrypted, no lifecycle rule that expires or transitions current
objects), the match-host IAM user + managed policy (write/delete on
``evals/*`` only), the standalone ``infra/iam-match-host-policy.json``
mirror, and the deploy-policy additions the new resources need. Nothing
here touches AWS: the template is parsed as plain YAML, the same way
``tests/test_lambda_template.py`` does it (the template uses long-form
intrinsics only, so ``yaml.safe_load`` suffices).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

_INFRA = Path(__file__).resolve().parent.parent / "infra"
_TEMPLATE_PATH = _INFRA / "template.yaml"
_MATCH_HOST_POLICY_PATH = _INFRA / "iam-match-host-policy.json"
_DEPLOY_POLICY_PATH = _INFRA / "iam-deploy-policy.json"
_RUNBOOK_PATH = _INFRA.parent / "docs" / "runbooks" / "evals-match-host.md"

# The constants the standalone policy files are written against (see
# docs/runbooks/iam-deploy-user.md: account + region are named explicitly).
_ACCOUNT_ID = "435593604218"
_STAGE = "prod"

_PUBLIC_PRINCIPALS = ("*", {"AWS": "*"}, {"AWS": ["*"]})
_ALLOWED_MATCH_HOST_ACTIONS = {"s3:PutObject", "s3:DeleteObject"}


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    with _TEMPLATE_PATH.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


@pytest.fixture(scope="module")
def match_host_policy() -> dict[str, Any]:
    return json.loads(_MATCH_HOST_POLICY_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def deploy_policy() -> dict[str, Any]:
    return json.loads(_DEPLOY_POLICY_PATH.read_text(encoding="utf-8"))


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _resolve_sub(value: Any) -> Any:
    """Resolve the ``Fn::Sub`` strings used here against the prod constants."""
    if not (isinstance(value, dict) and "Fn::Sub" in value):
        return value
    bucket = f"league-of-agents-{_STAGE}-evals-{_ACCOUNT_ID}"
    replacements = {
        "StageName": _STAGE,
        "AWS::AccountId": _ACCOUNT_ID,
        "AWS::Region": "us-east-1",
        "EvalsBucket.Arn": f"arn:aws:s3:::{bucket}",
        "EvalsBucket": bucket,
    }

    def _sub(match: re.Match[str]) -> str:
        return replacements[match.group(1)]

    return re.sub(r"\$\{([^}]+)\}", _sub, value["Fn::Sub"])


def _evals_bucket(template: dict[str, Any]) -> dict[str, Any]:
    return template["Resources"]["EvalsBucket"]


def _bucket_policy_statements(template: dict[str, Any]) -> list[dict[str, Any]]:
    policies = [
        resource
        for resource in template["Resources"].values()
        if resource["Type"] == "AWS::S3::BucketPolicy"
        and resource["Properties"]["Bucket"] == {"Ref": "EvalsBucket"}
    ]
    assert len(policies) == 1, "EvalsBucket must have exactly one bucket policy"
    return policies[0]["Properties"]["PolicyDocument"]["Statement"]


# --- EvalsBucket ---------------------------------------------------------------


def test_evals_bucket_exists_and_is_not_the_archive_bucket(template: dict[str, Any]) -> None:
    resources = template["Resources"]
    assert resources["EvalsBucket"]["Type"] == "AWS::S3::Bucket"
    assert "ArchiveBucket" in resources
    evals_name = _resolve_sub(resources["EvalsBucket"]["Properties"]["BucketName"])
    archive_name = _resolve_sub(resources["ArchiveBucket"]["Properties"]["BucketName"])
    assert evals_name != archive_name
    assert "evals" in evals_name


def test_evals_bucket_policy_public_allow_is_only_getobject_on_evals_prefix(
    template: dict[str, Any],
) -> None:
    public_allows = [
        statement
        for statement in _bucket_policy_statements(template)
        if statement["Effect"] == "Allow" and statement.get("Principal") in _PUBLIC_PRINCIPALS
    ]
    assert len(public_allows) == 1
    statement = public_allows[0]
    assert _as_list(statement["Action"]) == ["s3:GetObject"]
    resources = [_resolve_sub(r) for r in _as_list(statement["Resource"])]
    assert resources == [f"arn:aws:s3:::league-of-agents-{_STAGE}-evals-{_ACCOUNT_ID}/evals/*"]
    assert "Condition" not in statement


def test_evals_bucket_policy_has_no_other_allow_statements(template: dict[str, Any]) -> None:
    allows = [s for s in _bucket_policy_statements(template) if s["Effect"] == "Allow"]
    assert len(allows) == 1


def test_evals_bucket_still_blocks_public_acls(template: dict[str, Any]) -> None:
    block = _evals_bucket(template)["Properties"]["PublicAccessBlockConfiguration"]
    assert block["BlockPublicAcls"] is True
    assert block["IgnorePublicAcls"] is True
    # Relaxed only so the evals/* GetObject bucket policy can take effect.
    assert block["BlockPublicPolicy"] is False
    assert block["RestrictPublicBuckets"] is False


def test_archive_bucket_stays_fully_private(template: dict[str, Any]) -> None:
    block = template["Resources"]["ArchiveBucket"]["Properties"]["PublicAccessBlockConfiguration"]
    assert all(value is True for value in block.values())


def test_evals_bucket_is_versioned_and_encrypted(template: dict[str, Any]) -> None:
    props = _evals_bucket(template)["Properties"]
    assert props["VersioningConfiguration"] == {"Status": "Enabled"}
    rules = props["BucketEncryption"]["ServerSideEncryptionConfiguration"]
    algorithms = {rule["ServerSideEncryptionByDefault"]["SSEAlgorithm"] for rule in rules}
    assert algorithms and algorithms <= {"AES256", "aws:kms"}


_CURRENT_OBJECT_KEYS = {
    "ExpirationInDays",
    "ExpirationDate",
    "Transitions",
    "Transition",
}


def test_evals_bucket_has_no_lifecycle_rule_touching_current_objects(
    template: dict[str, Any],
) -> None:
    lifecycle = _evals_bucket(template)["Properties"].get("LifecycleConfiguration", {})
    for rule in lifecycle.get("Rules", []):
        assert not (_CURRENT_OBJECT_KEYS & set(rule)), rule


def test_evals_bucket_bounds_noncurrent_version_cost(template: dict[str, Any]) -> None:
    rules = _evals_bucket(template)["Properties"]["LifecycleConfiguration"]["Rules"]
    noncurrent = [
        rule["NoncurrentVersionExpiration"]["NoncurrentDays"]
        for rule in rules
        if rule.get("Status") == "Enabled" and "NoncurrentVersionExpiration" in rule
    ]
    assert noncurrent and all(0 < days <= 90 for days in noncurrent)


def test_evals_bucket_has_no_cors_or_website_configuration(template: dict[str, Any]) -> None:
    props = _evals_bucket(template)["Properties"]
    assert "CorsConfiguration" not in props
    # Cloudflare reaches the REST endpoint, not the S3 website endpoint.
    assert "WebsiteConfiguration" not in props


def test_evals_bucket_name_is_an_output(template: dict[str, Any]) -> None:
    assert template["Outputs"]["EvalsBucketName"]["Value"] == {"Ref": "EvalsBucket"}


def test_no_lambda_gets_access_to_the_evals_bucket(template: dict[str, Any]) -> None:
    """The site never writes eval data; only the match host does."""
    for resource in template["Resources"].values():
        if resource["Type"] != "AWS::Serverless::Function":
            continue
        assert "EvalsBucket" not in json.dumps(resource["Properties"].get("Policies", []))


# --- match-host IAM ------------------------------------------------------------


def _template_match_host_statements(template: dict[str, Any]) -> list[dict[str, Any]]:
    policy = template["Resources"]["MatchHostPolicy"]
    assert policy["Type"] == "AWS::IAM::ManagedPolicy"
    return policy["Properties"]["PolicyDocument"]["Statement"]


def test_match_host_user_exists_without_access_keys(template: dict[str, Any]) -> None:
    resources = template["Resources"]
    user = resources["MatchHostUser"]
    assert user["Type"] == "AWS::IAM::User"
    assert user["Properties"]["ManagedPolicyArns"] == [{"Ref": "MatchHostPolicy"}]
    # Key creation is a manual operator step (docs/runbooks/evals-match-host.md).
    assert not [r for r in resources.values() if r["Type"] == "AWS::IAM::AccessKey"]
    # No login profile / inline policies: the user is a key-only writer.
    assert "LoginProfile" not in user["Properties"]
    assert "Policies" not in user["Properties"]
    # Auto-named (stack-prefixed), so CAPABILITY_IAM suffices and the deploy
    # policy's user/league-of-agents-platform-* scope covers it.
    assert "UserName" not in user["Properties"]
    assert "ManagedPolicyName" not in resources["MatchHostPolicy"]["Properties"]


def test_template_match_host_policy_is_put_and_delete_on_evals_prefix_only(
    template: dict[str, Any],
) -> None:
    statements = _template_match_host_statements(template)
    assert statements
    for statement in statements:
        assert statement["Effect"] == "Allow"
        assert set(_as_list(statement["Action"])) <= _ALLOWED_MATCH_HOST_ACTIONS
        assert [_resolve_sub(r) for r in _as_list(statement["Resource"])] == [
            f"arn:aws:s3:::league-of-agents-{_STAGE}-evals-{_ACCOUNT_ID}/evals/*"
        ]
    granted = {a for s in statements for a in _as_list(s["Action"])}
    assert granted == _ALLOWED_MATCH_HOST_ACTIONS


def test_standalone_match_host_policy_allows_only_put_and_delete_on_evals(
    match_host_policy: dict[str, Any],
) -> None:
    assert match_host_policy["Version"] == "2012-10-17"
    statements = match_host_policy["Statement"]
    granted: set[str] = set()
    for statement in statements:
        assert statement["Effect"] == "Allow"
        assert "NotAction" not in statement and "NotResource" not in statement
        actions = _as_list(statement["Action"])
        resources = _as_list(statement["Resource"])
        assert all("*" not in action for action in actions)
        assert set(actions) <= _ALLOWED_MATCH_HOST_ACTIONS
        assert resources == [f"arn:aws:s3:::league-of-agents-{_STAGE}-evals-{_ACCOUNT_ID}/evals/*"]
        # The only wildcard is the object-key suffix under evals/.
        assert all(r.count("*") == 1 and r.endswith("/evals/*") for r in resources)
        granted |= set(actions)
    assert granted == _ALLOWED_MATCH_HOST_ACTIONS


def test_standalone_match_host_policy_mirrors_the_template(
    template: dict[str, Any], match_host_policy: dict[str, Any]
) -> None:
    def _normalize(statements: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        return sorted(
            (
                s.get("Sid"),
                s["Effect"],
                tuple(sorted(_as_list(s["Action"]))),
                tuple(sorted(_resolve_sub(r) for r in _as_list(s["Resource"]))),
            )
            for s in statements
        )

    assert _normalize(_template_match_host_statements(template)) == _normalize(
        match_host_policy["Statement"]
    )


# --- deploy policy + runbook ---------------------------------------------------


def _deploy_actions_for(deploy_policy: dict[str, Any], resource_prefix: str) -> set[str]:
    actions: set[str] = set()
    for statement in deploy_policy["Statement"]:
        if any(str(r).startswith(resource_prefix) for r in _as_list(statement["Resource"])):
            actions |= set(_as_list(statement["Action"]))
    return actions


def test_deploy_policy_can_manage_the_match_host_user_scoped_to_the_stack(
    deploy_policy: dict[str, Any],
) -> None:
    prefix = f"arn:aws:iam::{_ACCOUNT_ID}:user/league-of-agents-platform-"
    actions = _deploy_actions_for(deploy_policy, prefix)
    assert {"iam:CreateUser", "iam:DeleteUser", "iam:AttachUserPolicy"} <= actions
    # The deploy identity must never mint keys for the match host.
    assert not {"iam:CreateAccessKey", "iam:CreateLoginProfile"} & actions
    for statement in deploy_policy["Statement"]:
        resources = _as_list(statement["Resource"])
        if any(":user/" in str(r) for r in resources):
            assert all(str(r).startswith(prefix) for r in resources)


def test_deploy_policy_can_manage_the_match_host_managed_policy(
    deploy_policy: dict[str, Any],
) -> None:
    prefix = f"arn:aws:iam::{_ACCOUNT_ID}:policy/league-of-agents-platform-"
    actions = _deploy_actions_for(deploy_policy, prefix)
    assert {"iam:CreatePolicy", "iam:DeletePolicy", "iam:CreatePolicyVersion"} <= actions


def test_deploy_policy_can_put_and_remove_the_bucket_policy(
    deploy_policy: dict[str, Any],
) -> None:
    actions = _deploy_actions_for(deploy_policy, "arn:aws:s3:::league-of-agents-")
    assert {"s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:PutBucketVersioning"} <= actions
    assert {"s3:PutEncryptionConfiguration", "s3:PutBucketPublicAccessBlock"} <= actions


def test_match_host_runbook_exists_and_keeps_keys_out_of_the_repo() -> None:
    text = _RUNBOOK_PATH.read_text(encoding="utf-8")
    assert "create-access-key" in text
    assert "delete-access-key" in text
    assert "never" in text.lower()
