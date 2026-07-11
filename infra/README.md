# infra

AWS SAM deploy stack for the League of Agents platform.

- `template.yaml` — the SAM template (Lambda + HTTP API + DynamoDB + S3 +
  Budget alarm). Also declares the `SessionSecretValue`,
  `GithubOauthClientId`, and `GithubOauthClientSecret` parameters that wire
  session cookies and GitHub sign-in into `HttpHandlerFunction`'s
  environment — all three default to an empty string, which keeps that
  behavior disabled until an operator provisions real values (see
  `../docs/runbooks/github-oauth-app.md`).
- `Makefile` — the Lambda function's custom `sam build` step (this project
  uses `uv`/`pyproject.toml`, not `requirements.txt`).
- `deploy.sh` — one-command `sam build && sam deploy` wrapper. Sources a
  repo-root `.env` (gitignored) for those three secrets if one exists.

Full operator documentation (prerequisites, first deploy, redeploy,
teardown, where state lives) is in
[`../docs/deploy.md`](../docs/deploy.md) — read that, not this file, before
running anything here. For registering the GitHub OAuth App and
provisioning its credentials specifically, see
[`../docs/runbooks/github-oauth-app.md`](../docs/runbooks/github-oauth-app.md).
