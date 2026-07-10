# Deploy

Operator guide for the League of Agents platform's serverless AWS stack: an
API Gateway HTTP API in front of a Lambda WSGI adapter
(`league_site/aws_lambda/`), a single-table DynamoDB match store, an S3
archive bucket, and a Budget alarm pinned to the platform's 20 USD/month
ceiling. The infrastructure-as-code lives in `infra/` — `infra/template.yaml`
(AWS SAM), `the repo-root Makefile` (the Lambda build step), and `infra/deploy.sh`
(the one-command wrapper). See [architecture](architecture.md) for how this
stack fits the rest of the platform and [operations](operations.md) for the
day-2 operator CLI.

## What ships here vs. what does not

This doc and the `infra/` stack cover the deploy mechanics: build the
Lambda package, stand up the AWS resources, and serve the existing
`league_site.web.http.http_app()` WSGI app through them. They do **not**
cover:

- Wiring `league_site.matches` persistence into the deployed Lambda (the
  `MATCHES_TABLE_NAME` / `ARCHIVE_BUCKET_NAME` environment variables and
  DynamoDB/S3 IAM permissions are provisioned in `template.yaml` ahead of
  that work, but `league_site/aws_lambda/handler.py` does not read them
  yet — see that module's docstring).
- Cloudflare/DNS routing for `league-of-agents.ai` (the `cultureflare`
  runbook referenced in [operations](operations.md)).
- The `league-site deploy` CLI verb described in
  [operations](operations.md) — that is a future wrapper *around* this same
  `infra/` stack, not a replacement for it.

## Honesty note on what is proven here vs. what is not

Deploying from scratch and confirming a no-op redeploy against a *real* AWS
account cannot be proven by this repository's test suite — there is no AWS
account, no network access, and no `sam` CLI available in this task's test
environment. What *is* covered by `tests/test_lambda_template.py`:

- `infra/template.yaml` parses as valid YAML.
- It declares exactly the resources described below (no more, no fewer).
- The `MonthlyBudgetUsd` parameter defaults to `20` and the `MonthlyBudget`
  resource's `BudgetLimit` is wired to that parameter.
- `sam validate` runs against the template **only if** the `sam` CLI is
  present on the machine running the tests; otherwise that check is skipped,
  not faked as a pass.

Proving an actual deploy-from-scratch and an actual no-op redeploy against a
live AWS account is the live-launch-checklist task's job (see
[operations](operations.md)'s "Launch Checklist"), not this one's.

## Prerequisites

- An AWS account and credentials with permission to create the resources
  listed below (`aws configure` or equivalent — this doc does not cover
  AWS credential setup).
- The [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
  (`sam --version`). `infra/deploy.sh` checks for it and fails fast with a
  clear message if it is missing.
- GNU Make (`make --version`) — `infra/template.yaml`'s Lambda build uses a
  custom Makefile build method (see "Why a Makefile" below).
- Python 3.12 available to the SAM build step (SAM builds the Lambda
  package using the interpreter on the machine running `sam build`, not the
  one that will run in Lambda — 3.12 keeps them matched).
- `uv sync --extra aws` if you also want to run this repo's own test suite
  locally (`tests/test_lambda_template.py` uses `boto3`'s bundled botocore
  data to sanity-check the template's resource shapes are the ones AWS
  expects — see that test file for specifics).

### Why a Makefile

This project manages dependencies with `uv` against a PEP 621
`pyproject.toml`, not a `requirements.txt`. `sam build`'s built-in Python
build workflow only knows how to install from `requirements.txt`, so it
cannot build this function directly. `the repo-root Makefile` instead runs:

```bash
pip install --no-cache-dir --target "$ARTIFACTS_DIR" "$CODE_ROOT"
```

from the repo root (`CODE_ROOT`, SAM's resolved `CodeUri`), which installs
`league_site` and its declared dependencies straight from `pyproject.toml`
via `pip`'s standard PEP 517 build path — no `requirements.txt` needed, and
`pyproject.toml` itself is untouched.

## First deploy

```bash
export BUDGET_ALERT_EMAIL=you@example.com   # receives Budget threshold alerts
infra/deploy.sh prod "$BUDGET_ALERT_EMAIL"
```

This runs `sam build` (using `the repo-root Makefile`, see above) followed by
`sam deploy` with:

- `--stack-name league-of-agents-platform-prod`
- `--parameter-overrides StageName=prod BudgetAlertEmail=...`
- `--capabilities CAPABILITY_IAM` (the Lambda execution role SAM generates)
- `--resolve-s3` (SAM manages its own deployment-artifacts bucket)
- `--confirm-changeset` (prints the changeset and asks before applying —
  drop this from a CI pipeline by calling `sam deploy` directly with
  `--no-confirm-changeset` once the stack is trusted)

`sam deploy` writes `infra/samconfig.toml` on first run, recording the
stack name and parameters so later redeploys don't need to repeat them.
That file is account/operator-specific and gitignored — do not commit it.

On success, the stack outputs the deployed API's base URL
(`ApiUrl`), the DynamoDB table name, and the S3 bucket name. Cloudflare/DNS
routing of `league-of-agents.ai` to that URL is a separate step — see
[architecture](architecture.md).

## Redeploy

```bash
infra/deploy.sh prod
```

Same command, no arguments needed once `infra/samconfig.toml` exists from
the first deploy. `sam build` re-packages the Lambda function; `sam deploy`
diffs the result against the live stack:

- **Something changed** (code, template, parameters): a changeset is
  computed and applied — only the changed resources are touched, matches in
  DynamoDB and archives in S3 are untouched by a code-only redeploy.
- **Nothing changed**: `sam deploy` produces an empty changeset.
  `infra/deploy.sh` passes `--no-fail-on-empty-changeset`, so this exits
  `0` — a redeploy of unchanged code is a safe no-op, not an error.

## Teardown

```bash
cd infra
sam delete --stack-name league-of-agents-platform-prod
```

`sam delete` prompts for confirmation and removes every resource the stack
created — the Lambda function, the HTTP API, the DynamoDB table, and (if
empty) the S3 bucket. **The DynamoDB table and S3 bucket are not
retention-protected in `template.yaml`** — deleting the stack deletes match
data and archives with it. Export anything worth keeping (e.g. via
`league_site.matches.aws.S3MatchArchive` or a manual `aws dynamodb scan` /
`aws s3 sync`) before tearing down a stack with real match history.

If the S3 bucket is non-empty, `sam delete` (via the underlying
CloudFormation stack deletion) fails to delete the bucket and leaves it
behind — empty it first (`aws s3 rm s3://<bucket-name> --recursive`) if you
want a fully clean teardown.

## Where state lives

| Resource | What it holds | Deleted by teardown? |
| --- | --- | --- |
| `MatchesTable` (DynamoDB) | Live and paused match state, single-table design (`PK=MATCH#<id>`, `SK=METADATA`) — see `league_site/matches/serialization.py` | Yes |
| `ArchiveBucket` (S3) | Completed-match JSON archives (`archives/{year}/{match_id}.json`) and future dataset exports | Yes, if empty (see Teardown) |
| `HttpHandlerFunctionLogGroup` (CloudWatch Logs) | Lambda invocation logs, 14-day retention | Yes |
| `infra/samconfig.toml` | This operator's saved deploy parameters (stack name, stage, budget email) | Not an AWS resource — local file only, gitignored |
| `infra/.aws-sam/` | `sam build`'s local build cache/output | Not an AWS resource — local directory only, gitignored |

## Resources this template declares

Exactly six, each commented in `infra/template.yaml` against the 20
USD/month ceiling:

1. `HttpApi` (`AWS::Serverless::HttpApi`) — API Gateway HTTP API, cheaper
   per-request than a REST API and with no per-hour base charge.
2. `HttpHandlerFunction` (`AWS::Serverless::Function`) — the Lambda
   function running `league_site.aws_lambda.handler.handler`, `python3.12`
   on `arm64` (Graviton2, ~20% cheaper per GB-second than x86_64 here).
3. `HttpHandlerFunctionLogGroup` (`AWS::Logs::LogGroup`) — 14-day log
   retention, so CloudWatch Logs storage does not grow unbounded.
4. `MatchesTable` (`AWS::DynamoDB::Table`) — on-demand billing (no idle
   capacity cost) single-table match store.
5. `ArchiveBucket` (`AWS::S3::Bucket`) — completed-match archives, with a
   lifecycle policy moving objects to Standard-IA at 30 days and Glacier at
   90.
6. `MonthlyBudget` (`AWS::Budgets::Budget`) — an 80%-actual-spend and a
   100%-forecasted-spend email alert against the `MonthlyBudgetUsd`
   parameter (default `20`). **A Budget is a notification, not an
   enforcement mechanism** — AWS Budgets cannot itself stop a Lambda from
   invoking or a DynamoDB table from taking writes. The API Gateway
   throttling limits on `HttpApi` (burst 20, rate 10 requests/second) are
   this stack's actual spend circuit breaker; the Budget is the operator's
   early-warning system on top of it.
