#!/usr/bin/env bash
# One-command deploy for the League of Agents platform's serverless stack.
#
# Usage:
#   infra/deploy.sh [stage-name] [budget-alert-email]
#
# Both arguments are optional after the first successful deploy of a given
# stage: `sam deploy` remembers prior parameter values via its saved config
# (samconfig.toml, generated on first run — see docs/deploy.md for what that
# file is and why it is gitignored). Re-running with no arguments against an
# unchanged template and unchanged built artifact is a no-op (empty
# changeset), not an error or a re-provision — see --no-fail-on-empty-changeset
# below.
#
# See docs/deploy.md for prerequisites, first-deploy walkthrough, redeploy,
# and teardown instructions. This script only wraps `sam build && sam
# deploy`; it does not replace reading that doc.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STACK_NAME="league-of-agents-platform"
STAGE_NAME="${1:-prod}"
BUDGET_ALERT_EMAIL="${2:-${BUDGET_ALERT_EMAIL:-}}"

if ! command -v sam >/dev/null 2>&1; then
    echo "error: the AWS SAM CLI ('sam') is not on PATH. See docs/deploy.md's Prerequisites section." >&2
    exit 2
fi

cd "$SCRIPT_DIR"

echo "==> sam build (stage: ${STAGE_NAME})"
sam build --template-file template.yaml

# BudgetAlertEmail has no Default in template.yaml (an operator must own that
# choice explicitly) — on first deploy, pass it as this script's 2nd arg or
# export BUDGET_ALERT_EMAIL. On every later redeploy `sam deploy` reuses the
# value already saved in samconfig.toml, so it can be omitted.
PARAM_OVERRIDES="StageName=${STAGE_NAME}"
if [[ -n "$BUDGET_ALERT_EMAIL" ]]; then
    PARAM_OVERRIDES="${PARAM_OVERRIDES} BudgetAlertEmail=${BUDGET_ALERT_EMAIL}"
fi

DEPLOY_ARGS=(
    --stack-name "${STACK_NAME}-${STAGE_NAME}"
    --parameter-overrides "${PARAM_OVERRIDES}"
    --capabilities CAPABILITY_IAM
    --resolve-s3
    # An unchanged redeploy (same template, same built artifact) produces an
    # empty changeset. Without this flag `sam deploy` treats that as a
    # failure; with it, a no-op redeploy exits 0 — the acceptance behavior
    # this script commits to.
    --no-fail-on-empty-changeset
    --confirm-changeset
)

echo "==> sam deploy (stack: ${STACK_NAME}-${STAGE_NAME})"
sam deploy "${DEPLOY_ARGS[@]}"
