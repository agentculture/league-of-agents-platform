#!/usr/bin/env bash
# scripts/evals-cloudflare.sh — route https://league-of-agents.ai/evals/data/*
# to the evals bucket through a Cloudflare Worker (infra/cloudflare/evals-worker.js).
# Mechanism decision and the full procedure: "Live evals data" in
# docs/runbooks/cloudflare-league-of-agents-ai.md.
#
# Usage:
#   scripts/evals-cloudflare.sh            # dry run (default): print the plan, no network
#   scripts/evals-cloudflare.sh --apply    # deploy the Worker + routes with wrangler
#
# Dry-run makes no network calls and needs no credentials or tools.
# --apply needs `wrangler` on PATH (cultureflare has no Worker verbs; it is
# used only for the read-only `cultureflare whoami` check when present) and
# CLOUDFLARE_API_TOKEN (scopes: Workers Scripts:Edit, Workers Routes:Edit on the
# zone) plus CLOUDFLARE_ACCOUNT_ID.
#
# Environment overrides (optional):
#   CF_ZONE       zone name (default: league-of-agents.ai)
#   EVALS_ORIGIN  bucket REST hostname, <bucket>.s3.<region>.amazonaws.com
#                 (--apply resolves it from the stack's EvalsBucketName
#                 output when unset; dry-run prints a placeholder)
#   STACK_NAME    main stack (default: league-of-agents-platform-prod)
#   AWS_REGION    (default: $AWS_REGION, $AWS_DEFAULT_REGION, us-east-1)
#   WORKER_NAME   (default: league-evals-data)

set -euo pipefail

CF_ZONE="${CF_ZONE:-league-of-agents.ai}"
STACK_NAME="${STACK_NAME:-league-of-agents-platform-prod}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
WORKER_NAME="${WORKER_NAME:-league-evals-data}"
EVALS_ORIGIN="${EVALS_ORIGIN:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKER_FILE="$ROOT/infra/cloudflare/evals-worker.js"
ROUTES=("${CF_ZONE}/evals/data/*" "www.${CF_ZONE}/evals/data/*")

apply=0
for arg in "$@"; do
  case "$arg" in
    --apply) apply=1 ;;
    -h|--help) awk 'NR==1{next} /^#/{sub(/^# ?/, ""); print; next} {exit}' "$0"; exit 0 ;;
    *) echo "error: unknown argument '$arg'" >&2; exit 2 ;;
  esac
done

log() { printf '==> %s\n' "$*"; }

render_config() {
  local origin="$1"
  cat <<EOF
name = "${WORKER_NAME}"
main = "${WORKER_FILE}"
compatibility_date = "2026-01-01"
workers_dev = false
routes = [
  { pattern = "${ROUTES[0]}", zone_name = "${CF_ZONE}" },
  { pattern = "${ROUTES[1]}", zone_name = "${CF_ZONE}" },
]

[vars]
EVALS_ORIGIN = "${origin}"
EOF
}

if (( ! apply )); then
  log "dry run (no network calls, nothing changed); pass --apply to perform it"
  log "would deploy Worker '${WORKER_NAME}' from ${WORKER_FILE}"
  for r in "${ROUTES[@]}"; do log "would bind route: ${r}"; done
  log "origin: https://${EVALS_ORIGIN:-<EvalsBucketName>.s3.${AWS_REGION}.amazonaws.com}/evals/<path after /evals/data/>"
  log "cache: manifest.json 15 s; live/ seat files 5 s; thinking/ and hashed files immutable; 403 -> 404"
  log "wrangler.toml that would be used:"
  render_config "${EVALS_ORIGIN:-<EvalsBucketName>.s3.${AWS_REGION}.amazonaws.com}" | sed 's/^/    /'
  exit 0
fi

command -v wrangler >/dev/null 2>&1 || { echo "error: --apply needs 'wrangler' on PATH" >&2; exit 2; }
: "${CLOUDFLARE_API_TOKEN:?error: CLOUDFLARE_API_TOKEN not set}"
: "${CLOUDFLARE_ACCOUNT_ID:?error: CLOUDFLARE_ACCOUNT_ID not set}"
if command -v cultureflare >/dev/null 2>&1; then cultureflare whoami || true; fi

if [[ -z "$EVALS_ORIGIN" ]]; then
  command -v aws >/dev/null 2>&1 || { echo "error: set EVALS_ORIGIN or install aws" >&2; exit 2; }
  bucket=$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='EvalsBucketName'].OutputValue" --output text)
  EVALS_ORIGIN="${bucket}.s3.${AWS_REGION}.amazonaws.com"
fi

cfg="$(mktemp -d)/wrangler.toml"
render_config "$EVALS_ORIGIN" >"$cfg"
log "deploying Worker '${WORKER_NAME}' with routes ${ROUTES[*]}"
wrangler deploy --config "$cfg"
log "done; verify per the runbook: curl -I https://${CF_ZONE}/evals/data/<eval>/manifest.json"
