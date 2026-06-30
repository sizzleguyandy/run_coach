# Testing onboarding (3 layers)

The "agent refused to talk to the API" problem is a **tool-calling** problem, not a
connectivity one. Prove the plumbing works without the agent first; then the only
remaining variable is whether the LLM calls its tools.

## Layer 1 — FastAPI directly (no agent, no MCP)

```bash
curl -s http://localhost:8000/health
# {"status":"ok","races_loaded":...}

curl -s http://localhost:8000/races
```
If these work, the engine is up.

## Layer 2 — MCP adapter → FastAPI (the agent's exact path, no LLM)

```bash
WMT_API_BASE=http://localhost:8000 python scripts/smoke_onboarding.py
```
This imports `coach_mcp_server` and calls its tool functions
(`list_races`, `onboard_athlete` preview+commit, `get_athlete`, `get_today`,
`get_paces`) against the running API — the identical code the agent invokes,
minus the model. Expect **ALL PASS**.

**If Layer 2 passes, the backend and adapter are proven end to end.** Any failure
in the live agent is then the LLM not calling tools, NOT connectivity.

## Layer 3 — Agent → MCP → FastAPI (the full stack)

In the Run Coach TUI:
```bash
HERMES_HOME=~/.hermes-runcoach hermes
```
Then: **"I want to start training for Cape Town."**

- PASS: it calls `onboard_athlete` (you'll see the tool invocation) and walks you
  through name / date / committed days, then shows the slot-in.
- FAIL (the openclaw symptom): it talks *about* onboarding but never calls the
  tool, or claims it can't reach the API.

## If Layer 2 passes but Layer 3 fails ("agent won't talk to the API")

It is NOT connectivity — Layer 2 just proved the path. It's the agent not calling
tools. Check, in order:

1. **SOUL.md loaded?** `cat ~/.hermes-runcoach/SOUL.md` — must be the COACH_AGENT
   brief. If empty/default, the guardrail isn't active. Re-copy it (spawn doc
   step 3) and restart.
2. **Tools visible to this instance?** Confirm the MCP server is registered in
   `~/.hermes-runcoach/config.yaml` and reloaded (`/reload-mcp`). The agent should
   list `world_marathon_trainer` tools.
3. **Right HERMES_HOME?** Make sure you launched the coach instance, not the
   personal assistant (which has no coach tools).
4. **API base reachable from the MCP subprocess?** The `env.WMT_API_BASE` in
   config.yaml must point at the running API (`http://localhost:8000`).
5. **Reinforce the persona** if it still freelances: the SOUL.md already says
   "never invent training — call the tools"; make sure it wasn't truncated.

The point: Layer 2 isolates the backend so a Layer-3 failure has exactly one
cause — tool-calling behaviour — and a short checklist to fix it.
