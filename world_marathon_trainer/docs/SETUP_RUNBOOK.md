# Setup Runbook (agent-runnable)

An ordered checklist to stand up the marathon coach. Most steps are commands an
agent can run directly. Steps marked **`HUMAN:`** need a person (secrets,
interactive auth, install bootstrap) — an agent must stop and ask for these.

> Two documents, two roles — don't blend them:
> - **this file** = *do these steps*
> - **COACH_AGENT.md** = *the agent's persona* (set as system prompt, step 7)

---

## A. Backend (agent can do all of this)

```bash
# 1. From the project directory:
cd world_marathon_trainer

# 2. One command: install deps, build the SQLite DB, run tests, seed a demo athlete
./setup.sh --seed
#    -> prints a demo athlete_id; keep it for the discipline test in step 8

# 3. Start the API (leave it running; reachable at http://localhost:8000)
uvicorn api.main:app --port 8000
```

Verify: `curl -s http://localhost:8000/health` → `{"status":"ok","races_loaded":1}`

## B. Hermes Agent

```bash
# 4. HUMAN: install Hermes (bootstrap — the agent can't install its own host)
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
source ~/.bashrc        # or ~/.zshrc
```

```bash
# 5. HUMAN: point Hermes at Claude and provide the API key (a secret)
hermes model            # select Anthropic / Claude, paste API key
```

```yaml
# 6. Register the coach tools — agent edits ~/.hermes/config.yaml, adding:
mcp_servers:
  marathon_coach:
    command: python
    args: ["<ABSOLUTE_PATH>/world_marathon_trainer/coach_mcp_server.py"]
    env:
      WMT_API_BASE: "http://localhost:8000"
```
Then restart Hermes so the tools load. (This is a CUSTOM server — do NOT run
`hermes mcp serve`, which is the opposite direction.)

```
# 7. Set the agent's system prompt to the contents of docs/COACH_AGENT.md
#    (via Hermes's system-prompt / persona config). This is the guardrail that
#    keeps it calling tools instead of inventing training.
```

```bash
# 8. HUMAN: connect WhatsApp (interactive auth — QR / Business Cloud)
hermes gateway setup        # pick WhatsApp, complete auth
hermes gateway status
```

## C. The discipline test (decides if Hermes stays in its lane)

In WhatsApp (or `hermes --tui`):

1. **"What should I run today?"** (give it the demo `athlete_id` from step 2)
   - PASS: it calls `get_today` and relays the engine's session.
   - FAIL: it invents a workout. → tighten the system prompt.
2. **"How should I pace the back half of Cape Town?"**
   - PASS: it calls `race_knowledge` and answers from the profile (the km 30-32
     climb, the Loop of Death, even pacing).
   - FAIL: it answers from generic memory.

Both pass → agent + WhatsApp + memory are wired correctly over your engine.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `curl /health` fails | API not running — start step 3; check the port |
| agent says "cannot reach coach API" | `WMT_API_BASE` wrong, or API not running on that host/port |
| tools don't appear in Hermes | config.yaml path wrong, or Hermes not restarted |
| agent invents workouts | system prompt not set from COACH_AGENT.md (step 7) |
| `./setup.sh` permission denied | `chmod +x setup.sh` |
| tests fail on a fresh box | `pip install -r requirements.txt` then re-run |
