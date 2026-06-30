# Running the coach on Hermes Agent

Hermes provides the conversational shell — WhatsApp, memory, multi-channel, model
routing. Your FastAPI/engine stays the brain. They connect via the MCP adapter
(`coach_mcp_server.py`), which exposes your endpoints as a fixed set of tools.

```
Hermes (WhatsApp + memory + Claude)
   └─ MCP ─> coach_mcp_server.py (thin) ─> FastAPI (thin) ─> engine (brain)
```

## 1. Run the coach API

```bash
cd world_marathon_trainer
pip install -r requirements.txt
uvicorn api.main:app --port 8000
```

## 2. Install Hermes + point it at Claude

```bash
curl -fsSL https://hermes-agent.nousresearch.com/install.sh | bash
hermes model           # select Anthropic / Claude, give your API key
```

## 3. Register the coach MCP server

Add to `~/.hermes/config.yaml`:

```yaml
mcp_servers:
  marathon_coach:
    command: python
    args: ["/abs/path/to/world_marathon_trainer/coach_mcp_server.py"]
    env:
      WMT_API_BASE: "http://localhost:8000"
```

> Direction check: you are NOT running `hermes mcp serve` (that exposes Hermes's
> own tools). You are registering a CUSTOM server so Hermes can call YOUR coach.

Restart Hermes to load it. The agent now has these tools:

| Tool | Does |
|---|---|
| `list_races` | available races |
| `race_knowledge` | full course/strategy profile (the RAG source) |
| `onboard_athlete` | slot a new athlete in (preview or commit) |
| `get_athlete` | stored profile |
| `get_plan` | full live-computed plan |
| `get_today` | today's / this week's session |
| `get_paces` | current training paces + race-equivalents |
| `adapt_week` | run weekly adaptation, returns coaching notes |
| `get_adaptation_history` | audit trail |

## 4. Put it on WhatsApp

```bash
hermes gateway setup       # pick WhatsApp, follow auth
hermes gateway status
```

## 5. The system prompt — the openclaw guardrail (IMPORTANT)

Hermes is autonomous and self-improving. Constrain it so it never freelances
coaching. Set a system prompt along these lines:

```
You are a marathon coaching assistant. You are NOT the source of training
decisions — the training engine is, reached through your tools.

HARD RULES:
- For ANYTHING about the athlete's plan, today's session, paces, volume, or
  weekly review: call the matching tool (get_today, get_plan, get_paces,
  adapt_week). NEVER invent a workout, distance, pace, or prescription.
- For race-specific questions (course, hills, pacing, mistakes): call
  race_knowledge and answer ONLY from what it returns. Do not use prior
  knowledge of the race.
- If a tool returns an error or no data, say so plainly and ask the athlete
  for what's missing. Do not guess around it.
- You may be warm, motivating, and explain the engine's output in plain
  language — but the numbers always come from the tools.
- Do not create or persist "skills" that substitute your own training logic
  for the engine's.
```

## 6. The discipline test (decides if Hermes is right for you)

Once wired, in WhatsApp ask:

> "What should I run today?"

Watch the tool calls. **Pass:** it calls `get_today` and relays the engine's
session. **Fail:** it makes up a workout without calling the tool.

Then:

> "How should I pace the back half of Cape Town?"

**Pass:** it calls `race_knowledge` and answers from the profile (the km 30-32
climb, the Loop of Death, even pacing). **Fail:** it answers from generic memory.

If it passes both, you have your agent + WhatsApp + memory with only this thin
adapter as new code. If it fails, tighten the prompt or fall back to a custom
thin Claude loop where the agent structurally cannot go off-script.
```
