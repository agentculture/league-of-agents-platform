"""Tests for ``infra/domain.yaml`` — the Cloudflare-front domain stack.

Companion to ``tests/test_lambda_template.py`` (same conventions): these
tests do not touch AWS. They parse the template as plain YAML and assert
its shape (resource set, the ``ApiId``/``ApiStage`` cross-stack parameters,
and the outputs ``scripts/dns-runbook.sh`` depends on). ``sam validate`` is
invoked only when the ``sam`` CLI is present on the machine running the
tests. Proving an actual deploy — including the CREATE_IN_PROGRESS-until-
DNS-validated behavior documented in
``docs/runbooks/cloudflare-league-of-agents-ai.md`` — against a live AWS
account is out of scope for this test suite; see that doc's "Status"
section for what is and is not proven here.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

_TEMPLATE_PATH = Path(__file__).resolve().parent.parent / "infra" / "domain.yaml"

_EXPECTED_RESOURCES: dict[str, str] = {
    "Certificate": "AWS::CertificateManager::Certificate",
    "DomainNameApex": "AWS::ApiGatewayV2::DomainName",
    "DomainNameWww": "AWS::ApiGatewayV2::DomainName",
    "ApiMappingApex": "AWS::ApiGatewayV2::ApiMapping",
    "ApiMappingWww": "AWS::ApiGatewayV2::ApiMapping",
}

_EXPECTED_OUTPUTS = {
    "CertificateArn",
    "ApexRegionalDomainName",
    "WwwRegionalDomainName",
}


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    """Parse ``infra/domain.yaml`` with a plain YAML loader.

    Written entirely with long-form intrinsic functions (``Fn::Sub`` /
    ``Ref`` / ``Fn::GetAtt`` instead of the ``!Sub``/``!Ref``/``!GetAtt``
    short-hand tags), matching ``infra/template.yaml``'s convention, so
    this parses with ``yaml.safe_load`` and no CloudFormation-tag-aware
    loader is needed.
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


def test_api_id_is_a_required_parameter_with_no_default(template: dict[str, Any]) -> None:
    """ApiId is the cross-stack link to infra/template.yaml's HttpApi.

    No ``Default`` on purpose — the operator must supply the main stack's
    HttpApi ID explicitly (see docs/runbooks/cloudflare-league-of-agents-ai.md).
    """
    api_id_param = template["Parameters"]["ApiId"]
    assert api_id_param["Type"] == "String"
    assert "Default" not in api_id_param


def test_api_stage_parameter_defaults_to_prod(template: dict[str, Any]) -> None:
    """Matches infra/template.yaml's StageName default, so the two stacks

    line up out of the box for the common single-stage deploy.
    """
    assert template["Parameters"]["ApiStage"]["Default"] == "prod"


def test_domain_name_parameter_defaults_to_the_apex_domain(template: dict[str, Any]) -> None:
    assert template["Parameters"]["DomainName"]["Default"] == "league-of-agents.ai"


def test_certificate_covers_apex_and_www_with_dns_validation(template: dict[str, Any]) -> None:
    cert = template["Resources"]["Certificate"]["Properties"]
    assert cert["DomainName"] == {"Ref": "DomainName"}
    assert cert["SubjectAlternativeNames"] == [{"Fn::Sub": "www.${DomainName}"}]
    assert cert["ValidationMethod"] == "DNS"
    # No Route 53 HostedZoneId auto-validation wired in: DNS lives on
    # Cloudflare, not Route 53 — see the runbook's "Why the stack appears
    # to hang" note.
    assert "DomainValidationOptions" not in cert


def test_custom_domains_are_regional_not_edge_optimized(template: dict[str, Any]) -> None:
    """league-of-agents.ai is already fronted by Cloudflare's own edge —

    a second CloudFront distribution (EDGE-optimized) would double the
    edge hops for no benefit. See infra/domain.yaml's comment.
    """
    for logical_id in ("DomainNameApex", "DomainNameWww"):
        domain = template["Resources"][logical_id]["Properties"]
        (config,) = domain["DomainNameConfigurations"]
        assert config["EndpointType"] == "REGIONAL"
        assert config["SecurityPolicy"] == "TLS_1_2"
        assert config["CertificateArn"] == {"Ref": "Certificate"}


def test_apex_domain_name_uses_the_domain_name_parameter_directly(
    template: dict[str, Any],
) -> None:
    apex = template["Resources"]["DomainNameApex"]["Properties"]
    assert apex["DomainName"] == {"Ref": "DomainName"}


def test_www_domain_name_is_derived_from_the_domain_name_parameter(
    template: dict[str, Any],
) -> None:
    www = template["Resources"]["DomainNameWww"]["Properties"]
    assert www["DomainName"] == {"Fn::Sub": "www.${DomainName}"}


def test_api_mappings_reference_the_apiid_and_apistage_parameters(
    template: dict[str, Any],
) -> None:
    """Every mapping is parameterized on ApiId/ApiStage — the whole point

    of this being a second, independently deployable stack: nothing here
    hardcodes the main stack's HttpApi.
    """
    mapping_by_domain = {
        "ApiMappingApex": "DomainNameApex",
        "ApiMappingWww": "DomainNameWww",
    }
    for mapping_id, domain_id in mapping_by_domain.items():
        mapping = template["Resources"][mapping_id]["Properties"]
        assert mapping["ApiId"] == {"Ref": "ApiId"}
        assert mapping["Stage"] == {"Ref": "ApiStage"}
        assert mapping["DomainName"] == {"Ref": domain_id}
        # Root-path mapping: no ApiMappingKey, so the mapping applies to
        # the whole host, not a path prefix.
        assert "ApiMappingKey" not in mapping


def test_template_declares_exactly_the_expected_outputs(template: dict[str, Any]) -> None:
    assert _EXPECTED_OUTPUTS <= set(template["Outputs"].keys())


def test_certificate_arn_output_points_at_the_certificate_resource(
    template: dict[str, Any],
) -> None:
    assert template["Outputs"]["CertificateArn"]["Value"] == {"Ref": "Certificate"}


def test_regional_domain_name_outputs_point_at_the_domain_name_resources(
    template: dict[str, Any],
) -> None:
    assert template["Outputs"]["ApexRegionalDomainName"]["Value"] == {
        "Fn::GetAtt": "DomainNameApex.RegionalDomainName"
    }
    assert template["Outputs"]["WwwRegionalDomainName"]["Value"] == {
        "Fn::GetAtt": "DomainNameWww.RegionalDomainName"
    }


@pytest.mark.skipif(shutil.which("sam") is None, reason="AWS SAM CLI not installed")
def test_sam_validate_if_sam_cli_present() -> None:
    """Run `sam validate` when the CLI happens to be available; skip otherwise.

    A green run here is a bonus sanity check, not something this task's
    acceptance criteria depend on — the CLI is not assumed to exist in the
    environment running this test suite. Actually deploying this stack
    (including the DNS-validation-blocks-CREATE_COMPLETE behavior the
    runbook documents) against a live AWS account is out of scope for this
    task's tests entirely; see the module docstring and
    docs/runbooks/cloudflare-league-of-agents-ai.md.
    """
    result = subprocess.run(
        ["sam", "validate", "--template-file", str(_TEMPLATE_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
