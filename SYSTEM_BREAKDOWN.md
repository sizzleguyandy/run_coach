# TR3D Running Coach — Full System Breakdown

**Version:** 2.x (current active codebase at `run_coach/run_coach/`)
**Stack:** FastAPI + SQLAlchemy async + python-telegram-bot v20.x + SQLite
**Bot:** `@SizweUmoya_bot`
**API Base:** `http://localhost:8000`
**Authoring:** Bob — Run Coach AI Builder (specialist persona)

---

## 1. Architecture Overview

```
Telegram User
  │
  ▼
┌──────────────────────────────────────────────────────┐
│         TELEGRAM BOT  (telegram_bot/)                │
│  python-telegram-bot v20.x                           │
│  Handles: commands, conversations, inline buttons,  │
│  reminders, webhooks, ICS calendar export            │
└────────────────────┬─────────────────────────────────┘
                     │ HTTP POST/GET
                     ▼
┌──────────────────────────────────────────────────────┐
│         FASTAPI ENGINE  (coach_core/)               │
│  Handles: all business logic, VO2X, plans, logs    │
│  Pure engine modules: no I/O, deterministic         │
└────────────────────┬─────────────────────────────────┘
                     │ async SQLAlchemy
                     ▼
┌──────────────────────────────────────────────────────┐
│              SQLITE  (coach.db)                     │
│  athletes, run_logs, vo2x_history                   │
└──────────────────────────────────────────────────────┘
                     │
     ┌───────────────┼────────────────┐
     ▼               ▼                ▼
Open-Meteo API   n8n webhooks     GitHub Pages
(weather)       (AI coach,         (Mini Apps:
                reports,            session player,
                race eve)           crossing game,
                                    level-up screen)
```

**Key principle:** The Telegram bot is a thin UI layer. All business logic lives in `coach_core/engine/`. The bot only calls the API via `httpx` — it never imports engine modules directly.

---

## 2. Directory Structure

```
run_coach/
├── .env                          # Environment variables (secrets)
├── coach.db                       # SQLite database (delete after schema changes)
├── bot_persistence.pkl            # PTB session persistence (PicklePersistence)
│
├── coach_core/
│   ├── main.py                    # FastAPI app entry point, CORS, scheduler start
│   ├── database.py                # async SQLAlchemy engine + session factory
│   ├── models.py                  # SQLAlchemy ORM models (athletes, run_logs, vo2x_history)
│   │
│   ├── engine/                    # Pure business logic — no I/O, no database
│   │   ├── phases.py              # Phase allocation (I/II/III/IV) over N weeks
│   │   ├── volume.py              # Weekly volume curve + taper calculation
│   │   ├── paces.py               # VO2X → pace table (Daniels formula, interpolated)
│   │   ├── workouts.py            # 7-day session builder, dynamic run-day count
│   │   ├── workout_templates.py   # Daniels variety rotation per phase
│   │   ├── plan_builder.py        # Full plan assembly from all engine modules
│   │   ├── hills.py               # Hill workout substitution logic
│   │   ├── adaptation.py          # Closed-loop weekly adaptation (volume + VO2X nudge)
│   │   ├── c25k.py                # Couch to 5K programme + graduation
│   │   ├── truepace.py            # Open-Meteo weather fetch + pace adjustment
│   │   ├── sa_cities.py           # 30 SA cities, name/alias → lat/lon
│   │   ├── race_presets.py        # 6 SA race presets (dates, distance, hilliness)
│   │   ├── predictor.py            # Race time predictor (experienced + beginner paths)
│   │   ├── training_profiles.py    # Training profile presets (conservative/aggressive)
│   │   ├── race_knowledge.py       # RAG context for SA races (split tables, tips)
│   │   └── __init__.py
│   │
│   ├── routers/
│   │   ├── athlete.py             # Athlete CRUD, C25K, graduation, paces, location
│   │   ├── plan.py                # Current week, specific week, full plan (live-computed)
│   │   ├── log.py                 # Log run, log race, week summary, adaptation
│   │   ├── weather.py             # TRUEPACE adjustment, raw conditions
│   │   ├── admin.py               # Broadcast (X-Admin-Key auth, rate-limited)
│   │   └── predict.py             # Race prediction endpoint
│   │
│   └── billing.py                  # Loyalty/reward system (streak → 50% off)
│
├── telegram_bot/
│   ├── bot.py                     # PTB Application builder, handler registration, error handler
│   ├── config.py                  # TELEGRAM_TOKEN, API_BASE_URL from .env
│   ├── formatting.py              # All message text + inline keyboard builders (HTML)
│   ├── ics_generator.py           # Week plan → .ics calendar file
│   │
│   └── handlers/
│       ├── ui.py                  # Today / Plan / Dashboard / Paces / Settings views
│       ├── onboarding_v2.py       # V2 onboarding ConversationHandler (18 states)
│       ├── onboarding.py          # Legacy — retained for _parse_race_time utility only
│       ├── log_handler.py         # /log ConversationHandler (4 states)
│       ├── plan_handler.py        # /plan /paces /location commands
│       ├── coach_chat.py          # /ask ConversationHandler (1 state) — n8n RAG
│       ├── training_days.py       # Change training days ConversationHandler (4 states)
│       ├── reminder.py            # APScheduler: daily reminders, race prep milestones,
│       │                           #   Sunday weekly game, weekly/monthly AI reports,
│       │                           #   race eve briefings, VO2X level-up notifications
│       └── __init__.py
│
└── tests/
    └── (unit tests for engine modules)
```

---

## 3. Data Models (SQLAlchemy, SQLite)

### `athletes`
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `telegram_id` | String UNIQUE | Telegram user ID |
| `name` | String | |
| `plan_type` | String | `"full"` or `"c25k"` |
| `current_weekly_mileage` | Float | Current weekly volume in km |
| `vo2x` | Float NULL | Fitness index — NULL for C25K pre-graduation |
| `race_distance` | String | `"5k"`, `"10k"`, `"half"`, `"marathon"`, `"ultra"` |
| `race_hilliness` | String | `"low"`, `"medium"`, `"high"` |
| `race_date` | Date | Target race date |
| `race_name` | String | Display name (e.g. `"Comrades Marathon"`) |
| `preset_race_id` | String NULL | Preset key (e.g. `"comrades"`) |
| `start_date` | Date | First day of week 1 (always a Monday) |
| `long_run_day` | String | Day abbreviation (e.g. `"Sat"`) |
| `quality_day` | String | Day abbreviation (e.g. `"Tue"`) |
| `training_profile` | String | `"conservative"` or `"aggressive"` |
| `extra_training_days` | String | Comma-separated day list (e.g. `"Wed,Fri"`) |
| `c25k_week` | Integer | Current C25K week (1-12) |
| `c25k_completed` | Boolean | |
| `latitude`, `longitude` | Float NULL | For TRUEPACE |
| `run_hour` | Integer | Preferred run start hour (0-23) |
| `streak_weeks` | Integer | Consecutive ≥80%-compliance weeks |
| `total_badges` | Integer | Accumulated reward badges |

### `run_logs`
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `athlete_id` | Integer FK | |
| `week_number` | Integer | |
| `day_name` | String | `"Mon"`, `"Tue"`, etc. |
| `planned_distance_km` | Float NULL | |
| `actual_distance_km` | Float | |
| `duration_minutes` | Float NULL | |
| `rpe` | Integer NULL | Rate of perceived exertion 1-10 |
| `notes` | String NULL | Free text |

### `vo2xi_history`
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | |
| `athlete_id` | Integer FK | |
| `vo2x` | Float | |
| `source` | String | `"initial"`, `"race"`, `"adjusted"`, `"c25k_graduation"` |
| `effective_date` | Date | |

---

## 4. Engine Modules (Pure Logic — No I/O)

### 4.1 `phases.py`
Allocates a training block into Jack Daniels' 4 phases.

```
Input:  total_weeks (int), race_distance (str)
Output: PhaseAllocation(n=phase_I, n=phase_II, …, phase_IV, phases_by_week=[…])

Rules:
  marathon ≥18wk → Phase I(6) II(4) III(6) IV(2)
  marathon 12-17wk → no Phase I, starts Phase II directly
  marathon <12wk → aerobic-only (no quality)
  half     → Phase I shorter, Phase III compressed
  5k/10k   → no Phase I, immediate quality
  ultra    → extended Phase I, slower Phase III build
```

### 4.2 `volume.py`
Builds the weekly volume curve.

```
Input:  peak_mileage (km), race_distance, PhaseAllocation
Output: volume_curve (list of km per week), taper_weeks (int)

Rules:
  Build rate: conservative=+8%/wk, aggressive=+12%/wk
  Taper (marathon): 3 weeks — 90%, 80%, 70% of peak
  Taper (half): 2 weeks — 85%, 70%
  Phase IV "holds at actual_peak" — early Phase IV stays at peak, taper resets down
```

### 4.3 `paces.py`
VO2X → training pace table using Jack Daniels' velocity formula.

```
Input:  vo2x (float)
Output: Paces(easy_min_per_km, marathon_min_per_km, threshold_min_per_km,
              interval_min_per_km, repetition_min_per_km)

Velocity formula:
  v = (−0.182258 + √(0.03322 + 0.000416 × (VO2X×intensity + 4.6))) / −0.000208
  pace_min_per_km = 1000 / v

Intensity by zone:
  Easy: 70–79% VO2X
  Marathon: 80–89% VO2X
  Threshold: 88–92% VO2X
  Interval: 95–100% VO2X
  Repetition: >100% VO2X (VO2max peak)

All values are precomputed at integer VO2Xs 30–85 and stored in a lookup table.
Interpolation between table entries for sub-integer VO2X values.
```

### 4.4 `workouts.py`
Builds the 7-day training week.

```
Input: phase, week_in_phase, planned_volume_km, long_run_day, quality_day,
       extra_training_days, race_hilliness, training_profile
Output: days (dict keyed by "Mon"…"Sun")

Dynamic run-day count:
  <30 km/wk  → 3 days: quality + medium-long + long
  30–50 km/wk → 4 days: quality + recovery + medium-long + long
  >50 km/wk  → 5 days: quality + recovery + medium-long + easy + long

Session type rotation per phase:
  Phase I  → easy + strides + 1 medium-long
  Phase II → quality (interval/repetition) + easy + medium-long
  Phase III → quality (threshold/tempo) + easy + long
  Phase IV → race-pace sessions + taper volume

Volume distribution per week:
  Easy: 45% | Quality: 20% | Medium-long: 20% | Long run: 15%
```

### 4.5 `workout_templates.py`
Daniels variety rotation. Defines which specific quality session runs each week per phase.

### 4.6 `plan_builder.py`
Assembles the complete training plan from all engine modules.

```
Input:  current_weekly_mileage, vo2x, race_distance, race_date,
        start_date, race_hilliness, long_run_day, quality_day,
        training_profile, extra_training_days
Output: full plan dict with all weeks

Plan is live-computed — never stored in database.
Each GET /plan/{id}/current or /plan/{id}/week/{n} runs build_full_plan() fresh.
```

### 4.7 `hills.py`
Hill workout substitution based on race hilliness.

```
Input: phase, race_hilliness, week_number_in_phase
Output: bool (should_replace_with_hills)

Rules:
  Phase I  → never (strides are already hill-like)
  Phase II → medium: 50% (alternate weeks), high: always
  Phase III → medium: 50%, high: always
  Phase IV → no replacement, high adds downhill/terrain note to long run
```

### 4.8 `adaptation.py`
Closed-loop weekly adaptation.

```
Input:  planned_next_volume, WeekSummary(actual_volume, avg_rpe, sessions_completed),
        current_vo2x, training_profile
Output: (adjusted_volume, new_vo2x, coaching_notes[])

VO2X nudge:
  Compliance ≥80% → +0.5 VO2X (capped at old_vo2x + 1.5)
  Compliance 60–80% → no change
  Compliance <60% → −0.5 VO2X (min 20)

Volume adaptation:
  Conservative: ±8% max change per week
  Aggressive: ±12% max change per week
```

### 4.9 `c25k.py`
Couch to 5K — 12-week beginner programme.

```
Input:  week_number (1–12), weather_factor (optional)
Output: C25K week dict

Programme structure (alternating walk/run intervals, week by week):
  W1:  20 min → 60s run / 90s walk × 8
  W2:  20 min → 90s run / 2min walk × 6
  W3:  22 min → 90s run / 90s walk × 8
  …    (continues progressing)
  W9+: 30+ min continuous running
  W12: 30 min continuous (graduation-ready)

Weather factor: increases run intervals by 10% in heat (TRUEPACE > 1.05).

Adaptation (c25k_week advance/repeat):
  ≥80% of planned run minutes → advance
  60–80% → repeat week
  <60% → drop back one week
```

### 4.10 `truepace.py`
Open-Meteo weather → pace adjustment factor.

```
Input:  latitude, longitude, run_hour (0–23)
Output: WeatherAdjustment(factor, adjustment_pct, high_heat_warning)

Formula (dew point method):
  T = air temp (°C), D = dew point (°C)
  humidity_effect = max(0, (D − 10) / 10)          # 0–1
  heat_effect    = max(0, (T − 18) / 17)            # 0–1
  factor = 1 + (humidity_effect + heat_effect) × 0.15
  factor capped at 1.15 (15% slowdown maximum)

Wind: separate warning if wind > 10 km/h ("headwind detected — slow your pace")
```

### 4.11 `predictor.py`
Race time prediction for V2 onboarding.

```
Two paths:

EXPERIENCED (has recent race result):
  1. Calculate VO2X from recent race (Daniels formula)
  2. Scale marathon-equivalent to target distance + hill penalty
  3. Apply fitness modifier based on weekly mileage vs race distance
  4. Apply improvement factor (weeks to race × plan aggressiveness)
  5. Output low/high range (5–10% spread)

BEGINNER (no recent race):
  1. Map ability level → 5K time (from BEGINNER_5K_TIMES table)
  2. Calculate VO2X from that 5K time
  3. Scale to target distance × ability-based multiplier
  4. No fitness modifier (beginner, unconstrained)
  5. Apply beginner improvement factor (higher rate — aerobic gains are rapid)
  6. Wider range (±12%)

Both paths: skip fitness_modifier for VO2X-direct athletes (modifier already baked in)
```

### 4.12 `race_presets.py`
6 famous South African race presets. Each preset has: exact distance, race_distance label, hilliness, hill_factor, display_name, description, next race date calculator, coordinates.

```
comrades → 89.3km, ultra_90, high (0.18), October (first Sunday)
two_oceans → 56km, ultra_56, high (0.125), March/Easter
capetown_marathon → 42.2km, marathon, medium (0.05), May
soweto_marathon → 42.2km, marathon, medium (0.07), July
om_die_dam → 50km, ultra_56, high (0.10), August
durban_city_marathon → 42.2km, marathon, medium (0.07), July
```

### 4.13 `sa_cities.py`
30 SA cities with lat/lon and name aliases (joburg, capetown, jozi, etc.).

---

## 5. API Endpoints

### Athlete
| Method | Path | Description |
|---|---|---|
| `POST` | `/athlete/` | Create full-plan athlete profile |
| `POST` | `/athlete/c25k` | Create C25K athlete profile |
| `POST` | `/athlete/{telegram_id}/graduate` | Graduate C25K → full plan |
| `GET` | `/athlete/{telegram_id}` | Get athlete |
| `GET` | `/athlete/{telegram_id}/paces` | Get VO2X training paces |
| `PATCH` | `/athlete/{telegram_id}/location` | Set lat/lon/run_hour |
| `DELETE` | `/athlete/{telegram_id}` | Delete athlete + cascade history |
| `GET` | `/athletes/all` | All athletes (for scheduler) |

### Plan
| Method | Path | Description |
|---|---|---|
| `GET` | `/plan/{telegram_id}/current` | This week's plan (live-computed) |
| `GET` | `/plan/{telegram_id}/week/{n}` | Specific week |
| `GET` | `/plan/{telegram_id}` | Full plan (all weeks) |

### Log
| Method | Path | Description |
|---|---|---|
| `POST` | `/log/run` | Log a training run |
| `GET` | `/log/{telegram_id}/week/{n}/summary` | Week log summary |
| `POST` | `/log/{telegram_id}/adapt` | Trigger weekly adaptation |
| `POST` | `/log/race` | Log race result, update VO2X (with guard) |
| `POST` | `/log/{telegram_id}/c25k/adapt` | C25K week adaptation |
| `POST` | `/log/c25k/timetrial` | End-of-C25K 5K time trial → VO2X |

### Weather
| Method | Path | Description |
|---|---|---|
| `GET` | `/weather/{telegram_id}/adjustment` | TRUEPACE adjusted paces |
| `GET` | `/weather/{telegram_id}/conditions` | Raw weather data |

### Admin
| Method | Path | Description |
|---|---|---|
| `POST` | `/admin/broadcast` | Send message to all athletes (rate-limited) |
| `GET` | `/admin/stats` | Platform stats |

### Prediction
| Method | Path | Description |
|---|---|---|
| `GET` | `/predict/races` | List all SA race presets |
| `POST` | `/predict/` | Race time prediction |

---

## 6. Telegram Bot Handlers

### ConversationHandlers (state machines)

**Onboarding V2** — 18 states (0–17):
```
NAME(0) → RACE_SELECT(1)
  → (preset) → EXPERIENCE(5)
  → (custom) → CUSTOM_DIST(2) → CUSTOM_HILLS(3) → CUSTOM_DATE(4) → EXPERIENCE(5)
        ↓
  EXPERIENCE(5):
    → has recent race → RECENT_DIST(6) → RECENT_TIME(7) → WEEKLY_KM(9)
    → beginner → BEGINNER_ABILITY(8) → (couch/run5k) → LOCATION(12)
                               → (else) → WEEKLY_KM(9)
    → VO2X direct → VO2X_INPUT(13) → WEEKLY_KM(9)
        ↓
  WEEKLY_KM(9) → LONGEST_RUN(10) → PLAN_TYPE(11)
                                             ↓
  LONG_RUN_DAY(14) → QUALITY_DAY(15) → EASY_DAYS(16) → EASY_DAY_2(17) → LOCATION(12)
                                                    ↓
  LOCATION(12) → [compute prediction → show result → await v2_confirm callback]
```

**Log** — 4 states (0–3): `LOG_DAY → LOG_DISTANCE → LOG_DURATION → LOG_RPE`

**Race log** — 3 states (10–12): `RACE_DIST → RACE_TIME → RACE_CONFIRM` (aliased as `LR_*` to avoid collision)

**Coach chat** — 1 state (100): `COACH_QUESTION`

**Training days change** — 4 states (200–203): `CHANGE_LONG_RUN_DAY → CHANGE_QUALITY_DAY → CHANGE_EASY_DAYS → CHANGE_EASY_DAY_2`

### Inline Button Callback Router (`handle_callback`)

Routes on `callback_data` string:
```
menu              → show_main_menu
today             → show_today
plan              → show_plan
dashboard         → show_dashboard
paces             → show_paces
settings          → show_settings
log               → redirect to /log
change_training_days → training_days_conv
calendar_ics      → generate + send .ics file
v2_confirm        → POST athlete to API, show plan created
v2_restart        → clear user_data, restart
```

### Primary Views (shared by command + callback)

All rendered in `ui.py` and callable from both `CommandHandler` entry points and `CallbackQueryHandler`:

- **`show_today`** — fetches athlete + week + summary + weather in parallel, renders today session, optional AI coaching message from n8n, TRUEPACE block, session player URL for quality sessions
- **`show_plan`** — renders 7-day week table with logged-done / today / tomorrow / future status
- **`show_dashboard`** — compliance bar, phase, weeks to race, VO2X, streak, reward progress, Two Oceans batch
- **`show_paces`** — two-column paces table (base vs outdoor-adjusted if weather available)
- **`show_settings`** — location instructions, training days link, reset option

---

## 7. Reminder Scheduler (`reminder.py`)

APScheduler fires `send_daily_reminders()` every hour (minute=0, SA timezone).

| Trigger | Time | Action |
|---|---|---|
| Hourly | `run_hour` SA | Session reminder (today's workout) to athletes scheduled for that hour |
| Sunday 19:00 | Weekly AI report | Sends n8n-generated feedback to all athletes who logged ≥1 session |
| Sunday 20:00 | Weekly game | Crossing Mini App with lives = runs logged, roads = weeks to race |
| Sunday 08:00 | Monthly AI report | Last Sunday of month — aggregate last 4 weeks |
| Daily 15:00 | Race eve briefing | Athletes whose race is tomorrow — n8n payload with prediction + weather |
| Race prep milestones | Per athlete's run_hour | 12/8/6/4/2 weeks + 3 days + race morning |

---

## 8. Key Algorithms

### VO2X from race result
```
T = finish time (minutes), D = distance (km)
v = (−0.182258 + √(0.03322 + 0.000416 × (4.6 − T×1000/D))) / −0.000208
VO2X = T×1000/D × 60 / v
```
(Exact inverse of the velocity formula in paces.py — verified against Daniels table)

### Race time prediction (experienced)
```
1. vo2x = calculate_vo2x_from_race(recent_dist, recent_time)
2. marathon_eq = recent_time × (42.195/recent_dist)^1.06
3. base = marathon_eq × (target_dist/42.195) × (1 + hill_factor)
4. If weekly_mileage/target < 0.5 → modifier = 1.25 (slow)
   If weekly_mileage/target < 0.9 → modifier = 1.12
   If weekly_mileage/target < 1.2 → modifier = 1.03
   Else → modifier = 0.97
5. improvement = weeks_to_race × plan_rate × profile_factor
6. goal = base × modifier × (1 − improvement)
7. range = goal × {0.95–1.05} (balanced) or {0.95–1.12} (conservative)
```

### TRUEPACE adjustment
```
humidity_effect = max(0, (dew_point − 10) / 10)
heat_effect     = max(0, (temperature − 18) / 17)
factor = 1 + (humidity_effect + heat_effect) × 0.15
CAPPED AT 1.15 (never more than 15% slowdown)
```

### C25K progression
```
Planned run minutes = sum of all run intervals in the week's schedule × 3 sessions
Compliance = actual_run_minutes / planned_run_minutes
≥80% → advance | 60–80% → repeat | <60% → drop back
```

---

## 9. External Integrations

### Open-Meteo (weather, no API key)
```
GET https://api.open-meteo.com/v1/forecast
  ?latitude={lat}&longitude={lon}
  &hourly=temperature_2m,dew_point_2m,wind_speed_10m,precipitation_probability
  &timezone=auto&forecast_days=2
```

### n8n webhooks (AI features, configurable via env vars)
| Webhook | Env var | Purpose |
|---|---|---|
| `/today` coaching | `N8N_TODAY_WEBHOOK` | AI coaching message for today's session |
| Coach chat | `N8N_CHAT_WEBHOOK` | RAG-powered Q&A with race knowledge context |
| Weekly/monthly AI reports | `N8N_REPORT_COACH_URL` | AI-generated training feedback |
| Race eve briefing | `N8N_REPORT_COACH_URL` | Pre-race AI briefing with weather + prediction |

### GitHub Pages Mini Apps (static HTML/JS)
| Mini App | URL env var | Purpose |
|---|---|---|
| Session player | `MINI_APP_BASE_URL` | Guided quality session with intervals + paces |
| Crossing game | `MINI_APP_BASE_URL` | Weekly Sunday game — lives × roads to race |
| Level-up | `MINI_APP_BASE_URL` | VO2X integer boundary celebration screen |

---

## 10. Known Patterns & Hard Rules

### parse_mode — HTML everywhere, never Markdown
Markdown v1/v2 crashes silently on apostrophes, en-dashes, bullet characters.
All message formatting uses HTML tags: `<b>`, `<i>`, `<code>`, `<pre>`.
Inline keyboard text must never contain emoji (breaks some Telegram clients).

### Module-level imports in PTB handlers
Runtime imports inside async handler functions crash silently — the PTB dispatcher swallows exceptions.
All engine imports must be at the top of the handler file, never inside functions.

### Error handler must be registered
`app.add_error_handler(error_handler)` in `bot.py` — all crashes are silently swallowed without it.

### TRUEPACE never blocks plan delivery
Every weather API call is wrapped in `try/except: pass`. Plan delivery is unconditional.

### coach.db must be deleted after schema changes
SQLite does not auto-add new columns to existing tables. After any `models.py` change, the database file must be deleted before restart.

### Plans are live-computed
`GET /plan/{id}/current` runs `build_full_plan()` fresh every time. No plan caching.
VO2X and mileage changes take effect immediately on the next `/plan` call.

### State integer collision prevention
Log handler states (0–3) and onboarding states (0–13) are in separate `ConversationHandler`s so no collision.
Race log states use 10–12 (aliased as `LR_RACE_DIST`, etc.) to avoid overlap.
Training days change uses 200–203.
Coach chat uses state 100.

### VO2X update guard on race logging
Race result drops VO2X by >3 points → requires `force=True` or shows guard message.
Always writes to `VO2XHistory` regardless. VO2X increases always accepted.

### User text sanitisation
Any user-supplied text echoed in `parse_mode="HTML"` must be HTML-escaped first (`html.escape()` or `_safe()`).

---

## 11. SOUL.md Identity Reminders

- **Name:** Bob
- **Role:** Senior Python Engineer + Run Coach System Builder
- **Personality:** Specialist on this system. Ship working features without breaking what works.
- **Hard rule:** No changes without permission. Wait for Andrew's explicit instructions.
- **Working directory:** `C:\Users\runfr\openclaw\run_coach` (the live system)

---

*Document generated: 2026-04-06. Source: `run_coach/run_coach/` — full codebase read and analysed.*
