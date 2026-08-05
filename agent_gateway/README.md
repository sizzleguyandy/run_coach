# TR3D Agent Gateway

A second front-end for the TR3D running coach: a web chat UI backed by a Claude
agent that reads an athlete's plan out of `coach_core` and answers questions
about it.

**`coach_core` is not modified and not imported.** The gateway is a separate
FastAPI service that consumes the engine over HTTP, read-only — the same
pattern the Telegram bot already uses.

```
                    ┌──────────────────────────┐
   Web chat UI ────▶│  agent_gateway  (NEW)    │
   (agent_gateway/  │  · own auth (link code)  │
    web/index.html) │  · Claude agent + tools  │
                    └────────────┬─────────────┘
                                 │  HTTP GET only
                                 ▼
                    ┌──────────────────────────┐
                    │  coach_core  (UNCHANGED) │
                    │  plans · paces · logs    │
                    └──────────────────────────┘
```

## Why it can't break anything

| Property | How it's enforced |
|---|---|
| Cannot write to `coach_core` | `CoreClient` has exactly one transport method, `_get`. There is no `_post`/`_patch`/`_delete` to call. |
| Cannot read another athlete | No tool accepts an athlete identifier. The athlete is bound from a verified session token via a context variable, so prompt injection has no parameter to attack. |
| Cannot be impersonated | `coach_core` treats a raw `telegram_id` as identity with no auth, so the gateway never accepts one from a client. A visitor proves identity once with a Telegram link code and gets an HMAC-signed token; the athlete id is read out of the verified token. |
| Cannot be coupled to `coach_core` internals | It talks HTTP and imports nothing from `coach_core`. |

## Running it

`coach_core` runs as usual. Start the gateway alongside it:

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export CORE_API_BASE_URL=http://localhost:8000     # where coach_core lives
export GATEWAY_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(48))')"

uvicorn agent_gateway.main:app --port 8100
```

Open `http://localhost:8100`, send `/mycode` to the Telegram bot, and paste the
code in to connect.

### Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required. Read by the SDK. |
| `CORE_API_BASE_URL` | `http://localhost:8000` | Where `coach_core` is reachable. |
| `GATEWAY_SECRET` | random per process | Signs session tokens. **Set this** — otherwise tokens are invalidated on restart. |
| `AGENT_MODEL` | `claude-opus-5` | Model id. |
| `AGENT_EFFORT` | `medium` | `low`/`medium`/`high`/`xhigh`/`max`. |
| `GATEWAY_ALLOWED_ORIGINS` | unset | Comma-separated origins. Unset means `*` with credentials disabled. |
| `GATEWAY_SESSION_TTL_HOURS` | `720` | Session token lifetime. |

## API

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/link` | none | Exchange a Telegram link code for a session token. |
| `POST /api/chat` | bearer token | Ask the coach a question. |
| `GET /health` | none | Liveness + whether a model key is configured. |
| `GET /` | none | The chat frontend. |

## The agent's tools

All read-only, all scoped to the authenticated athlete:

`get_athlete_profile` · `get_training_paces` · `get_current_week` ·
`get_training_week` · `get_plan_overview` · `get_week_training_log` ·
`get_month_training_log` · `get_running_conditions`

Because the agent has no write tools, "log my run" and "change my plan" are
answered by pointing the athlete back at the Telegram bot.

## Adding another channel

`agent.py` is channel-agnostic — it takes conversation history plus a question
and returns text. A WhatsApp, Slack, or SMS adapter is a new route that
authenticates the user, calls `bind_athlete(...)`, and calls
`CoachAgent.reply(...)`. The web UI in `web/` is simply the first adapter.
