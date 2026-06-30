# Build Instructions (for a setup agent)

You are an automation agent setting up the Marathon Coach end to end. Execute the
steps in order. Do **everything** yourself **except two checkpoints** that need a
human — **LLM selection** and **WhatsApp auth** — where you must stop, print the
exact command for the human to run, and wait for them to confirm before
continuing.

Use absolute paths throughout. `PROJECT` below = the absolute path to the
`world_marathon_trainer/` directory.

> Two documents, two roles — don't blend them:
> - **this file** = the steps you execute
> - **COACH_AGENT.md** = the coach agent's persona (you install it in step 6)

---

## Step 1 — Backend: deps, database, tests, demo athlete  (agent)

```bash
cd "$PROJECT"
./setup.sh --seed
```
This installs Python deps, builds the SQLite DB, runs the test suite, and seeds a
demo athlete. **Capture the printed `athlete_id`** — you'll use it in step 8.
If the tests don't pass, stop and report the failure.

## Step 2 — Start the coach API  (agent)

Run it so it stays up and is reachable at `http://localhost:8000`:
```bash
cd "$PROJECT"
nohup uvicorn api.main:app --port 8000 > /tmp/wmt_api.log 2>&1 &
```
Verify:
```bash
curl -s http://localhost:8000/health     # expect {"status":"ok","races_loaded":1}
```
If this fails, stop and report (check `/tmp/wmt_api.log`).

## Step 3 — Install Hermes Agent  (agent)

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
# then ensure the hermes binary is on PATH (source the shell rc if needed)
hermes --version
```

## Step 4 — Register the coach tools (MCP server)  (agent)

Edit `~/.hermes/config.yaml` and add (create the file/keys if missing):
```yaml
mcp_servers:
  marathon_coach:
    command: python
    args: ["<PROJECT>/coach_mcp_server.py"]
    env:
      WMT_API_BASE: "http://localhost:8000"
```
Replace `<PROJECT>` with the absolute path. This is a CUSTOM MCP server — do NOT
run `hermes mcp serve` (that's the opposite direction). After editing, reload:
```bash
# in a Hermes session:  /reload-mcp     (or just restart Hermes)
```
Verify the tools are visible (e.g. `hermes mcp` / list tools) — you should see
`marathon_coach` with `get_today`, `get_plan`, `race_knowledge`, etc.

## Step 5 — Confirm the MCP requirements  (agent)

```bash
python -c "import mcp, httpx; print('mcp deps ok')"
```
(`setup.sh` already installed these via requirements.txt; this just confirms.)

## Step 6 — Set the coach's identity (SOUL.md)  (agent)

Hermes loads its identity from `~/.hermes/SOUL.md` and injects it verbatim as the
first thing in the system prompt. No command is needed — just write the file:

```bash
cp "<PROJECT>/docs/COACH_AGENT.md" ~/.hermes/SOUL.md
```

(Hermes auto-creates a starter SOUL.md and never overwrites a user one, so copy
over it explicitly.) Since this whole Hermes instance IS the marathon coach, the
coach brief is its durable identity. This is the guardrail that stops the agent
inventing training. If you came from openclaw, do NOT reuse the old builder
SOUL.md here — use COACH_AGENT.md.

## Step 7 — HUMAN CHECKPOINT 1: select the LLM  ⛔ STOP

Print this and wait for the human to complete it (it requires an API key — a
secret you must not handle):

```
HUMAN: run `hermes model`, choose your model (e.g. Anthropic/Claude),
       and paste your API key. Tell me when done.
```

## Step 8 — HUMAN CHECKPOINT 2: connect WhatsApp  ⛔ STOP

Print this and wait (interactive auth you cannot complete):

```
HUMAN: run `hermes gateway setup`, choose WhatsApp, and complete the auth
       (QR scan / Business Cloud), then `hermes gateway start`.
       `hermes gateway status` should show it connected. Tell me when done.
```

## Step 9 — Verify (agent + human together)

Once both checkpoints are confirmed, run the **discipline test** in WhatsApp
(or `hermes --tui`), using the demo `athlete_id` from step 1:

1. **"What should I run today?"**
   - PASS: it calls `get_today` and relays the engine's session.
   - FAIL: it invents a workout → re-check step 6 (persona not set).
2. **"How should I pace the back half of Cape Town?"**
   - PASS: it calls `race_knowledge`, answers from the profile (km 30-32 climb,
     the Loop of Death, even pacing).
   - FAIL: answers from generic memory → tighten the persona.

Both pass → setup complete.

---

## Summary: who does what

```
AGENT (you) does:                          HUMAN does (2 stops):
  setup.sh (deps, DB, tests, seed)           hermes model   (LLM + API key)
  start the API                              hermes gateway setup (WhatsApp)
  install Hermes
  register MCP server (config.yaml)
  set the system prompt (COACH_AGENT.md)
  run the discipline test
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `curl /health` fails | API not running — redo step 2; check `/tmp/wmt_api.log` |
| agent: "cannot reach coach API" | `WMT_API_BASE` wrong or API down |
| tools don't appear in Hermes | config.yaml path wrong, or Hermes not restarted |
| agent invents workouts | persona not set from COACH_AGENT.md (step 6) |
| `./setup.sh` permission denied | `chmod +x setup.sh` and retry |
| `hermes` not found after install | source your shell rc, or restart the shell |
