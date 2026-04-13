# Run Coach — AI Builder Instructions

> Complete instructions for assembling the Run Coach system from the supplied
> source files. Follow every step in order. Do not skip steps or re-order them.

---

## What You Are Building

A Telegram-based running coach that:
- Generates Daniels-methodology training plans (6–24 weeks, all distances 5k→ultra)
- Includes a Couch to 5K beginner programme
- Applies weather-based pace adjustments (TRUEPACE via Open-Meteo)
- Adapts weekly based on logged runs and RPE
- Delivers everything through Telegram with inline button navigation

**Stack:** Python 3.11+ · FastAPI · SQLite · python-telegram-bot 20.x

---

## Pre-flight Checklist

Before you start, confirm you have:

- [ ] Python 3.11 or higher installed
- [ ] A Telegram Bot Token (create via @BotFather → /newbot)
- [ ] Internet access (for Open-Meteo weather API — no key needed)
- [ ] All source files from the supplied archive

---

## Step 1 — Create Project Structure

Create this exact directory tree. Every folder and `__init__.py` must exist.

```
run_coach/
├── .env.example
├── .env                          ← you create this (copy from .env.example)
├── requirements.txt
├── run.sh
├── coach_core/
│   ├── __init__.py
│   ├── database.py
│   ├── main.py
│   ├── models.py
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── adaptation.py
│   │   ├── c25k.py
│   │   ├── hills.py
│   │   ├── paces.py
│   │   ├── phases.py
│   │   ├── plan_builder.py
│   │   ├── sa_cities.py
│   │   ├── truepace.py
│   │   ├── volume.py
│   │   └── workouts.py
│   └── routers/
│       ├── __init__.py
│       ├── athlete.py
│       ├── log.py
│       ├── plan.py
│       └── weather.py
└── telegram_bot/
    ├── __init__.py
    ├── bot.py
    ├── config.py
    ├── formatting.py
    └── handlers/
        ├── __init__.py
        ├── log_handler.py
        ├── onboarding.py
        ├── plan_handler.py
        └── ui.py
```

**Commands to create all directories:**
```bash
mkdir -p run_coach/coach_core/engine
mkdir -p run_coach/coach_core/routers
mkdir -p run_coach/telegram_bot/handlers
touch run_coach/coach_core/__init__.py
touch run_coach/coach_core/engine/__init__.py
touch run_coach/coach_core/routers/__init__.py
touch run_coach/telegram_bot/__init__.py
touch run_coach/telegram_bot/handlers/__init__.py
```

---

## Step 2 — Place Source Files

Copy each supplied source file into its correct location:

| Supplied file | Destination path |
|---|---|
| `requirements.txt` | `run_coach/requirements.txt` |
| `run.sh` | `run_coach/run.sh` |
| `.env.example` | `run_coach/.env.example` |
| `database.py` | `run_coach/coach_core/database.py` |
| `main.py` | `run_coach/coach_core/main.py` |
| `models.py` | `run_coach/coach_core/models.py` |
| `adaptation.py` | `run_coach/coach_core/engine/adaptation.py` |
| `c25k.py` | `run_coach/coach_core/engine/c25k.py` |
| `hills.py` | `run_coach/coach_core/engine/hills.py` |
| `paces.py` | `run_coach/coach_core/engine/paces.py` |
| `phases.py` | `run_coach/coach_core/engine/phases.py` |
| `plan_builder.py` | `run_coach/coach_core/engine/plan_builder.py` |
| `sa_cities.py` | `run_coach/coach_core/engine/sa_cities.py` |
| `truepace.py` | `run_coach/coach_core/engine/truepace.py` |
| `volume.py` | `run_coach/coach_core/engine/volume.py` |
| `workouts.py` | `run_coach/coach_core/engine/workouts.py` |
| `routers/athlete.py` | `run_coach/coach_core/routers/athlete.py` |
| `routers/log.py` | `run_coach/coach_core/routers/log.py` |
| `routers/plan.py` | `run_coach/coach_core/routers/plan.py` |
| `routers/weather.py` | `run_coach/coach_core/routers/weather.py` |
| `bot.py` | `run_coach/telegram_bot/bot.py` |
| `config.py` | `run_coach/telegram_bot/config.py` |
| `formatting.py` | `run_coach/telegram_bot/formatting.py` |
| `log_handler.py` | `run_coach/telegram_bot/handlers/log_handler.py` |
| `onboarding.py` | `run_coach/telegram_bot/handlers/onboarding.py` |
| `plan_handler.py` | `run_coach/telegram_bot/handlers/plan_handler.py` |
| `ui.py` | `run_coach/telegram_bot/handlers/ui.py` |

---

## Step 3 — Configure Environment

```bash
cd run_coach
cp .env.example .env
```

Edit `.env` and set:

```
TELEGRAM_BOT_TOKEN=your_token_here
API_BASE_URL=http://localhost:8000
DATABASE_URL=sqlite+aiosqlite:///./coach.db
```

**Getting your Telegram Bot Token:**
1. Open Telegram and message @BotFather
2. Send `/newbot`
3. Follow prompts — choose a name and username ending in `bot`
4. BotFather returns a token like `7123456789:AAFxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`
5. Paste that as `TELEGRAM_BOT_TOKEN`

---

## Step 4 — Install Dependencies

```bash
cd run_coach
pip install -r requirements.txt
```

**requirements.txt contains:**
```
fastapi>=0.110.0
uvicorn[standard]>=0.27.0
sqlalchemy>=2.0.0
aiosqlite>=0.19.0
python-telegram-bot>=20.7
httpx>=0.26.0
pydantic>=2.0.0
python-dotenv>=1.0.0
```

If you are on a system that requires `--break-system-packages`:
```bash
pip install -r requirements.txt --break-system-packages
```

---

## Step 5 — Verify Syntax

Before running anything, verify all Python files are error-free:

```bash
cd run_coach
python3 -c "
import ast, os, sys
errors = []
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in ['__pycache__', '.git']]
    for f in files:
        if not f.endswith('.py'): continue
        try:
            ast.parse(open(os.path.join(root, f)).read())
        except SyntaxError as e:
            errors.append(f'{f}: {e}')
if errors:
    for e in errors: print('ERROR:', e)
    sys.exit(1)
else:
    print(f'All files OK')
"
```

Expected output: `All files OK`
If any syntax errors appear, re-check that the file was copied to the correct location.

---

## Step 6 — Start the System

### Option A — Single command (recommended)

```bash
chmod +x run.sh
./run.sh
```

This starts both services and shows their PIDs. Press Ctrl+C to stop both.

### Option B — Two separate terminals

**Terminal 1 — API:**
```bash
cd run_coach
uvicorn coach_core.main:app --host 0.0.0.0 --port 8000 --reload
```

**Terminal 2 — Bot:**
```bash
cd run_coach
python -m telegram_bot.bot
```

---

## Step 7 — Verify the API is Running

Open in browser or run:
```bash
curl http://localhost:8000/health
```

Expected: `{"status":"ok"}`

Full interactive API docs: `http://localhost:8000/docs`

---

## Step 8 — Verify the Bot is Running

1. Open Telegram
2. Search for your bot by the username you gave it
3. Send `/start`
4. You should see the experience level keyboard appear

---

## Step 9 — Run the Test Suite

```bash
cd run_coach
python3 - << 'PYEOF'
import sys, unittest.mock as mock

for mod in ['sqlalchemy','sqlalchemy.ext.asyncio','sqlalchemy.orm',
            'dotenv','httpx','telegram','telegram.ext',
            'coach_core.database','coach_core.models']:
    sys.modules[mod] = mock.MagicMock()
sys.path.insert(0, '.')

from coach_core.engine.phases import get_phases
from coach_core.engine.volume import build_volume_curve, get_taper_weeks
from coach_core.engine.paces import calculate_paces, format_pace
from coach_core.engine.c25k import build_c25k_week, adapt_c25k_week
from coach_core.engine.truepace import compute_adjustment
from coach_core.engine.sa_cities import find_city, SA_CITIES
from coach_core.engine.hills import should_replace_with_hills
from coach_core.engine.adaptation import calculate_vdot_from_race, adapt_next_week, WeekSummary

results = []
def test(name, cond):
    mark = "PASS" if cond else "FAIL"
    results.append((name, cond))
    print(f"  [{mark}] {name}")

# Phases
p18 = get_phases(18)
test("18wk phases: I=6 III=6 IV=6", p18.phase_I==6 and p18.phase_III==6 and p18.phase_IV==6)
test("6wk phases:  I=3 IV=3",       get_phases(6).phase_I==3)

# Volume
v18 = build_volume_curve(40, 'marathon', p18)
test("18wk plan has 18 weeks",      len(v18)==18)
test("Race week = 35.0",            v18[-1]==35.0)
test("Marathon taper = 3 weeks",    get_taper_weeks('marathon')==3)
test("5k taper = 1 week",           get_taper_weeks('5k')==1)

# Paces
p50 = calculate_paces(50)
test("VDOT50 easy = 4:37 /km",      format_pace(p50.easy_min_per_km)=="4:37 /km")
test("VDOT50 threshold = 3:20 /km", format_pace(p50.threshold_min_per_km)=="3:20 /km")
test("VDOT50 interval = 2:51 /km",  format_pace(p50.interval_min_per_km)=="2:51 /km")

# VDOT from race
test("5k 20min → VDOT ~49.8",       abs(calculate_vdot_from_race(5,20)-49.8)<0.5)
test("Marathon 3:30 → VDOT ~44.6",  abs(calculate_vdot_from_race(42.195,210)-44.6)<0.5)

# VDOT cap fix
s = WeekSummary(80,80,4.5,5)
_, v, _ = adapt_next_week(80, s, 84.8)
test("VDOT nudge cap = 85 (not 80)", v==85.0)

# C25K
test("C25K 12 weeks",               len(build_c25k_week(1)['days'])==7)
test("C25K advance",                adapt_c25k_week(3,60,58)[0]==4)
test("C25K repeat",                 adapt_c25k_week(3,60,39)[0]==3)
test("C25K warm note at 1.06",      '🌡️' in build_c25k_week(5,1.06)['days']['Mon']['notes'])
test("C25K hot warning at 1.12",    '🔴' in build_c25k_week(5,1.12)['days']['Mon']['notes'])

# TRUEPACE
test("T=30 dew=20 → factor 1.075",  compute_adjustment(30,20).factor==1.075)
test("Cap at 1.15",                  compute_adjustment(50,40).factor==1.15)
test("No adj below dew=10",          compute_adjustment(20,9).factor==1.0)
test("High heat warning >1.10",      compute_adjustment(38,28).high_heat_warning)

# SA Cities
test("30 cities loaded",             len(SA_CITIES)==30)
test("Cape Town lookup",             find_city('capetown').name=='Cape Town')
test("Joburg alias",                 find_city('joburg').name=='Johannesburg')
test("PE alias",                     find_city('pe').name=='Gqeberha')
test("PMB alias",                    find_city('pmb').name=='Pietermaritzburg')

# Hills
test("Phase III high → hills",       should_replace_with_hills(3,'high',1))
test("Phase IV → never hills",       not should_replace_with_hills(4,'high',1))
test("Phase II medium alt",          should_replace_with_hills(2,'medium',1))
test("Phase II medium alt (even)",   not should_replace_with_hills(2,'medium',2))

passed = sum(1 for _,c in results if c)
total  = len(results)
print(f"\n{passed}/{total} tests passed")
if passed < total:
    print("FAILED:", [n for n,c in results if not c])
    sys.exit(1)
PYEOF
```

Expected: `29/29 tests passed`

---

## Architecture Overview

```
User (Telegram)
      ↕  messages / inline buttons
Telegram Bot  (telegram_bot/)
      ↕  HTTP on localhost:8000
FastAPI Engine  (coach_core/)
      ↕  async SQLAlchemy
SQLite Database  (coach.db)

FastAPI also calls:
      → Open-Meteo API  (weather, no key needed)
```

### Request flow — athlete sends /start

```
1. Telegram → bot.py → onboarding.py (ConversationHandler)
2. 3 to 9 questions collected depending on path
3. onboarding.py → POST /athlete/ or POST /athlete/c25k
4. coach_core/routers/athlete.py → writes to SQLite
5. onboarding.py → GET /plan/{id}/current
6. coach_core/routers/plan.py → build_full_plan() or build_c25k_week()
7. Week 1 returned → formatted → sent to user with inline keyboard
```

### Request flow — athlete taps 📊 Dashboard

```
1. Telegram → bot.py → handle_callback("dashboard")
2. ui.py:show_dashboard() → _fetch_all() runs 3 parallel requests:
   - GET /athlete/{id}
   - GET /plan/{id}/current
   - GET /log/{id}/week/{n}/summary
3. format_dashboard() builds the snapshot text
4. Message edited in-place with dashboard_keyboard()
```

---

## Module Responsibilities

### `coach_core/engine/`

| Module | Responsibility |
|---|---|
| `phases.py` | Allocates weeks into Phase I/II/III/IV. No side effects. |
| `volume.py` | Generates weekly volume curve from current mileage to peak, then taper. |
| `paces.py` | VDOT → pace lookup (Daniels 4th ed table, VDOT 30–85, interpolated). |
| `workouts.py` | Builds the 7-day session dict for a given week. Calls hills.py for quality substitution. |
| `plan_builder.py` | Assembles full plan by calling phases → volume → paces → workouts for every week. |
| `hills.py` | Determines when/if to replace flat quality sessions with hill work. Prescribes hill workouts. |
| `adaptation.py` | Post-week closed loop: adjusts volume + VDOT based on compliance and RPE. |
| `c25k.py` | Standalone C25K programme. 12-week static schedule, adaptation, VDOT graduation. |
| `truepace.py` | Fetches Open-Meteo weather, computes pace adjustment factor, 1-hour cache. |
| `sa_cities.py` | 30-city SA lookup table. find_city() resolves names and aliases to lat/lon. |

### `coach_core/routers/`

| Router | Endpoints |
|---|---|
| `athlete.py` | `POST /athlete/` `POST /athlete/c25k` `GET /athlete/{id}` `GET /athlete/{id}/paces` `PATCH /athlete/{id}/location` `POST /athlete/{id}/graduate` `DELETE /athlete/{id}` |
| `plan.py` | `GET /plan/{id}/current` `GET /plan/{id}/week/{n}` `GET /plan/{id}` |
| `log.py` | `POST /log/run` `GET /log/{id}/week/{n}/summary` `POST /log/{id}/adapt` `POST /log/race` `POST /log/{id}/c25k/adapt` `POST /log/c25k/timetrial` |
| `weather.py` | `GET /weather/{id}/adjustment` `GET /weather/{id}/conditions` |

### `telegram_bot/`

| File | Responsibility |
|---|---|
| `bot.py` | Builds the Application, registers all handlers and ConversationHandlers. Entry point. |
| `config.py` | Loads TELEGRAM_BOT_TOKEN and API_BASE_URL from .env. |
| `formatting.py` | All message formatters and inline keyboard builders. No API calls. |
| `handlers/onboarding.py` | 11-state ConversationHandler for /start. Branches to C25K or full plan. |
| `handlers/ui.py` | All inline button callbacks. show_today/plan/dashboard/paces/settings. |
| `handlers/plan_handler.py` | /plan /today /dashboard /paces /location commands. Delegates to ui.py. |
| `handlers/log_handler.py` | /log (run logging) and /lograce (race result + VDOT update) ConversationHandlers. |

---

## Telegram Bot Commands Reference

| Command | Description |
|---|---|
| `/start` | Onboarding — creates profile, delivers Week 1 |
| `/menu` | Main menu with inline buttons |
| `/today` | Today's specific session |
| `/plan` | This week's full 7-day plan |
| `/dashboard` | Weekly snapshot: compliance, days done, days to race, phase |
| `/log` | Log a completed training run |
| `/lograce` | Log a race result — updates VDOT with guard logic |
| `/paces` | Training paces for all zones (E/M/T/I/R) |
| `/progress` | Week summary with compliance bar |
| `/location` | Set city for TRUEPACE weather adjustments |
| `/reset` | Delete profile and start over |
| `/cancel` | Cancel current conversation |

---

## Onboarding Flow

```
/start
  ↓
Q1: Experience level
  [🌱 Beginner] → Q2: Name → Q3: City → DONE (C25K plan)
  [🏃 Returning] → Q2: Name → Q3: Mileage → Q4: VDOT method...
  [💪 Regular]   → Q2: Name → Q3: Mileage → Q4: VDOT method...
                                                      ↓
                                            [Know it]   → Q4a: Enter number
                                            [Estimate]  → Q4b: Race distance
                                                          Q4c: Finish time
                                            [Not sure]  → estimated from mileage
                                                      ↓
                                            Q5: Race distance
                                            Q6: Race date
                                            Q7: Hilliness
                                            Q8: City (TRUEPACE)
                                                      ↓
                                                    DONE (full plan)
```

---

## Key System Rules

These are non-obvious behaviours that matter for correct operation:

**1. Plan is generated on the fly — not stored**
The plan is recalculated each time `GET /plan/{id}/current` is called. Only the athlete profile is persisted. This means VDOT or mileage changes take effect immediately on the next `/plan` call.

**2. C25K plan_type gates everything**
When `athlete.plan_type == "c25k"`, the plan and weather routers return C25K-specific responses. The paces endpoint returns 400. Always check `plan_type` before assuming a VDOT exists.

**3. VDOT race result guard**
`POST /log/race` will NOT update the live VDOT if the result is more than 3 points lower than current. It returns `vdot_updated: false`. The athlete must resubmit with `force: true` to override. All results are always stored in `VDOTHistory` regardless.

**4. TRUEPACE is always optional**
Every code path that calls `fetch_weather()` wraps it in try/except and falls back gracefully. The plan is never blocked by a weather failure.

**5. Inline buttons edit the message in-place**
`handle_callback()` calls `edit_message_text()` on the existing message. This means tapping Today/Plan/Dashboard updates the same message rather than sending a new one. This only works while the message exists in Telegram's history — very old messages cannot be edited and will silently fall back to sending a new message.

**6. ConversationHandler state isolation**
The `LOG_*` states use `range(4)` (values 0–3). The `RACE_*` states use `range(10, 13)` (values 10–12). They must not overlap. The onboarding states use `range(11)` (values 0–10) but are in a separate ConversationHandler so there is no conflict.

---

## Database Schema

SQLite file created automatically at `coach.db` on first start.

### athletes
```sql
id                    INTEGER PRIMARY KEY
telegram_id           TEXT UNIQUE NOT NULL
name                  TEXT NOT NULL
plan_type             TEXT DEFAULT 'full'       -- 'full' or 'c25k'
current_weekly_mileage REAL                     -- NULL for c25k until graduation
vdot                  REAL                     -- NULL for c25k until graduation
race_distance         TEXT                     -- '5k','10k','half','marathon','ultra'
race_hilliness        TEXT DEFAULT 'low'       -- 'low','medium','high'
race_date             DATE
start_date            DATE NOT NULL
c25k_week             INTEGER                  -- 1-12, NULL for full plans
c25k_completed        BOOLEAN DEFAULT FALSE
latitude              REAL                     -- for TRUEPACE
longitude             REAL                     -- for TRUEPACE
run_hour              INTEGER DEFAULT 7        -- preferred run start hour
created_at            DATETIME
updated_at            DATETIME
```

### run_logs
```sql
id                    INTEGER PRIMARY KEY
athlete_id            INTEGER REFERENCES athletes(id)
week_number           INTEGER NOT NULL
day_name              TEXT NOT NULL             -- 'Mon'..'Sun'
planned_distance_km   REAL
actual_distance_km    REAL NOT NULL
duration_minutes      REAL
rpe                   INTEGER                  -- 1-10
notes                 TEXT
logged_at             DATETIME
```

### vdot_history
```sql
id                    INTEGER PRIMARY KEY
athlete_id            INTEGER REFERENCES athletes(id)
vdot                  REAL NOT NULL
source                TEXT    -- 'initial','race','time_trial','adjusted','c25k_graduation'
effective_date        DATE NOT NULL
created_at            DATETIME
```

---

## Common Problems and Fixes

**Bot doesn't respond to /start**
- Check the bot token in `.env` is correct (no extra spaces)
- Ensure the bot process is running (check Terminal 2 or `./run.sh` output)
- Make sure you are messaging the correct bot username

**`ModuleNotFoundError: No module named 'coach_core'`**
- You must run Python from inside the `run_coach/` directory
- Check that `coach_core/__init__.py` exists (even if empty)

**`sqlite3.OperationalError: no such table`**
- The database initialises automatically on first API start
- If you see this after the API is running, restart the API service

**Weather not showing in /plan**
- Weather requires a location to be set — use `/location` first
- Open-Meteo is a free external API — it can be slow or temporarily unavailable
- This is always silent — the plan shows without weather rather than failing

**VDOT race result says `vdot_updated: false`**
- The result dropped more than 3 VDOT points — this is the safety guard
- In `/lograce` the bot explains this and asks for confirmation
- To force-accept: resubmit `POST /log/race` with `"force": true`

**Telegram inline buttons stop working on old messages**
- Telegram only allows editing messages that are not too old
- The bot silently falls back to sending a new message if edit fails
- This is expected behaviour — not a bug

**`PORT already in use` on port 8000**
- Another service is using port 8000
- Change the port: `uvicorn coach_core.main:app --port 8001`
- Also update `API_BASE_URL=http://localhost:8001` in `.env`

---

## Production Deployment Notes

For running on a server (VPS, cloud instance):

**1. Use a process manager**
```bash
# systemd service example
[Unit]
Description=Run Coach API
After=network.target

[Service]
WorkingDirectory=/path/to/run_coach
ExecStart=uvicorn coach_core.main:app --host 0.0.0.0 --port 8000
Restart=always
EnvironmentFile=/path/to/run_coach/.env

[Install]
WantedBy=multi-user.target
```

**2. Firewall**
Port 8000 should NOT be exposed publicly — the Telegram bot talks to it on localhost only.

**3. Database backups**
```bash
# Back up the SQLite database
cp coach.db coach.db.backup.$(date +%Y%m%d)
```

**4. Webhook vs polling**
The current setup uses long-polling (`run_polling`). For production, Telegram webhooks are more efficient but require a public HTTPS URL. The bot works correctly on polling for all use cases including this system.

---

## Extending the System

**Adding a new Telegram command:**
1. Write the handler function in the appropriate `handlers/` file
2. Register it in `bot.py`: `app.add_handler(CommandHandler("newcmd", cmd_newcmd))`
3. Add to `set_bot_commands()` list in `bot.py`

**Adding a new API endpoint:**
1. Add the route function to the appropriate `routers/` file
2. No registration needed — routers are already included in `main.py`

**Adding a new city to TRUEPACE:**
1. Add an `SACity` entry to `SA_CITIES` list in `sa_cities.py`
2. Include aliases tuple (can be empty)
3. No other changes needed

**Changing VDOT drop threshold:**
1. Edit `VDOT_DROP_THRESHOLD = 3.0` in `coach_core/routers/log.py`

---

## Supplied Files Checklist

Verify all files are present before starting:

**Engine (10 files)**
- [ ] `adaptation.py`
- [ ] `c25k.py`
- [ ] `hills.py`
- [ ] `paces.py`
- [ ] `phases.py`
- [ ] `plan_builder.py`
- [ ] `sa_cities.py`
- [ ] `truepace.py`
- [ ] `volume.py`
- [ ] `workouts.py`

**API layer (7 files)**
- [ ] `database.py`
- [ ] `main.py`
- [ ] `models.py`
- [ ] `routers/athlete.py`
- [ ] `routers/log.py`
- [ ] `routers/plan.py`
- [ ] `routers/weather.py`

**Telegram bot (7 files)**
- [ ] `bot.py`
- [ ] `config.py`
- [ ] `formatting.py`
- [ ] `handlers/log_handler.py`
- [ ] `handlers/onboarding.py`
- [ ] `handlers/plan_handler.py`
- [ ] `handlers/ui.py`

**Config (3 files)**
- [ ] `requirements.txt`
- [ ] `run.sh`
- [ ] `.env.example`

**Total: 27 source files + 5 `__init__.py` files = 32 files**
