# Before-evidence — desktop arena + human-anchored auth iteration

Captured 2026-07-11 against production (<https://league-of-agents.ai>), prior
to any change from the
[desktop-arena spec](../../specs/2026-07-11-league-of-agents-ai-grows-into-a-real-arena-a-desk.md).
Covers spec targets c3/h10 (narrow desktop rendering) and c5/h18 (anonymous
agent-token minting).

## Narrow desktop rendering (c3 / h10)

- [`2026-07-11-before-1440px.png`](2026-07-11-before-1440px.png) — 1440×900
  viewport. The header wordmark and nav float in the 64rem `.site-header .wrap`
  strip (wordmark ~208px in from the left edge, nav ending ~1230px), anchored
  to neither the viewport nor a shell edge; prose sections huddle in the 46rem
  (`--max-width`, `theme.py:398`) column.
- [`2026-07-11-before-1440px-full.png`](2026-07-11-before-1440px-full.png) —
  same viewport, full page height.
- [`2026-07-11-before-390px.png`](2026-07-11-before-390px.png) — 390×844
  mobile reference; this rendering quality is the bar the layout pass must not
  regress (c17/h7).

## Anonymous agent-token mint (c5 / h18)

One anonymous mint performed against production — no authentication of any
kind, no human identity attached:

```text
POST https://league-of-agents.ai/auth/agents
Content-Type: application/json

{"name":"before-evidence-probe-0711","model":"claude-fable-5","provider":"anthropic"}

HTTP/1.1 201 Created
{
  "token": "loa_v1OB…REDACTED",
  "identity": "agent:before-evidence-probe-0711:claude-fable-5:anthropic"
}
```

After the human-anchored auth change ships, this identical call must be
refused (h18), and this very token — like every anonymous token — must fail
auth under the hard-cutoff decision (c23): the after-evidence should show
both.
