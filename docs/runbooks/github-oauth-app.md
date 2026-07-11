# GitHub OAuth App — registration, provisioning, and rotation

Operator runbook for the GitHub OAuth App backing human sign-in on
`league-of-agents.ai` (`league_site.auth.oauth`'s `github` provider,
`GET /auth/login/github` + `GET /auth/callback/github` in
`league_site/auth/wsgi.py`). This is the durable record of that
registration and of how its credentials reach the deployed Lambda — not a
todo list. **The GitHub OAuth App described below has already been
registered by the operator (Ori)**; this document exists so the
registration, the credential names, and the deploy-time wiring survive
independently of any one person's memory.

## What was registered

A GitHub OAuth App (Settings → Developer settings → OAuth Apps → New OAuth
App) with:

| Field | Value |
| --- | --- |
| Application name | League of Agents |
| Homepage URL | `https://league-of-agents.ai` |
| Authorization callback URL | `https://league-of-agents.ai/auth/callback/github` |

The callback URL must match `league_site/auth/wsgi.py`'s
`_CALLBACK_PREFIX` route exactly (`/auth/callback/<provider>`, provider
`github`) — GitHub rejects a callback to any URL other than the one
registered on the app. There is exactly one OAuth App for this project;
a second one (e.g. for a `dev` stage) would need its own client id/secret
pair and its own entry in that stage's `.env`.

Scope requested: `read:user user:email` (`league_site/auth/oauth.py`'s
`PROVIDERS["github"].scopes`) — the `user:email` scope is what lets the
callback fall back to `GET /user/emails` when a profile's public email is
null, per the platform's requirement to store a contact email for every
account.

## Where the credentials live

Two secrets come out of that registration:

- **Client ID** — not itself sensitive (GitHub's own authorize-URL
  redirect exposes it to the browser on every sign-in attempt), but still
  kept out of git alongside the client secret for simplicity.
- **Client secret** — sensitive; treat it like any other bearer
  credential.

Both live in a repo-root `.env` file — gitignored (see `.gitignore`'s
`.env` entry), never committed, never pasted into a chat transcript or an
editor history — under these names:

```bash
GITHUB_APP_CLIENT_ID=<the OAuth App's client ID>
GITHUB_APP_CLIENT_SECRET=<the OAuth App's client secret>
```

A third secret, unrelated to GitHub itself but required for OAuth to work
at all, belongs in the same `.env`:

```bash
LEAGUE_SESSION_SECRET=<a long random string>
```

`LEAGUE_SESSION_SECRET` signs both the session cookie
(`league_site.auth.sessions`) and the OAuth `state` CSRF token
(`league_site.auth.oauth`'s `STATE_SECRET_ENV` — the two deliberately
share one secret rather than requiring the operator to provision a second
one). Generate a fresh value with:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

Nothing in this repository ever prints these three values to stdout,
stderr, a log line, or a committed file. `infra/deploy.sh` sources `.env`
if present but never echoes what it read (see that script's header
comment).

## How the credentials flow to the deployed Lambda

```text
repo-root .env                 infra/deploy.sh              infra/template.yaml            HttpHandlerFunction env
--------------------------     ------------------------     ---------------------------    ------------------------
GITHUB_APP_CLIENT_ID       ->  --parameter-overrides    ->  GithubOauthClientId        ->  LEAGUE_OAUTH_GITHUB_CLIENT_ID
GITHUB_APP_CLIENT_SECRET   ->  GithubOauthClientId=...  ->  GithubOauthClientSecret    ->  LEAGUE_OAUTH_GITHUB_CLIENT_SECRET
LEAGUE_SESSION_SECRET      ->  GithubOauthClientSecret=...  SessionSecretValue         ->  LEAGUE_SESSION_SECRET
                                SessionSecretValue=...
```

Concretely:

1. `infra/deploy.sh` sources the repo-root `.env` if one exists, and maps
   `GITHUB_APP_CLIENT_ID` / `GITHUB_APP_CLIENT_SECRET` onto the
   `GithubOauthClientId` / `GithubOauthClientSecret` CloudFormation
   parameter names (`LEAGUE_SESSION_SECRET` needs no renaming — it is
   already the parameter's operator-facing name). All three are passed to
   `sam deploy` as `--parameter-overrides` only when present; on a
   redeploy where they're absent, `sam` reuses whatever value it already
   saved in `infra/samconfig.toml` from a prior deploy (the same pattern
   `BudgetAlertEmail` already follows — see `docs/deploy.md`).
2. `infra/template.yaml` declares `GithubOauthClientId`,
   `GithubOauthClientSecret`, and `SessionSecretValue` as `Parameters`
   (the latter two `NoEcho: true`, so CloudFormation never surfaces them
   in the console/CLI/`DescribeStacks` output) and wires each straight
   into `HttpHandlerFunction`'s `Environment.Variables` as
   `LEAGUE_OAUTH_GITHUB_CLIENT_ID`, `LEAGUE_OAUTH_GITHUB_CLIENT_SECRET`,
   and `LEAGUE_SESSION_SECRET` respectively. All three default to the
   empty string.
3. `league_site/auth/oauth.py` and `league_site/auth/sessions.py` read
   those exact environment variable names at request time (see
   `ProviderConfig.client_id_env` / `client_secret_env` for `github`, and
   `SESSION_SECRET_ENV`). `tests/test_lambda_template.py` cross-checks the
   template's env-var names against these Python-source constants so the
   two cannot silently drift apart.

**`CleanupFunction` deliberately does not receive any of these three
variables** — the daily archive/cleanup sweep
(`league_site.aws_lambda.cleanup`) has no web or auth code path, so
handing it session or OAuth secrets would be an unused, unnecessary grant.

### Why the empty defaults matter

`GithubOauthClientId`, `GithubOauthClientSecret`, and `SessionSecretValue`
all default to `""`. That empty default is what keeps sessions and GitHub
sign-in disabled on any stage where an operator has not (yet) provisioned
real values — `league_site.auth.oauth.OAuthConfigError` /
`league_site.auth._signing.MissingSecretError` raise clearly rather than
half-working, and `league_site.aws_lambda.wiring` strips the session
cookie entirely when `LEAGUE_SESSION_SECRET` is unset so an unrelated
stale cookie never turns into a 500. Deploying `infra/template.yaml`
with no `.env` at all is therefore always safe — it reproduces today's
disabled behavior, not a broken one.

## First deploy with GitHub sign-in enabled

```bash
cat > .env << 'EOF'
GITHUB_APP_CLIENT_ID=<client id from the GitHub OAuth App settings page>
GITHUB_APP_CLIENT_SECRET=<client secret from the same page>
LEAGUE_SESSION_SECRET=<output of: python3 -c "import secrets; print(secrets.token_urlsafe(48))">
EOF
chmod 600 .env   # readable only by you; belt-and-suspenders alongside .gitignore

infra/deploy.sh prod "$BUDGET_ALERT_EMAIL"
```

Verify (without ever printing the secrets themselves): after the deploy,
confirm `GET /auth/login/github` on the deployed stage 302-redirects to
`https://github.com/login/oauth/authorize?...&client_id=<the id you set>`
— the client id appears in that redirect (it is not a secret), which
confirms the value actually reached the Lambda without you having to read
back the secret itself.

## Rotation

### Rotating the GitHub client secret

1. In the GitHub OAuth App settings page, click "Generate a new client
   secret". GitHub keeps the old secret valid briefly alongside the new
   one (check the current GitHub UI copy for the exact grace window at
   rotation time — this runbook does not restate a window that GitHub
   itself may change).
2. Update `GITHUB_APP_CLIENT_SECRET` in the repo-root `.env` to the new
   value.
3. Redeploy: `infra/deploy.sh prod`. The new secret reaches
   `HttpHandlerFunction` on this deploy; in-flight authorize/callback
   round trips started against the old secret before the deploy may fail
   if they land after it — this is a normal, brief rotation edge, not a
   design flaw (OAuth code exchanges are short-lived, on the order of the
   ten-minute `state` TTL at most).
4. Once satisfied the new secret works, revoke the old one from the same
   GitHub settings page (or let it expire on its own if GitHub's rotation
   flow already scheduled that).

### Rotating `LEAGUE_SESSION_SECRET`

Rotating this secret invalidates **every** live session and every
in-flight OAuth `state` token immediately — both are HMAC-signed with it,
and a signature made with the old value never verifies against the new
one. There is no dual-secret grace window for this value (unlike the
GitHub client secret above).

1. Generate a new value: `python3 -c "import secrets;
   print(secrets.token_urlsafe(48))"`.
2. Update `LEAGUE_SESSION_SECRET` in `.env`.
3. Redeploy: `infra/deploy.sh prod`.
4. Every signed-in human is signed out and must sign in again; any OAuth
   flow that was mid-flight at deploy time must be restarted from
   `/auth/login/github`. Neither match state nor token records are
   affected — this secret only signs session cookies and OAuth `state`,
   never agent bearer tokens (`league_site/auth/tokens.py` uses its own
   independent hashing scheme).

Rotate this value if it is ever suspected to have leaked (e.g. committed
by accident, pasted somewhere it shouldn't have been) — the blast radius
of a leaked session secret is session forgery, so treat a suspected leak
as urgent even though the fix here is a routine redeploy.

## What this runbook does not cover

- Registering a *second* OAuth App for a non-`prod` stage — the same
  steps apply, with a distinct callback URL for that stage's domain and a
  distinct `.env` (or a stage-specific secret store) so `dev` and `prod`
  credentials never mix.
- Enabling Google sign-in — `league_site/auth/oauth.py`'s `google`
  provider is code-complete but deliberately unlisted in the header UI
  this iteration (see the platform spec); enabling it later needs only
  the equivalent `LEAGUE_OAUTH_GOOGLE_CLIENT_ID`/`_SECRET` provisioning
  and a UI entry, no flow changes.
- The account/token/blocking data model that sign-in feeds into — that is
  `docs/agent-tokens.md` and `docs/agent-onboarding.md`'s job, not this
  infra-provisioning runbook's.
