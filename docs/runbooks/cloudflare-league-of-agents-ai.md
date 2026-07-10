# Cloudflare front for league-of-agents.ai

Operator runbook for fronting `league-of-agents.ai` with Cloudflare, on top
of the AWS origin `infra/template.yaml` deploys. DNS, TLS, and routing are
managed exclusively through the `cultureflare` CLI, driven by the committed,
re-runnable `scripts/dns-runbook.sh`. Cloudflare Access is **not** used —
this is a public site, not one gated behind an authenticated tunnel.

## Status

This runbook has been authored, syntax-checked (`bash -n`, `shellcheck`),
and exercised against a fake `cultureflare` stand-in and a scratch ACM
validation payload — see `tests/test_domain_template.py` for what is
actually proven by this repository's test suite. It has **not** been run
against the real `league-of-agents.ai` Cloudflare zone or a real AWS
account: no `CLOUDFLARE_API_TOKEN` or AWS credentials are available in the
environment that produced this runbook. First real execution — and the
resulting no-op-on-a-second-run proof — is the live-launch-checklist task's
job (see `docs/operations.md`'s Launch Checklist), not this one's. This is
the same honesty split `docs/deploy.md` documents for `infra/template.yaml`
itself.

## Design

- **DNS**: Cloudflare (proxied / orange cloud), managed by `cultureflare`.
- **Origin**: API Gateway HTTP API (`infra/template.yaml`'s `HttpApi`),
  fronted by a REGIONAL custom domain in `infra/domain.yaml`, a second,
  independent SAM/CloudFormation stack.
- **TLS**: An ACM certificate for `league-of-agents.ai` and
  `www.league-of-agents.ai` (one certificate, two names), DNS-validated.
- **Access**: Not used. `cultureflare remote-login` (which puts a hostname
  behind Cloudflare Access) is deliberately not part of this runbook.

Two AWS stacks exist on purpose: `infra/template.yaml` (compute, data,
budget) and `infra/domain.yaml` (certificate, custom domain, API mapping).
They are parameterized together — `infra/domain.yaml` takes the main
stack's `HttpApi` ID as a required `ApiId` parameter — but deployed and
torn down independently, so a domain/cert change never touches match data
and vice versa.

## Prerequisites

- The main stack (`infra/template.yaml`) already deployed — see
  `docs/deploy.md`. This runbook needs its `HttpApi` resource ID.
- `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` exported in the
  shell running `cultureflare` (see `cultureflare learn` for the
  recommended secure-loading pattern — a `chmod 600` env file outside any
  git-tracked directory, sourced just before invoking `cultureflare`).
- AWS credentials with permission to deploy `infra/domain.yaml` and to call
  `cloudformation:DescribeStack*` and `acm:DescribeCertificate`.
- `cultureflare`, `aws`, `jq`, `dig`, and `curl` on `PATH`.

## Command sequence

### 1. Deploy infra/domain.yaml

Resolve the main stack's `HttpApi` ID and deploy the domain stack:

```bash
API_ID=$(aws cloudformation describe-stack-resources \
  --stack-name league-of-agents-platform-prod \
  --logical-resource-id HttpApi \
  --query 'StackResources[0].PhysicalResourceId' --output text)

sam deploy \
  --template-file infra/domain.yaml \
  --stack-name league-of-agents-platform-domain \
  --parameter-overrides "ApiId=${API_ID}" "ApiStage=prod" \
  --capabilities CAPABILITY_IAM \
  --resolve-s3 \
  --no-fail-on-empty-changeset
```

### Why the stack appears to hang

`infra/domain.yaml`'s `Certificate` resource is DNS-validated with no
Route 53 auto-validation wired in (DNS lives on Cloudflare, not Route 53).
CloudFormation holds the `Certificate` resource — and so the whole stack —
in `CREATE_IN_PROGRESS` until the validation CNAME below exists and ACM
observes it. **This is expected.** Run the deploy in the background (or in
a separate terminal) and move on to step 2 while it waits.

### 2. Phase 1 — create the ACM validation CNAME(s)

```bash
scripts/dns-runbook.sh validate
```

Dry-run by default: prints the zone, record type/name/content, and the
exact JSON body `cultureflare dns create` would `POST`, for each pending
validation record (one per name on the certificate — apex and `www`), and
exits `0` without creating anything. Expected output looks like:

```text
==> resolving Certificate ARN from stack league-of-agents-platform-domain (region us-east-1)
==> certificate ARN: arn:aws:acm:us-east-1:123456789012:certificate/...
==> reading pending DNS validation records from ACM
==> validation record: CNAME _abc123.league-of-agents.ai -> _def456.acm-validations.aws
==> ok: CNAME _abc123.league-of-agents.ai -> _def456.acm-validations.aws (dry-run)
==> validation record: CNAME _ghi789.www.league-of-agents.ai -> _jkl012.acm-validations.aws
==> ok: CNAME _ghi789.www.league-of-agents.ai -> _jkl012.acm-validations.aws (dry-run)
```

Once satisfied, commit it:

```bash
scripts/dns-runbook.sh validate --apply
```

**These validation CNAMEs must stay unproxied (DNS-only / grey cloud)** —
`scripts/dns-runbook.sh` never passes `--proxied` for them. ACM resolves
them with a plain DNS lookup; a proxied (orange cloud) record would return
Cloudflare's edge IPs instead of the real validation target and the
certificate would never validate. Within a few minutes of the CNAME
propagating, ACM issues the certificate and the `infra/domain.yaml` deploy
from step 1 completes on its own (`CREATE_COMPLETE`).

`CertificateArn` is a stack Output specifically so this step can find the
certificate before the stack finishes creating (`aws cloudformation
describe-stack-resources` returns a resource's `PhysicalResourceId` — here,
the certificate ARN — the moment the resource *starts* creating, not only
once it completes). The validation record's own name/value cannot be a
CloudFormation Output: `AWS::CertificateManager::Certificate` exposes no
`Fn::GetAtt` attributes for it. That's why this phase reads them from the
ACM API (`aws acm describe-certificate`) instead of from `describe-stacks`
— and why `scripts/dns-runbook.sh validate` also accepts `--validation-file
PATH` as a fallback, for an operator who already pulled that JSON down some
other way (e.g. restricted IAM permissions on the machine running the
script).

### 3. Phase 2 — route the apex and www hosts

Once `infra/domain.yaml` reaches `CREATE_COMPLETE`:

```bash
scripts/dns-runbook.sh route
```

Dry-run by default, same shape as phase 1. Expected output:

```text
==> resolving regional domain targets from stack league-of-agents-platform-domain (region us-east-1)
==> apex target: d-abcdefghij.execute-api.us-east-1.amazonaws.com
==> www target: d-klmnopqrst.execute-api.us-east-1.amazonaws.com
==> ok: CNAME league-of-agents.ai -> d-abcdefghij.execute-api.us-east-1.amazonaws.com (dry-run)
==> ok: CNAME www.league-of-agents.ai -> d-klmnopqrst.execute-api.us-east-1.amazonaws.com (dry-run)
```

Commit it:

```bash
scripts/dns-runbook.sh route --apply
```

**These records must stay proxied** (`scripts/dns-runbook.sh` always passes
`--proxied` for them) — that is what puts Cloudflare's edge, and its TLS,
in front of the AWS origin at all.

### Why a CNAME at the apex works here

A `CNAME` record at a zone's apex (`league-of-agents.ai` itself, not a
subdomain) is invalid in plain DNS — it cannot coexist with the zone's
mandatory `SOA`/`NS` records. Cloudflare's proxy layer sidesteps this:
when a `CNAME` record is proxied (orange cloud), Cloudflare flattens it at
its own edge and answers apex queries with its anycast IPs directly, never
publishing an actual `CNAME` at the apex over the wire. This is a
standard, widely used Cloudflare feature (CNAME flattening) — it is why
`scripts/dns-runbook.sh route` can create a `CNAME` (not an `A` record) at
`league-of-agents.ai` at all, and why that record must stay proxied.

### 4. Verify

```bash
scripts/dns-runbook.sh verify
```

Read-only: `dig +short` for both hosts, then `curl -I` against
`https://league-of-agents.ai/` and `https://www.league-of-agents.ai/`,
checking for a `cf-ray` response header (Cloudflare-specific — its
presence confirms the request actually went through Cloudflare's edge, not
just that some HTTPS server answered).

### All-in-one

```bash
scripts/dns-runbook.sh all --apply
```

Runs phase 1, then phase 2, then verify, in sequence. If phase 2 cannot yet
resolve `infra/domain.yaml`'s outputs (the stack is still waiting on phase
1's validation to propagate), it prints why and continues on to `verify`
rather than aborting — `verify` is read-only and safe to run at any time,
including before phase 2 has run.

## Idempotence contract

`cultureflare dns create`'s own idempotency guard is keyed on
`type + name + content`: creating a record that already exists exits `1`
with an "already exists" message rather than creating a duplicate (see
`cultureflare explain dns create`). `scripts/dns-runbook.sh`'s
`cf_dns_create` wrapper treats exactly that case — exit `1` whose message
contains "already exists" — as a no-op success, not a failure. Any other
non-zero exit (missing token, bad zone, upstream error) still fails the
script loudly.

Consequence: re-running `scripts/dns-runbook.sh` — any phase, with or
without `--apply`, any number of times — after a prior successful `--apply`
run is a no-op. Every record it would create already matches exactly, so
`cultureflare` reports "already exists" for each one, the wrapper treats
that as success, and the script exits `0` having made no changes. This is
the concrete mechanism behind the spec's honesty condition h4 (see below).

Redeploying `infra/domain.yaml` itself is likewise a no-op when nothing
changed, for the same reason `infra/deploy.sh` documents for
`infra/template.yaml`: an unchanged template and unchanged parameters
produce an empty CloudFormation changeset, and `--no-fail-on-empty-changeset`
makes that exit `0` rather than an error.

## Rollback

- **infra/domain.yaml**: `sam delete --stack-name
  league-of-agents-platform-domain` (or `aws cloudformation delete-stack`).
  This removes the ACM certificate, both `AWS::ApiGatewayV2::DomainName`
  resources, and both API mappings. It does **not** touch Cloudflare — DNS
  and CloudFormation are two separate systems here by design.
- **Cloudflare DNS records**: the installed `cultureflare` CLI (see
  `cultureflare dns --help`) exposes `dns create` only — there is no `dns
  delete` verb yet. Removing the validation or routing CNAME records this
  runbook created requires either the Cloudflare dashboard or a direct
  Cloudflare API call (`DELETE /zones/{zone_id}/dns_records/{id}`) outside
  this runbook. Note the record IDs from a prior `--apply` run's
  non-dry-run JSON output (`cultureflare dns create ... --json --apply`)
  if you expect to need this.
- Rolling back phase 2 without rolling back phase 1 (i.e. un-routing
  traffic while keeping the certificate valid) is safe and independent —
  delete the apex/`www` CNAMEs and league-of-agents.ai simply stops
  resolving through Cloudflare; the certificate and custom domain resources
  are untouched.

## How this satisfies honesty condition h4

The spec (`docs/specs/2026-07-10-league-of-agents-ai-is-live-a-beautiful-welcoming.md`)
states h4 as:

> DNS and tunnel for league-of-agents.ai are created and verified by
> cultureflare commands captured in a committed runbook, and re-running the
> runbook is a no-op.

Mapped onto what is actually in this repository:

- **"created ... by cultureflare commands"**: every mutation in this
  runbook — the ACM validation CNAMEs and the apex/`www` routing CNAMEs —
  goes through `cultureflare dns create`. Nothing calls the Cloudflare API
  directly.
- **"verified by cultureflare commands captured in a committed runbook"**:
  `scripts/dns-runbook.sh` and this document are both committed to the
  repository (not run ad hoc from a shell history), and `verify` is a
  first-class phase of the same script, not a separate manual step.
- **"tunnel"**: this design does not use a Cloudflare Tunnel
  (`cloudflared`) — the origin is a public API Gateway regional custom
  domain that Cloudflare proxies to over the public internet, not a
  private tunnel. "Tunnel" in the spec's honesty condition is satisfied
  here by the proxied CNAME routing path (Cloudflare edge to AWS origin)
  that phase 2 establishes; there is no separate tunnel resource because
  the origin is already public and does not need one. Cloudflare Access
  (which `cultureflare remote-login` sets up for tunnel-style private
  hostnames) is deliberately not used, per the design decision at the top
  of this document.
- **"re-running the runbook is a no-op"**: see "Idempotence contract"
  above — this is a direct, mechanical consequence of `cultureflare dns
  create`'s own idempotency guard plus this script's exit-1-already-exists
  handling, not an assertion taken on faith.

What is **not** claimed: that this has been proven against the real
`league-of-agents.ai` zone in this task. See "Status" above.
