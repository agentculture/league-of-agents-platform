# IAM deploy/maintenance user — least-privilege runbook

Root credentials were used exactly once, as the bootstrap for the
2026-07-10 launch sweep. This runbook retires them: it creates a dedicated
deploy/maintenance user whose policy is **derived from observation, not
guesswork** — every AWS service-action pair exercised during the launch
deploys was recorded as it ran (operator-side command log) and
cross-checked against CloudTrail's management-event history for the same
window (110 unique service:action pairs observed), then validated with the
IAM policy simulator.

The policy lives at [`infra/iam-deploy-policy.json`](../../infra/iam-deploy-policy.json).

## What the policy covers

| Concern | Statements | Scope |
|---|---|---|
| Stack lifecycle (`sam deploy`, both stacks) | `StackLifecycle`, `StackDiscovery` | `league-of-agents-platform-*`, the SAM-managed artifact-bucket stack, the Serverless transform |
| Build artifacts + archive bucket | `SamArtifactsAndArchiveBuckets` | `aws-sam-cli-managed-default*`, `league-of-agents-*` buckets |
| Function roles (CAPABILITY_IAM) | `FunctionRoles`, `ServiceLinkedRoleFirstUse` | `role/league-of-agents-platform-*`, `role/aws-service-role/*` |
| Lambda code/config (incl. the cold-start probe) | `Functions`, `SamPreflightAndFirstUseServiceRoles` | `function:league-of-agents-*` |
| HTTP API + custom domains | `HttpApiAndCustomDomains` | API Gateway v2 REST-verb actions on `/apis*`, `/domainnames*` |
| DynamoDB tables + GSIs | `Tables` | `table/league-of-agents-*` (+ indexes) |
| Log groups + debugging (`aws logs tail`) | `FunctionLogGroups`, `LogGroupDiscovery` | `/aws/lambda/league-of-agents-*` |
| ACM certs (domain stack) | `DomainCertificates` | `*` (RequestCertificate cannot be resource-scoped pre-creation) |
| Cleanup schedule | `CleanupSchedule` | `rule/league-of-agents-platform-*` |
| $20 budget alarm | `BudgetCeiling`, `BudgetResourceApi` | account budgets (API requires `*`) |
| OAuth secrets (next iteration, platform#6) | `OauthSecretsNextIteration` | `parameter/league/*` |
| Identity + audit | `IdentityAndAudit` | `sts:GetCallerIdentity`, `cloudtrail:LookupEvents` |

Deliberately **excluded** despite appearing in the observation window, with
reasons: `logs:CreateLogStream` (the function role's own runtime writes,
not the operator's), `sts:AssumeRole` + `signin:*` (session plumbing),
`ec2:DescribeVpcs` / `glue:GetDatabases` / `iam:GetAccountSummary` /
`iam:ListRoles` (console browsing noise from the same session),
`kms:*` (DynamoDB/SSM call KMS with service grants, not operator
credentials).

## Validation

`aws iam simulate-custom-policy` over the 20 representative deploy-path
action/resource pairs (changeset lifecycle, artifact upload, role
create/pass, function code+config update, API+domain writes, table
create/GSI update, log retention/tail, cert request, schedule, budget,
SSM parameter, CloudTrail audit): **all allowed**. Two out-of-scope
probes (`dynamodb:CreateTable` on an unrelated table, `iam:CreateRole` on
an unrelated role): **implicitly denied**.

Re-run the validation any time the policy changes — the simulation script
pattern is one `aws iam simulate-custom-policy --policy-input-list
"$(cat infra/iam-deploy-policy.json)" --action-names <action>
--resource-arns <arn>` call per pair.

## Create the user (run once, as root or an IAM admin)

```bash
aws iam create-user --user-name league-deploy
aws iam put-user-policy \
  --user-name league-deploy \
  --policy-name league-deploy-least-privilege \
  --policy-document file://infra/iam-deploy-policy.json
aws iam create-access-key --user-name league-deploy   # store in your credential manager
```

Configure locally (`aws configure --profile league-deploy`), then deploy with:

```bash
AWS_PROFILE=league-deploy bash infra/deploy.sh prod <budget-email>
```

## Rotation and root retirement

- Rotate keys: `aws iam create-access-key` (new) → update the credential
  store → `aws iam delete-access-key` (old). Two keys may coexist during
  the swap; never keep two active outside a rotation window.
- After the first successful `league-deploy` deploy, stop using root for
  this project entirely; root stays for IAM administration only.
- The policy names account `435593604218` and region `us-east-1`
  explicitly — a fork deploying elsewhere edits those constants.

## Observation appendix

The raw observation set this policy was derived from (110 unique
service:action pairs, CloudTrail management events over the launch
window) is reproducible with `aws cloudtrail lookup-events --start-time
<window-start>` — extract `eventSource` + `eventName` per event and
de-duplicate.
