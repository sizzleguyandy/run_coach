# AGENTS.md — Run Coach

Instructions for all AI agents working in this repository.

---

## What This Project Is

A Telegram-based running coach (`@SizweUmoya_bot`). It generates Jack Daniels-methodology training plans, applies weather-based pace adjustments via Open-Meteo, and adapts weekly from logged runs and RPE. The stack is Python 3.11, FastAPI, SQLite, and python-telegram-bot 20.x.

---

## Architecture Rules

These constraints are load-bearing. Break any of them and the system stops working correctly.

**1. The bot never imports engine modules directly.**
The Telegram bot (`telegram_bot/`) is a thin UI layer. It talks to `coach_core/` only via HTTP on `localhost:8000` using `httpx`. Never add a direct import from `telegram_bot` into `coach_core/engine/`.

**2. Engine modules are pure and deterministic.**
Files under `coach_core/engine/` have no I/O, no network calls, no database reads. They take arguments and return values. Keep them that way. Weather fetching belongs in `truepace.py`, which is the one exception (it calls Open-Meteo), but it is wrapped in try/except everywhere it is called so the plan never blocks on weather.

**3. Plan is not stored — it is recalculated on every request.**
`GET /plan/{id}/current` rebuilds the full plan from the athlete profile each call. Do not add plan persistence without explicit instruction; it would make live VO2X and mileage changes require a cache invalidation step that does not exist.

**4. `plan_type` gates all downstream logic.**
When `athlete.plan_type == "c25k"`, the plan and weather routers return C25K-specific responses and the paces endpoint returns 400. Always check `plan_type` before assuming a VO2X value exists.

**5. ConversationHandler state ranges must not overlap.**
`LOG_*` states: `range(4)` (0–3). `RACE_*` states: `range(10, 13)` (10–12). Onboarding states: `range(11)` (0–10) in a separate handler. Adding a new ConversationHandler requires a non-overlapping state range.

**6. VO2X race guard is intentional.**
`POST /log/race` silently refuses to update `athlete.vo2x` if the new value is more than 3 points below current, returning `vo2x_updated: false`. The athlete must resubmit with `force: true`. Do not remove this guard.

**7. Inline button callbacks edit the existing message, not send a new one.**
`handle_callback()` calls `edit_message_text()`. Telegram only allows editing messages that are not too old. The bot has a silent fallback to send a new message if the edit fails. This is expected — do not replace the fallback with an error.

---

## Database

SQLite at `coach.db`, created automatically on first API start. Three tables: `athletes`, `run_logs`, `vo2x_history`. Schema lives in `coach_core/models.py`. If you change the schema, delete `coach.db` locally before testing — SQLAlchemy does not auto-migrate.

---

## Development Workflow

**Before touching any file:**
1. Read `FREEZE.md`. Files listed there are frozen — do not edit them without explicit instruction.
2. Read `CALCULATIONS.md` before changing any pace, volume, or VO2X formula. The numbers are derived from Daniels 4th ed tables and corpus-verified against real race results.

**After any change:**
Run the syntax check:
```bash
python3 syntax_check.py
```
Expected output: `All files OK`

Then run the test suite:
```bash
python3 -c "
import sys, unittest.mock as mock
for mod in ['sqlalchemy','sqlalchemy.ext.asyncio','sqlalchemy.orm',
            'dotenv','httpx','telegram','telegram.ext',
            'coach_core.database','coach_core.models']:
    sys.modules[mod] = mock.MagicMock()
sys.path.insert(0, '.')
exec(open('test_audit_fixes.py').read())
"
```
Expected output: all tests passing. Do not commit with a failing test.

**Commit format:**
```
<short imperative summary under 72 chars>

<body: what changed and why, if not obvious from the summary>
```
No ticket numbers, no emoji, no "feat:" prefixes unless the project already uses them.

---

## Module Map

| Path | Responsibility |
|------|----------------|
| `coach_core/engine/phases.py` | Allocates weeks into Phase I/II/III/IV |
| `coach_core/engine/volume.py` | Builds weekly volume curve from base mileage to peak, then taper |
| `coach_core/engine/paces.py` | VO2X → pace lookup (Daniels 4th ed table, VO2X 30–85, interpolated) |
| `coach_core/engine/workouts.py` | Builds 7-day session dict for a given week |
| `coach_core/engine/plan_builder.py` | Assembles full plan: phases → volume → paces → workouts |
| `coach_core/engine/hills.py` | Determines when to substitute flat quality sessions with hill work |
| `coach_core/engine/adaptation.py` | Post-week closed loop: adjusts volume and VO2X from compliance and RPE |
| `coach_core/engine/c25k.py` | Standalone C25K programme, 12-week static schedule with adaptation |
| `coach_core/engine/truepace.py` | Fetches Open-Meteo weather, computes pace adjustment factor, 1-hour cache |
| `coach_core/engine/sa_cities.py` | 30-city SA lookup table |
| `coach_core/routers/athlete.py` | Athlete CRUD and graduation endpoints |
| `coach_core/routers/plan.py` | Plan retrieval endpoints |
| `coach_core/routers/log.py` | Run logging and adaptation trigger endpoints |
| `coach_core/routers/weather.py` | Weather adjustment and conditions endpoints |
| `telegram_bot/bot.py` | Builds the Application, registers all handlers |
| `telegram_bot/formatting.py` | All message formatters and inline keyboard builders. No API calls. |
| `telegram_bot/handlers/onboarding.py` | 11-state ConversationHandler for /start |
| `telegram_bot/handlers/ui.py` | All inline button callbacks |
| `telegram_bot/handlers/log_handler.py` | /log and /lograce ConversationHandlers |

---

## Writing Rules

All prose output — Telegram messages, error text, docstrings, comments, and documentation — follows the rules in `CLAUDE.md`.

The short version:
- No emdashes. No intensifiers. No filler phrases.
- Every claim that includes a number must use a real, attributable number.
- Headings describe content; they do not tease it.
- No AI transition phrases (furthermore, moreover, that being said, at its core).
- No AI verbs (leverage, utilize, facilitate, foster, bolster, delve, underscore).
- Write like a researcher, not a copywriter.

Read `CLAUDE.md` in full before writing or editing any user-facing text.
