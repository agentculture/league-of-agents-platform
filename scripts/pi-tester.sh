#!/usr/bin/env bash
# pi + Nemotron tester (plan task t14).
#
# Runs pi (pi.dev) on the lobes gateway model "worker" (Nemotron 3.5 Super) as an
# outside agent against a LOCAL server: it reads the agent docs, plays a match
# through the API with a local-only token, and checks the /evals/ pages and data.
# It also makes read-only requests to production /evals/ and the leaderboard, and
# compares the production leaderboard before and after the run.
#
# Usage: scripts/pi-tester.sh <evals-export-dir> [log-file]
# Needs: uv, pi on PATH with the lobes provider configured, curl.
# The tester gets the bash tool only (no edit/write); the local stores are in
# memory, so nothing it does can change production ratings.
set -euo pipefail

DATA=${1:?usage: scripts/pi-tester.sh <evals-export-dir> [log-file]}
LOG=${2:-docs/verification/$(date -u +%Y-%m-%d)-pi-nemotron-tester.md}
PORT=${PORT:-8770}
MODEL=${PI_MODEL:-lobes/worker}
PROD=https://league-of-agents.ai
WORK=$(mktemp -d)
trap 'kill "$SERVER_PID" 2>/dev/null || true; rm -rf "$WORK"' EXIT

uv run python scripts/evals-local-serve.py --data "$DATA" --port "$PORT" >"$WORK/serve.json" 2>"$WORK/serve.log" &
SERVER_PID=$!
for _ in $(seq 1 40); do [ -s "$WORK/serve.json" ] && break; sleep 0.25; done
URL=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["url"])' "$WORK/serve.json")
TOKEN=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["token"])' "$WORK/serve.json")
EVAL=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["evals"][0]["id"])' "$DATA/index.json")

curl -s "$PROD/api/v1/leaderboard?limit=50" >"$WORK/lb-before.json" || true

cat >"$WORK/prompt.md" <<EOF
You are an outside AI agent trying League of Agents for the first time. A local copy of
the site runs at $URL. Use only the bash tool with curl. Never print the token below in
your report. Do not modify any files.

Your agent token for the LOCAL site (Bearer): $TOKEN

Do these in order and keep notes of what worked, what confused you, and any errors:
1. Read $URL/llms.txt and $URL/agents.md. Summarise in two sentences how an agent is
   supposed to join and play.
2. Following those docs, create a match through the HTTP API on the local site with your
   token and take turns until the match ends or 10 turns pass. Report the match id, how
   many turns you took, and any request that failed with its status and error code.
3. Open the evals pages: GET $URL/evals/ and $URL/evals/$EVAL/hard-a/ (check status codes
   and that the replay page has labelled Previous/Play/Next controls), then the data:
   $URL/evals/data/$EVAL/manifest.json, $URL/evals/data/$EVAL/summary.json, and one match
   file named in the manifest. From the data, report: each match's status and reason, the
   GLM and Sonnet pooled rejected-order counts, which team publishes reasoning text and
   which does not, and whether any match file is missing a reason.
4. Read-only production check: curl -sI $PROD/evals/ and report the status line only.

Finish with a section headed "Report" containing a JSON object:
{"onboarding_clear": bool, "match_played": bool, "turns_taken": int, "api_errors": [..],
 "evals_pages_ok": bool, "data_ok": bool, "findings": [short strings], "confusions": [..]}
EOF

START=$(date -u +%Y-%m-%dT%H:%M:%SZ)
set +e
pi -p --model "$MODEL" --tools bash --no-mcp "$(cat "$WORK/prompt.md")" >"$WORK/pi.out" 2>"$WORK/pi.err"
PI_EXIT=$?
set -e
END=$(date -u +%Y-%m-%dT%H:%M:%SZ)

curl -s "$PROD/api/v1/leaderboard?limit=50" >"$WORK/lb-after.json" || true
if cmp -s "$WORK/lb-before.json" "$WORK/lb-after.json"; then LB="unchanged"; else LB="CHANGED"; fi

mkdir -p "$(dirname "$LOG")"
{
  echo "# pi + Nemotron tester run"
  echo
  echo "- Model: \`$MODEL\` via pi $(pi --version 2>/dev/null | head -1), tools: bash only, MCP off"
  echo "- Local site: $URL (in-memory stores, local-only token owned by github:local-tester)"
  echo "- Eval data: \`$EVAL\` from $DATA"
  echo "- Started $START, finished $END, pi exit code $PI_EXIT"
  echo "- Production leaderboard (top 50) before vs after: **$LB**"
  echo
  echo "## Tester output"
  echo
  echo '```text'
  sed "s/$TOKEN/[local token]/g" "$WORK/pi.out"
  echo '```'
  if [ -s "$WORK/pi.err" ]; then
    echo
    echo "## pi stderr (last 40 lines)"
    echo
    echo '```text'
    tail -40 "$WORK/pi.err" | sed "s/$TOKEN/[local token]/g"
    echo '```'
  fi
} >"$LOG"
echo "wrote $LOG (pi exit $PI_EXIT, leaderboard $LB)"
