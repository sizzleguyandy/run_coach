# Spawn & Build: the Run Coach Agent

**Context.** Hermes is already installed and running as the user's personal
assistant, and the WhatsApp gateway is installed. You (the personal assistant)
are about to **spawn a second, isolated agent** that becomes the **Run Coach** and
builds itself. The Run Coach must have its **own SOUL and MEMORY**, separate from
yours, so the two identities and memories never mix.

`PROJECT` below = the absolute path to the `world_marathon_trainer/` directory.

---

## Step 0 — Spawn the Run Coach as an isolated agent

The Run Coach needs its own identity + memory, which in Hermes means its own
home directory (`HERMES_HOME`). Create an isolated instance:

```bash
export RUNCOACH_HOME="$HOME/.hermes-runcoach"
mkdir -p "$RUNCOACH_HOME"
```

> If your Hermes build has a native multi-agent / sub-agent spawn command, use it
> and point that agent's home at `$RUNCOACH_HOME`. Check `hermes --help` for an
> `agent`/`spawn` subcommand. Either way, the rule is the same: **the Run Coach
> gets its own HERMES_HOME so its SOUL.md and MEMORY.md are its own.**

From here on, "**you**" = the Run Coach agent, building yourself inside
`$RUNCOACH_HOME`.

## Step 1 — Backend (your brain)

Ensure the World Marathon Trainer API is up (it's the source of all training
truth). If it's already running, skip the build:

```bash
curl -s http://localhost:8000/health || (
  cd "$PROJECT" && ./setup.sh --seed &&
  nohup uvicorn api.main:app --port 8000 > /tmp/wmt_api.log 2>&1 &
)
sleep 2 && curl -s http://localhost:8000/health   # expect status ok
```

`./setup.sh --seed` prints a demo `athlete_id` — keep it for the verify step.

## Step 2 — Wire your tools (MCP)

Write `$RUNCOACH_HOME/config.yaml` with the coach MCP server (create the file):

```yaml
mcp_servers:
  world_marathon_trainer:
    command: python
    args: ["<PROJECT>/coach_mcp_server.py"]
    env:
      WMT_API_BASE: "http://localhost:8000"
```

Replace `<PROJECT>` with the absolute path. (CUSTOM server — do NOT run
`hermes mcp serve`.) Reload with `/reload-mcp` or restart the instance. You should
see `world_marathon_trainer` with `get_today`, `get_plan`, `race_knowledge`, etc.

## Step 3 — Write your SOUL (identity)

Hermes injects `SOUL.md` verbatim as the first thing in your system prompt:

```bash
cp "<PROJECT>/docs/COACH_AGENT.md" "$RUNCOACH_HOME/SOUL.md"
```

This is your identity **and** the guardrail: it forbids you from inventing
training — the numbers always come from your tools.

## Step 4 — Initialise your MEMORY

Seed your persistent memory with the coaching memory policy:

```bash
cp "<PROJECT>/docs/COACH_MEMORY.md" "$RUNCOACH_HOME/MEMORY.md"
```

This tells you what to remember (each athlete's `athlete_id` + human context) and
what never to memorise (paces/volume/plan — always fetch fresh, because the engine
adapts weekly).

## Step 5 — Model

Reuse the same provider/key the personal assistant already uses (cheapest, no new
secret). If the isolated instance needs its own model config, set it:

```bash
HERMES_HOME="$RUNCOACH_HOME" hermes model     # ⛔ HUMAN if a new API key is needed
```

## Step 6 — WhatsApp channel

The gateway is already installed. Bind the Run Coach to a WhatsApp route so
athletes reach the coach, not the personal assistant:

```
⛔ HUMAN: give the Run Coach its own WhatsApp number/route (or confirm the
   routing), and complete any auth. Run `hermes gateway status` to confirm.
```

(A separate number keeps athlete conversations cleanly on the coach. If your
gateway supports routing multiple personas on one number, route coach traffic to
`$RUNCOACH_HOME`.)

## Step 7 — Verify (discipline test)

Message the Run Coach over WhatsApp (or its TUI), using the demo `athlete_id`:

1. **"What should I run today?"** → must call `get_today` and relay the engine's
   session. If it invents a workout → SOUL.md not loaded (redo step 3).
2. **"How should I pace the back half of Cape Town?"** → must call
   `race_knowledge` and answer from the profile (km 30-32 climb, the Loop of
   Death), not from generic memory.
3. **"Remember my knee was sore."** then later **"how's my knee?"** → must recall
   it from MEMORY (step 4 working), while still fetching today's session fresh.

All three pass → the Run Coach is built: its own SOUL, its own MEMORY, your engine
as its brain.

---

## What the Run Coach owns vs shares

```
OWN (isolated in $RUNCOACH_HOME):     SHARES with the engine:
  SOUL.md     (coach identity)          the training truth — every plan, pace,
  MEMORY.md   (per-athlete context)     and adaptation comes from the tools,
  config.yaml (coach MCP tools)         never from the coach's own memory
```

## Human-only steps (everything else is yours to do)

- Step 5: a new model API key, *only if* the isolated instance can't reuse the
  existing one.
- Step 6: giving the coach its WhatsApp number/route + any auth.
