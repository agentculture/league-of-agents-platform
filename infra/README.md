# infra

AWS SAM deploy stack for the League of Agents platform.

- `template.yaml` — the SAM template (Lambda + HTTP API + DynamoDB + S3 +
  Budget alarm).
- `Makefile` — the Lambda function's custom `sam build` step (this project
  uses `uv`/`pyproject.toml`, not `requirements.txt`).
- `deploy.sh` — one-command `sam build && sam deploy` wrapper.

Full operator documentation (prerequisites, first deploy, redeploy,
teardown, where state lives) is in
[`../docs/deploy.md`](../docs/deploy.md) — read that, not this file, before
running anything here.
