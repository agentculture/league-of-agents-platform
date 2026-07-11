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
# GitHub OAuth + session secrets (see docs/runbooks/github-oauth-app.md):
# this script sources a repo-root .env (gitignored, never committed) if one
# exists, and maps its GITHUB_APP_CLIENT_ID / GITHUB_APP_CLIENT_SECRET names
# onto the GithubOauthClientId / GithubOauthClientSecret CloudFormation
# parameters. LEAGUE_SESSION_SECRET can also live in that .env; generate one
# with:
#   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
# None of these three values are ever echoed to stdout/stderr by this script,
# and none are written anywhere by it — sam itself persists whatever was
# passed into infra/samconfig.toml (also gitignored; see docs/deploy.md's
# "Where state lives" table), the same way BudgetAlertEmail already is.
#
# See docs/deploy.md for prerequisites, first-deploy walkthrough, redeploy,
# and teardown instructions. This script only wraps `sam build && sam
# deploy`; it does not replace reading that doc.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
STACK_NAME="league-of-agents-platform"
STAGE_NAME="${1:-prod}"
BUDGET_ALERT_EMAIL="${2:-${BUDGET_ALERT_EMAIL:-}}"

if ! command -v sam >/dev/null 2>&1; then
    echo "error: the AWS SAM CLI ('sam') is not on PATH. See docs/deploy.md's Prerequisites section." >&2
    exit 2
fi

# Load operator secrets from a repo-root .env, if the operator keeps one
# there (see docs/runbooks/github-oauth-app.md). Never echoed: `set -a`
# exports every assignment for the rest of this script's own use, not for
# printing.
ENV_FILE="${REPO_ROOT}/.env"
if [[ -f "$ENV_FILE" ]]; then
    echo "==> loading secrets from ${ENV_FILE} (not printed)"
    set -a
    # shellcheck source=/dev/null
    source "$ENV_FILE"
    set +a
fi

# Map the operator-facing .env names onto the CloudFormation parameter
# names that flow through to the LEAGUE_OAUTH_GITHUB_CLIENT_ID/SECRET env
# vars league_site/auth/oauth.py reads at runtime (see
# infra/template.yaml's GithubOauthClientId/GithubOauthClientSecret
# parameters).
GITHUB_OAUTH_CLIENT_ID="${GITHUB_APP_CLIENT_ID:-}"
GITHUB_OAUTH_CLIENT_SECRET="${GITHUB_APP_CLIENT_SECRET:-}"

# LEAGUE_SESSION_SECRET: generate one on a genuinely first deploy (no .env
# value and no prior samconfig.toml to fall back to); otherwise require it
# to come from the operator (.env, an exported var, or — on a redeploy —
# whatever sam already saved) rather than silently regenerating a value
# that would invalidate every live session on each run.
SAMCONFIG="${SCRIPT_DIR}/samconfig.toml"
if [[ -z "${LEAGUE_SESSION_SECRET:-}" && ! -f "$SAMCONFIG" ]]; then
    echo "==> no LEAGUE_SESSION_SECRET found and no prior deploy config (${SAMCONFIG}) — generating one for this first deploy (not printed)"
    LEAGUE_SESSION_SECRET="$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")"
fi

cd "$SCRIPT_DIR"

echo "==> sam build (stage: ${STAGE_NAME})"
sam build --template-file template.yaml

# BudgetAlertEmail has no Default in template.yaml (an operator must own that
# choice explicitly) — on first deploy, pass it as this script's 2nd arg or
# export BUDGET_ALERT_EMAIL. On every later redeploy `sam deploy` reuses the
# value already saved in samconfig.toml, so it can be omitted. The three
# secrets below follow the identical "pass if present, otherwise let sam
# reuse the saved value" pattern — see the header comment above.
PARAM_OVERRIDES="StageName=${STAGE_NAME}"
if [[ -n "$BUDGET_ALERT_EMAIL" ]]; then
    PARAM_OVERRIDES="${PARAM_OVERRIDES} BudgetAlertEmail=${BUDGET_ALERT_EMAIL}"
fi
if [[ -n "${LEAGUE_SESSION_SECRET:-}" ]]; then
    PARAM_OVERRIDES="${PARAM_OVERRIDES} SessionSecretValue=${LEAGUE_SESSION_SECRET}"
fi
if [[ -n "$GITHUB_OAUTH_CLIENT_ID" ]]; then
    PARAM_OVERRIDES="${PARAM_OVERRIDES} GithubOauthClientId=${GITHUB_OAUTH_CLIENT_ID}"
fi
if [[ -n "$GITHUB_OAUTH_CLIENT_SECRET" ]]; then
    PARAM_OVERRIDES="${PARAM_OVERRIDES} GithubOauthClientSecret=${GITHUB_OAUTH_CLIENT_SECRET}"
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
