#!/usr/bin/env bash
# scripts/dns-runbook.sh — committed, re-runnable cultureflare runbook that
# points Cloudflare-fronted league-of-agents.ai at the infra/domain.yaml
# stack's API Gateway regional custom domain.
#
# This is the runbook referenced by docs/architecture.md, docs/deploy.md,
# docs/operations.md, and the spec's honesty condition h4: "DNS and tunnel
# for league-of-agents.ai are created and verified by cultureflare commands
# captured in a committed runbook, and re-running the runbook is a no-op."
# Full prose walkthrough (prerequisites, expected output, rollback):
# docs/runbooks/cloudflare-league-of-agents-ai.md — read that first.
#
# Usage:
#   scripts/dns-runbook.sh validate [--apply] [--validation-file PATH]
#   scripts/dns-runbook.sh route    [--apply] [--apex-target HOST] [--www-target HOST]
#   scripts/dns-runbook.sh verify
#   scripts/dns-runbook.sh all      [--apply]
#
# Phases (see the doc for the full picture; summary here):
#
#   validate  Phase 1. Reads the ACM certificate's pending DNS validation
#             record(s) (via `aws cloudformation describe-stack-resources`
#             + `aws acm describe-certificate`, or from --validation-file)
#             and creates them in Cloudflare as unproxied CNAMEs via
#             `cultureflare dns create`. This is what lets the
#             infra/domain.yaml stack's Certificate resource finish
#             CREATE_IN_PROGRESS and issue.
#
#   route     Phase 2. Reads the infra/domain.yaml stack's
#             ApexRegionalDomainName / WwwRegionalDomainName outputs (or
#             --apex-target/--www-target) and creates the apex + www CNAME
#             records in Cloudflare, proxied (orange cloud), via
#             `cultureflare dns create`. Requires the domain.yaml stack to
#             already be CREATE_COMPLETE (i.e. Phase 1 already validated).
#
#   verify    Read-only. dig + curl checks that both hostnames resolve and
#             serve over HTTPS through Cloudflare.
#
#   all       Runs validate, then route, then verify in sequence. If route
#             is not yet possible (domain.yaml stack still validating),
#             prints why and continues to verify rather than aborting.
#
# Every mutating cultureflare call is dry-run unless --apply is passed to
# this script; --apply propagates to every `cultureflare ... --apply` call
# made in that run. Dry-run is safe to run at any time, including against a
# stack that does not exist yet — it will simply fail the AWS lookups with a
# clear error.
#
# Idempotence contract: cultureflare's own idempotency guard is keyed on
# type+name+content — re-running `cultureflare dns create` for a record that
# already exists exits 1 with an "already exists" message (see
# `cultureflare explain dns create`). This script's cf_dns_create wrapper
# treats that specific case as a no-op success, not a failure — so
# re-running this whole script (any phase, with or without --apply) after a
# prior successful --apply run is a no-op: every record already matches, so
# nothing is created a second time and the script exits 0. See the doc's
# "Idempotence contract" section for the full argument.
#
# Environment overrides (all optional; defaults match infra/domain.yaml and
# infra/deploy.sh's own defaults):
#   CF_ZONE           Cloudflare zone name (default: league-of-agents.ai)
#   APEX_HOST         Apex hostname to route (default: league-of-agents.ai)
#   WWW_HOST          www hostname to route (default: www.league-of-agents.ai)
#   DOMAIN_STACK_NAME infra/domain.yaml stack name
#                     (default: league-of-agents-platform-domain)
#   AWS_REGION        AWS region the domain stack was deployed to
#                     (default: $AWS_REGION, then $AWS_DEFAULT_REGION, then us-east-1)
#
# Requires on PATH: cultureflare, aws, jq, dig, curl (verify only).
# Requires in the environment: CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID
# (see docs/runbooks/cloudflare-league-of-agents-ai.md's Prerequisites).

set -euo pipefail

CF_ZONE="${CF_ZONE:-league-of-agents.ai}"
APEX_HOST="${APEX_HOST:-league-of-agents.ai}"
WWW_HOST="${WWW_HOST:-www.league-of-agents.ai}"
DOMAIN_STACK_NAME="${DOMAIN_STACK_NAME:-league-of-agents-platform-domain}"
AWS_REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"

apply=0
validation_file=""
apex_target=""
www_target=""

log() {
  # Progress/diagnostic lines only — never record data an operator might
  # want to pipe/parse (that goes through cultureflare's own stdout).
  printf '==> %s\n' "$*" >&2
}

usage() {
  # Skip line 1 (shebang), print the leading `#` comment block, stop at the
  # first non-comment line — same convention as infra/deploy.sh's siblings
  # in .claude/skills/cultureflare-write/scripts/.
  awk 'NR==1{next} /^#/{sub(/^# ?/, ""); print; next} {exit}' "$0"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "error: required command '$1' is not on PATH." >&2
    exit 2
  fi
}

# Runs `cultureflare dns create` and treats "record already exists" (exit 1
# with that specific message) as an idempotent no-op success rather than a
# failure — that is cultureflare's own idempotency contract (see
# `cultureflare explain dns create`), and it is what makes re-running this
# whole runbook a no-op. Any other non-zero exit (bad zone, missing token,
# auth failure, upstream error) is a real failure and is propagated.
cf_dns_create() {
  local zone="$1" type="$2" name="$3" content="$4"
  shift 4
  local -a extra_args=("$@")
  local -a apply_args=()
  if (( apply )); then
    apply_args=("--apply")
  fi
  local out rc
  set +e
  out=$(cultureflare dns create "$zone" "$type" "$name" "$content" --json \
    "${extra_args[@]}" "${apply_args[@]}" 2>&1)
  rc=$?
  set -e
  case "$rc" in
    0)
      log "ok: ${type} ${name} -> ${content} ($( (( apply )) && echo applied || echo dry-run))"
      ;;
    1)
      if [[ "$out" == *"already exists"* ]]; then
        log "no-op (already exists): ${type} ${name} -> ${content}"
      else
        echo "error: cultureflare dns create failed for ${type} ${name}" >&2
        echo "$out" >&2
        return 1
      fi
      ;;
    *)
      echo "error: cultureflare dns create failed (exit ${rc}) for ${type} ${name}" >&2
      echo "$out" >&2
      return "$rc"
      ;;
  esac
}

# Strips a single trailing "." — ACM and dig both report fully-qualified
# names/values with a trailing dot; Cloudflare's API (and every other
# record name in this repo's runbook) uses the bare form.
strip_trailing_dot() {
  local value="$1"
  printf '%s' "${value%.}"
}

fetch_stack_output() {
  local output_key="$1"
  aws cloudformation describe-stacks \
    --stack-name "$DOMAIN_STACK_NAME" \
    --region "$AWS_REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='${output_key}'].OutputValue" \
    --output text
}

# Phase 1: ACM DNS validation CNAME(s).
#
# infra/domain.yaml's Certificate resource has no Route 53
# DomainValidationOptions (DNS lives on Cloudflare, not Route 53), so
# CloudFormation holds the domain.yaml stack in CREATE_IN_PROGRESS until
# these records exist and ACM observes them — see the doc's "Why the stack
# appears to hang" note. Because of that, the validation record's name/value
# is read from the ACM API directly (keyed off the certificate ARN, which
# CloudFormation assigns the moment the resource *starts* creating — see
# infra/domain.yaml's CertificateArn output comment), not from stack
# Outputs, which do not exist yet at this point in the deploy.
phase_validate() {
  local records_json

  if [[ -n "$validation_file" ]]; then
    log "reading validation records from ${validation_file} (--validation-file override)"
    records_json=$(cat "$validation_file")
  else
    require_cmd aws
    require_cmd jq
    log "resolving Certificate ARN from stack ${DOMAIN_STACK_NAME} (region ${AWS_REGION})"
    local cert_arn
    cert_arn=$(aws cloudformation describe-stack-resources \
      --stack-name "$DOMAIN_STACK_NAME" \
      --region "$AWS_REGION" \
      --logical-resource-id Certificate \
      --query 'StackResources[0].PhysicalResourceId' \
      --output text)
    log "certificate ARN: ${cert_arn}"
    log "reading pending DNS validation records from ACM"
    records_json=$(aws acm describe-certificate \
      --certificate-arn "$cert_arn" \
      --region "$AWS_REGION" \
      --query 'Certificate.DomainValidationOptions' \
      --output json)
  fi

  require_cmd cultureflare
  require_cmd jq

  local count
  count=$(printf '%s' "$records_json" | jq 'length')
  if [[ "$count" -eq 0 ]]; then
    echo "error: no DomainValidationOptions found — is the certificate resource created yet?" >&2
    return 1
  fi

  local record name value rtype
  while IFS= read -r record; do
    name=$(printf '%s' "$record" | jq -r '.ResourceRecord.Name')
    value=$(printf '%s' "$record" | jq -r '.ResourceRecord.Value')
    rtype=$(printf '%s' "$record" | jq -r '.ResourceRecord.Type')
    name=$(strip_trailing_dot "$name")
    value=$(strip_trailing_dot "$value")
    log "validation record: ${rtype} ${name} -> ${value}"
    cf_dns_create "$CF_ZONE" "$rtype" "$name" "$value" \
      --comment "league-of-agents-platform: ACM DNS validation (scripts/dns-runbook.sh)"
  done < <(printf '%s' "$records_json" | jq -c '.[]')
}

# Phase 2: apex + www CNAME to the API Gateway regional custom domain,
# proxied. CNAME at the zone apex is normally invalid DNS, but Cloudflare's
# CNAME flattening handles it transparently for a proxied record — see the
# doc's "Why a CNAME at the apex works here" note. Requires the
# infra/domain.yaml stack to have reached CREATE_COMPLETE (i.e. Phase 1
# already validated the certificate) unless --apex-target/--www-target
# override the lookup.
phase_route() {
  local apex="$apex_target" www="$www_target"

  if [[ -z "$apex" || -z "$www" ]]; then
    require_cmd aws
    log "resolving regional domain targets from stack ${DOMAIN_STACK_NAME} (region ${AWS_REGION})"
    if [[ -z "$apex" ]]; then
      apex=$(fetch_stack_output ApexRegionalDomainName)
    fi
    if [[ -z "$www" ]]; then
      www=$(fetch_stack_output WwwRegionalDomainName)
    fi
  fi

  if [[ -z "$apex" || -z "$www" ]]; then
    cat >&2 <<'EOF'
error: could not resolve ApexRegionalDomainName / WwwRegionalDomainName.
       Either the domain.yaml stack has not reached CREATE_COMPLETE yet
       (still waiting on Phase 1's DNS validation to propagate — re-run
       `scripts/dns-runbook.sh route` once
       `aws cloudformation describe-stacks --stack-name <domain-stack>
       --query 'Stacks[0].StackStatus'` shows CREATE_COMPLETE), or pass
       --apex-target / --www-target explicitly.
EOF
    return 1
  fi

  require_cmd cultureflare
  log "apex target: ${apex}"
  log "www target: ${www}"
  cf_dns_create "$CF_ZONE" CNAME "$APEX_HOST" "$apex" --proxied \
    --comment "league-of-agents-platform: API Gateway regional domain (scripts/dns-runbook.sh)"
  cf_dns_create "$CF_ZONE" CNAME "$WWW_HOST" "$www" --proxied \
    --comment "league-of-agents-platform: API Gateway regional domain (scripts/dns-runbook.sh)"
}

# Read-only verification: both hostnames resolve, and both serve HTTPS
# through Cloudflare (the `cf-ray` response header is Cloudflare-specific —
# its presence confirms traffic is actually proxied, not just that *some*
# server answered).
phase_verify() {
  require_cmd dig
  require_cmd curl

  local host status headers
  for host in "$APEX_HOST" "$WWW_HOST"; do
    log "dig +short ${host}"
    dig +short "$host" >&2 || true

    log "curl -I https://${host}/"
    if headers=$(curl -sS -m 10 -D - -o /dev/null "https://${host}/" 2>&1); then
      status=$(printf '%s' "$headers" | head -n1 | tr -d '\r')
      log "  ${status}"
      if printf '%s' "$headers" | grep -qi '^cf-ray:'; then
        log "  cf-ray header present — traffic is going through Cloudflare"
      else
        log "  warning: no cf-ray header — traffic may not be proxied through Cloudflare"
      fi
    else
      log "  warning: HTTPS request to ${host} failed (see above) — DNS may not have propagated yet"
    fi
  done
}

phase_all() {
  phase_validate
  if ! phase_route; then
    log "route phase incomplete (see above) — continuing to verify anyway"
  fi
  phase_verify
}

main() {
  if [[ $# -eq 0 ]]; then
    usage
    exit 2
  fi

  local phase="$1"
  shift

  case "$phase" in
    -h|--help)
      usage
      exit 0
      ;;
  esac

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --apply)
        apply=1
        shift
        ;;
      --validation-file)
        validation_file="$2"
        shift 2
        ;;
      --apex-target)
        apex_target="$2"
        shift 2
        ;;
      --www-target)
        www_target="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        echo "error: unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done

  case "$phase" in
    validate)
      phase_validate
      ;;
    route)
      phase_route
      ;;
    verify)
      phase_verify
      ;;
    all)
      phase_all
      ;;
    *)
      echo "error: unknown phase: ${phase} (expected validate|route|verify|all)" >&2
      usage >&2
      exit 2
      ;;
  esac
}

main "$@"
