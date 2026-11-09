# pi + Nemotron tester run

- Model: `lobes/worker` via pi 1.5.0, tools: bash only, MCP off
- Local site: http://127.0.0.1:8770 (in-memory stores, local-only token owned by github:local-tester)
- Eval data: `glm-vs-claude-2026-11` from <local export of the prototype bundle>
- Started 2026-11-09T04:42:01Z, finished 2026-11-09T04:49:02Z, pi exit code 0
- Production leaderboard (top 50) before vs after: **unchanged**

## Tester output

```text
All steps complete. Production returns `HTTP/2 404`. I have everything needed. Writing the final report.

---

## Notes

**Step 1 — joining/playing (from /llms.txt and /agents.md):**
- A human signs in with GitHub and mints a bearer token for the agent via `POST /auth/agents` (name, model, provider); the plaintext token is shown exactly once.
- The agent sends it as `Authorization: Bearer <token>` on `/api/v1` requests, creates a match with `POST /api/v1/matches` (e.g. `{"mode": "solo-vs-bot"}`), then submits turns with `POST /api/v1/matches/{id}/turns` (`{"action": ...}`); the engine validates each move and auto-completes the match when the game is over.

**Step 2 — match played:**
- `POST /api/v1/matches` with `{"mode": "solo-vs-bot"}` → **500 `{"code": "internal_error", "message": "internal server error"}`** (failed; retried, same result).
- Created with no `mode` (defaults to `stub-duel`) → **201**, match `f17543d7f16f4bd399abd758bd541a40`, solo stub-duel (target 10, legal actions {1,2,3}, same participant takes every turn).
- Took 4 turns: points 3, 3, 3, 1 → score 10 → match **completed**, winner = me (within the 10-turn cap).
- (A second `stub-duel` match `6bc1c18ff0ac489496204f0678afa451` was created as a mode-check and left unplayed.)

**Step 3 — evals:**
- `GET /evals/` → **200**; `GET /evals/glm-vs-claude-2026-11/hard-a/` → **200**; replay page has labelled controls: `aria-label="Previous turn"`, `"Play"`, `"Next turn"`.
- `manifest.json` → **200**, 6 matches. `summary.json` → **200**. Match file `hard-a.a4ac7412.json` → **200**.
- Match statuses/reasons (from summary.json):
  - thief-a6 — live — "Rerun of a5 with every harness fix, plus the burden rule."
  - thief-a5 — tie — "Stopped at turn 12, Sonnet 17-11, called a tie: mission point values were never shown."
  - thief-a4 — tie — "Stopped at turn 24, 29-29, called a tie: stale enemy sightings were shown as current."
  - opus-a — queued — "Same rules as a6 on a new seed. Starts when a6 ends."
  - thief-a — void — "Void at turn 24: the harness lost GLM's orders on malformed message JSON."
  - hard-a — finished — "Stopped by the operator at turn 32 as a GLM win, 42-22."
- Pooled rejected-order counts (summary `pooled`): GLM (blue/GLM TensorFold) = **7**; Sonnet (red) = **57** (verified by summing per-match rejected: 0+3+1+2+1=7 and 9+11+16+12+9=57).
- Reasoning text: blue GLM TensorFold `"reasoning": "full"` → **publishes** reasoning; red Sonnet 5.5 medium `"reasoning": "none"` → **does not**.
- Match-file reason check: sampled `hard-a.a4ac7412.json` includes `"reason"`; no missing reason found in the sampled file, and all 6 summary entries carry reasons.

**Step 4 — production read-only:**
- `curl -sI https://league-of-agents.ai/evals/` → `HTTP/2 404` (evals index not served at that path in production).

**Confusions:** the docs' example create body `{"mode": "solo-vs-bot"}` returned 500 on the local site (docs led me to expect a structured 400 `unknown_mode` at worst); the running deployment's registry serves `stub-duel`, so it's unclear whether `solo-vs-bot` is misconfigured locally or just crashes. Also, the token was handed to me out of band — I couldn't perform the human GitHub sign-in/mint flow locally since there's no sign-in surface on the local copy.

---

## Report

```json
{
  "onboarding_clear": true,
  "match_played": true,
  "turns_taken": 4,
  "api_errors": [
    { "request": "POST /api/v1/matches", "body": { "mode": "solo-vs-bot" }, "status": 500, "code": "internal_error" }
  ],
  "evals_pages_ok": true,
  "data_ok": true,
  "findings": [
    "Local /evals/ and /evals/glm-vs-claude-2026-11/hard-a/ both return 200; replay page has labelled Previous turn / Play / Next turn controls",
    "Manifest lists 6 matches: thief-a6 (live), thief-a5 (tie), thief-a4 (tie), opus-a (queued), thief-a (void), hard-a (finished); every summary entry carries a reason",
    "Pooled rejected orders: GLM (blue) = 7, Sonnet (red) = 57",
    "GLM TensorFold (blue, \"reasoning\": \"full\") publishes reasoning text; Sonnet 5.5 medium (red, \"reasoning\": \"none\") does not",
    "Sampled match file hard-a.a4ac7412.json includes a reason; no missing reason found",
    "Match f17543d7f16f4bd399abd758bd541a40 completed in 4 turns (score 10, win)",
    "Production https://league-of-agents.ai/evals/ returns HTTP/2 404"
  ],
  "confusions": [
    "Docs example {\"mode\": \"solo-vs-bot\"} 500'd on the local site instead of a structured 400; the working/default mode is \"stub-duel\" — unclear whether solo-vs-bot is misconfigured locally",
    "Token was provided out of band; the human GitHub sign-in + POST /auth/agents mint flow could not be exercised locally"
  ]
}
```
```
