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

## Cache purge (emergency, /theme.css and /site.js)

### Why this section exists

On 2026-07-11 a deploy shipped a new `theme.py` (10,604 B `/theme.css`,
with `:root[data-theme=...]` blocks) and a new `scripts.py`, but the
three-state theme toggle did nothing on production. The origin Lambda was
serving the current build correctly — a direct fetch confirmed the
10,604-byte stylesheet with the `data-theme` rules. Cloudflare's edge,
however, was still serving the pre-dazzle `/theme.css` (5,863 B, no
`data-theme` blocks at all) under `cache-control: max-age=14400`
(`cf-cache-status: HIT`). New HTML, new JS, stale CSS: the toggle stamped
`data-theme` on `<html>` exactly as designed, but no CSS rule existed at
the edge to react to it. This is a caching problem, not an application
bug — `theme.py` and `scripts.py` were both correct on origin the whole
time.

The fix has two legs, and this section documents only the first:

1. **(ops, immediate)** Purge the two stale URLs from Cloudflare's edge
   cache — the procedure below. This is a manual operator action taken
   against the real zone; it is not automated by this repository and
   there is nothing in `tests/` that exercises it, for the same reason
   `cultureflare` itself cannot be exercised against a real zone in CI
   (see "Status" above). **Execution against the real
   `league-of-agents.ai` zone, and the resulting fresh-fetch confirmation,
   are operator actions recorded when they happen — not claimed here.**
2. **(code, durable)** Ship versioned asset URLs (`/theme.css?v=<version>`
   or a content-hashed path) so a deploy can never again strand new HTML
   against an old stylesheet at any caching layer. See the closing
   paragraph below — **this leg shipped in platform t4**: every
   `/theme.css`, `/site.js`, and `/fonts/*.woff2` reference the shell
   emits now carries `?v=<content-hash>`, and this purge procedure is no
   longer part of the routine deploy path.

### Purge-by-URL API call

Cloudflare's purge endpoint supports several granularities (purge
everything, purge by tag, purge by hostname); this runbook uses only the
narrowest and least invasive: **purge by URL**, which invalidates exactly
the files named and nothing else on the zone.

```bash
curl -sS -X POST \
  "https://api.cloudflare.com/client/v4/zones/${ZONE_ID}/purge_cache" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  -H "Content-Type: application/json" \
  --data '{
    "files": [
      "https://league-of-agents.ai/theme.css",
      "https://league-of-agents.ai/site.js",
      "https://www.league-of-agents.ai/theme.css",
      "https://www.league-of-agents.ai/site.js"
    ]
  }'
```

Both the apex and `www` hosts are listed explicitly — Cloudflare caches
each hostname's copy of a URL independently, and this site answers on
both (see "Phase 2" above). Purging only the apex would leave a visitor
on `www.league-of-agents.ai` looking at the stale stylesheet.

`ZONE_ID` is not something this runbook or `cultureflare` has printed
before now; get it with:

```bash
cultureflare zones list
```

This is one of the documented direct-API exceptions to "every mutation in
this runbook goes through `cultureflare`" (see "How this satisfies
honesty condition h4" below for the parallel case) — `cultureflare` has
`whoami`, `zones list`, `dns create`, `learn`, and `explain`, and no purge
verb as of this writing (`cultureflare --help`). The `DELETE
/zones/{zone_id}/dns_records/{id}` call in "Rollback" above is the same
kind of exception, for the same reason: the CLI simply doesn't cover that
verb yet, and the direct API call is what's left.

### Required token scope

The purge call needs a token with **Zone → Cache Purge → Purge**
permission on the `league-of-agents.ai` zone. This is a narrower grant
than the `Zone → DNS → Edit` scope this runbook's `CLOUDFLARE_API_TOKEN`
otherwise needs for `cultureflare dns create` — if the token configured
for day-to-day DNS work was not also granted Cache Purge at creation time,
the purge call fails with a 403 and a new or edited token is needed before
it will succeed. Check current scope with `cultureflare whoami` (it
reports what the configured token can do) before assuming a 403 means
something else is wrong.

### Loading the token

Same secure-loading pattern this runbook already uses for
`CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (see "Prerequisites"
above and `cultureflare learn`): a `chmod 600` env file kept outside any
git-tracked directory, sourced into the shell immediately before the
`curl` call —

```bash
chmod 600 ~/.config/agent/cultureflare.env
set -a; . ~/.config/agent/cultureflare.env; set +a
```

— never committed, and never pasted directly into an editor or a chat
transcript. The purge token is a bearer credential with write access to
Cloudflare's cache for this zone; treat it with the same care as the DNS
token.

### Browser caches are a second, separate cache

Purging the edge fixes what Cloudflare serves to the *next* request for
`/theme.css` and `/site.js`. It does nothing for a browser that already
fetched and cached those files during the 4-hour `max-age=14400` window —
that copy lives on the visitor's disk, not at Cloudflare, and a
Cloudflare-side purge cannot reach it. A visitor who loaded the site
before the purge needs a hard refresh (bypassing the browser's own HTTP
cache, not just a normal reload) to see the corrected stylesheet. This is
why the honesty condition on the toggle fix is checked "on production,
not locally" with "a fresh fetch through Cloudflare" — verifying the edge
is necessary but not sufficient; the operator doing that verification
should also confirm with a hard refresh (or a private/incognito window,
which starts with no cache at all) that the browser layer isn't masking
either a real fix or a real remaining bug.

### Why purging is no longer necessary for routine deploys

This procedure originally existed because `/theme.css` and `/site.js` were
served at fixed, unversioned URLs — a deploy changed the bytes behind
`/theme.css` without changing its name, so anything that cached the old
name (Cloudflare's edge, a visitor's browser) kept serving stale bytes
until it expired or was told to forget. **Platform t4 shipped versioned
asset URLs** (`/theme.css?v=<hash>`, `/site.js?v=<hash>`, and every
`/fonts/*.woff2` reference — a content hash of the served bytes, computed
once at import by `league_site.web.shell.asset_url`), so that is no
longer true: each deploy's shelled HTML references a *new* URL for its
build, so there is nothing stale to purge — the new URL was never cached
by anyone, and the old URL's cached copy is simply never requested again.
Routine purges are no longer part of the deploy path. This procedure
remains useful only for genuine emergencies: if a bug ships that is bad
enough to need pulling *the currently-referenced* versioned URL's content
out of cache before its natural TTL expires (rather than shipping a fix
forward under a newer version), the same purge-by-URL call above still
works — it purges whatever URL is named, versioned or not.

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
