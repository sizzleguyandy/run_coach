# TR3D Strength Module Specification – Draft v0.1

**Target Release:** Post-v1.5 (planned v1.6)
**Freeze Date:** TBD
**Author:** @SizweUmoya_bot team

---

## 1. Module Overview

The TR3D Strength Module replaces the parked Hevy integration with a **built-in HTML5 mini-app** that delivers phase-appropriate strength workouts, logs user progress (weights, reps, sets, RPE), and **automatically adjusts running intensity** when the user gets significantly stronger.

### Key Principles

- **Phase-aligned** – strength programming matches the running phase (Base, Repetitions, Intervals, Taper).
- **Two workout modes** – time-based (circuits, plyometrics) and set-based (traditional gym).
- **Adaptive running load** – heavy strength increases trigger easy runs on subsequent running days.
- **Zero external dependency** – no API keys, no third-party fitness app.
- **Telegram-native delivery** – mini-app link sent via bot, no native app install.

---

## 2. Architecture Additions

The existing monolith (FastAPI + Telegram bot + n8n + SQLite) is extended as follows:

| Component | Technology | Role |
|-----------|------------|------|
| Strength mini-app | HTML5 / vanilla JS + Chart.js (GitHub Pages) | Workout logger, timer, set tracking, progress charts |
| Bot integration | `python-telegram-bot` | `/strength` command, daily reminders, mini-app launcher |
| Backend endpoints | FastAPI (new routes) | CRUD for templates, log storage, strength adaptation logic |
| Database | SQLite (new tables) | `strength_templates`, `strength_logs`, extended `athletes` |
| Scheduler | APScheduler (existing) | Send reminders on user-selected strength days |
| AI coaching | n8n (update existing workflows) | Inject strength summary into weekly/monthly reports and coach chat |

All new code lives in `coach_core/strength/` (backend) and a new GitHub Pages directory `strength/` for the mini-app.

---

## 3. Data Model (SQLite additions)

### 3.1 New table: `strength_templates`

Stores preset workouts per running phase.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | Auto-increment |
| `phase_name` | TEXT | `Base`, `Repetitions`, `Intervals`, `Taper` |
| `template_name` | TEXT | e.g., `Full Body A` |
| `workout_type` | TEXT | `time_based` or `set_based` |
| `structure` | JSON | Workout definition (see examples below) |
| `difficulty` | TEXT | `beginner`, `intermediate`, `advanced` |
| `created_at` | DATETIME | |

**Example `structure` for set-based:**
```json
{
  "exercises": [
    { "name": "Squat",       "sets": 3, "reps": 8, "rest_sec": 60, "weight_unit": "kg" },
    { "name": "Bench Press", "sets": 3, "reps": 8, "rest_sec": 60 }
  ]
}
```

**Example `structure` for time-based:**
```json
{
  "rounds": 4,
  "exercises": [
    { "name": "Jumping Jacks", "work_sec": 30, "rest_sec": 10 },
    { "name": "Push-ups",      "work_sec": 30, "rest_sec": 10 }
  ],
  "rest_between_rounds_sec": 30
}
```

### 3.2 New table: `strength_logs`

Stores each completed strength session.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | |
| `athlete_id` | INTEGER FK → `athletes.id` | |
| `log_date` | DATE | |
| `template_id` | INTEGER | optional, FK to `strength_templates` |
| `exercises` | JSON | Detailed log of each set/rep/weight/RPE |
| `total_volume_load` | FLOAT | Calculated: sum(sets × reps × weight_kg) |
| `session_rpe` | INTEGER | 1–10 overall perceived exertion |
| `notes` | TEXT | |
| `created_at` | DATETIME | |

**Example `exercises` log:**
```json
[
  {
    "name": "Squat",
    "sets": [
      { "weight": 60, "reps": 8, "rpe": 6 },
      { "weight": 70, "reps": 8, "rpe": 7 },
      { "weight": 70, "reps": 7, "rpe": 8 }
    ]
  }
]
```

### 3.3 Existing `athletes` table – new columns

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `strength_frequency` | INTEGER | 2 | Planned strength days per week (0–4) |
| `strength_level` | TEXT | `beginner` | `beginner`, `intermediate`, `advanced` |
| `strength_last_volume` | FLOAT | 0 | Total volume load of most recent session |
| `strength_last_date` | DATE | NULL | Date of last logged strength session |
| `strength_days` | TEXT | `Tue,Thu` | Comma-separated day names (e.g., `Mon,Wed,Fri`) |

---

## 4. Phase-Specific Workout Templates (Preloaded)

The system seeds `strength_templates` with the following workouts. At onboarding, the user chooses their `strength_level` (beginner / intermediate / advanced). The bot will recommend templates matching that level.

### Phase I – Base (2–3x / week)
- **Full Body A (beginner)** – 3×8–12: squats, push-ups, rows, planks
- **Full Body B (intermediate)** – 3×8–12: deadlifts, bench press, pull-ups, lunges
- **Full Body C (advanced)** – 4×6–8: power cleans, overhead press, weighted dips, Bulgarian split squats

### Phase II – Repetitions (2x / week, explosive)
- **Plyometric Circuit (time-based)** – 4 rounds: box jumps, clap push-ups, jump lunges, burpees (30s work / 15s rest)
- **Speed Strength (set-based)** – 3×5: hang cleans, squat jumps, medicine ball throws (light weight, fast reps)

### Phase III – Intervals (2x / week, low-moderate)
- **Maintenance Circuit** – 3 rounds: goblet squats (12 reps), push-ups (15), ring rows (10), planks (45s)
- **Mobility + Core** – time-based: leg raises, bird-dog, glute bridges, band pull-aparts (45s work / 15s rest)

### Phase IV – Taper (1x / week, very light)
- **Light Recovery** – 2 sets: bodyweight squats, light band walks, scapular push-ups, cat-camel stretch (no weights)

---

## 5. Mini-app Design (HTML5)

Hosted at `https://sizzleguyandy.github.io/run-coach-apps/strength/`.
**Tech stack:** vanilla HTML/CSS/JS, Chart.js for progress charts, localStorage for offline draft sessions.

### Screens

#### A. Dashboard (landing page)
- Next scheduled strength day & template recommendation.
- Chart: total volume load over last 4 weeks.
- Quick buttons: `Start workout` | `View history` | `Change strength days`.

#### B. Workout selection
- Shows templates for current running phase (fetched from `GET /strength/templates?phase=Base`).
- User picks one, or chooses `Custom workout` (free-form logging).

#### C. Active workout logger
- **Set-based mode:** For each exercise, shows a timer for rest period. User enters weight, reps, RPE (slider 1–10) per set. After all sets, moves to next exercise.
- **Time-based mode:** Displays a countdown timer for work/rest intervals. No weight input; user taps `Done` after each round.
- At any point, user can save as draft (stored in `localStorage`) and resume later.
- Final step: submit to `POST /strength/log`. Mini-app closes and bot sends confirmation.

#### D. Progress view
- Historical logs (list + chart).
- Personal records: best weight for a given exercise (e.g., Squat 1RM estimated via Epley formula).
- Weekly strength volume vs running volume (dual axis).

---

## 6. API Endpoints (New)

All endpoints are protected by `telegram_id` passed as a query parameter or header (same auth as existing endpoints).

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/strength/templates` | List templates for a given `phase` and `difficulty` (optional). |
| `GET` | `/strength/templates/{template_id}` | Get single template. |
| `POST` | `/strength/log` | Save a completed strength session. Body: `{telegram_id, log_date, template_id (optional), exercises, session_rpe, notes}`. |
| `GET` | `/strength/logs` | Get strength logs for an athlete (last 30 days). Query: `telegram_id`. |
| `PATCH` | `/athlete/{telegram_id}/strength` | Update strength settings: `strength_frequency`, `strength_days`, `strength_level`. |
| `GET` | `/strength/next-recommendation` | Returns recommended template for today based on phase and last logged strength date. |

---

## 7. Adaptation Logic: Heavy Weight → Easy Running

### 7.1 Detecting strength load increase

When a strength session is logged via `POST /strength/log`, the backend:
1. Retrieves the athlete's `strength_last_volume` and `strength_last_date`.
2. If the athlete has no previous log, stores the new volume and exits.
3. Calculates percentage increase:
   `increase_pct = (new_volume - last_volume) / last_volume * 100`
4. If `increase_pct >= 20` and the last strength session was within the last 7 days, the athlete is flagged as `high_strength_load = True` in a temporary cache (in-memory dict with expiry 48 hours).

### 7.2 Adjusting running intensity

When the bot fetches today's running session (via `/today` or daily reminder), it checks the flag:

- If `high_strength_load == True` **and** today's running session type is **not** Easy, the system:
  - **Downgrades** the session to Easy pace at the same duration.
  - Adds a note: *"Heavy strength load detected – intensity reduced to Easy for recovery."*
  - Sends a message: *"You increased your strength volume by 25% yesterday. To avoid overtraining, today's intervals are replaced with 40 min easy running."*
- If the flag is set but today is a rest day or already Easy, no change.
- Flag clears after 48 hours or after the next running session, whichever comes first.

### 7.3 Conflict resolution: strength + running on same day

- Bot reminds the user to **do strength first** (or at least 6 hours before running if possible).
- Running session is **automatically capped at Easy intensity**.
- If user logs strength after running, the system warns that next run will be Easy.

### 7.4 Weekly adaptation endpoint modification

The existing `/log/{telegram_id}/adapt` (running adaptation) is extended:
- If the athlete logged ≥2 strength sessions in the past week **and** average session RPE ≥8, the volume increase for running is halved (e.g., from +3% to +1.5% for aggressive profile).
- If the athlete missed strength days, no penalty — only running compliance matters.

---

## 8. Bot Commands & Scheduler Integration

### 8.1 New commands

| Command | Handler | Description |
|---------|---------|-------------|
| `/strength` | `strength.cmd_strength` | Show inline menu: `Log workout` \| `View progress` \| `Set strength days` |
| `/strength log` | – | Opens mini-app directly (no intermediate menu). |
| `/strength history` | – | Sends a summary of last 7 days (text + link to mini-app chart). |

### 8.2 Scheduler reminders

The existing hourly job (`send_daily_reminders`) is enhanced:
- For each athlete, if `today` is in `strength_days` and it is at `run_hour - 2h`, send:
  > *"💪 Strength day! Your [Base Phase] workout is ready. [Open mini-app]"*
- If the athlete has not logged strength for 3 consecutive scheduled days, a nudge is sent:
  > *"Haven't seen you in the gym. A quick 15-min circuit can boost your running economy."*

### 8.3 Settings menu update

The existing `Settings → Training Days` flow gains a **Strength days** step after easy days:
- Bot asks: *"Which days do you prefer for strength training? (max 4 days)"*
- User selects from buttons: Mon, Tue, Wed, Thu, Fri, Sat, Sun.
- Saves to `athletes.strength_days`.

---

## 9. n8n Workflow Updates

Three existing workflows gain strength context.

### 9.1 `today-coach` payload additions
```json
{
  "strength_today": true,
  "strength_template": "Full Body A (Base)",
  "strength_note": "Do strength before running – today's run is Easy only."
}
```

### 9.2 `help-coach` payload additions
```json
{
  "strength_summary": "Last strength: 2 days ago, volume 2400 kg (+15% vs previous).",
  "strength_frequency": 2,
  "strength_level": "intermediate"
}
```

### 9.3 `report-coach` weekly & monthly additions
- **Weekly:** sessions logged / planned, volume change vs previous week, impact on running.
- **Monthly:** total strength volume, most improved exercise, recommendation for next phase.

---

## 10. Implementation Plan (Post-v1.5)

| Phase | Tasks | Estimated effort |
|-------|-------|-----------------|
| **1. Database & Backend** | Create new tables, write migration script, implement API endpoints + adaptation logic. | 2 days |
| **2. Mini-app (MVP)** | Build HTML/CSS/JS with set-based logging, timer, basic charts. Host on GitHub Pages. | 3 days |
| **3. Bot integration** | Add `/strength` command, scheduler reminders, settings flow. | 1 day |
| **4. n8n updates** | Modify three workflows to include strength context. Test with sample payloads. | 1 day |
| **5. Phase templates** | Seed `strength_templates` with 12 workouts (3 per phase, 4 difficulty levels). | 0.5 day |
| **6. Testing & docs** | Unit tests for adaptation logic, user acceptance testing with beta group. | 2 days |
| **Total** | | **9.5 days** |

All work on `feature/strength-module` branch, merged only after v1.5 freeze is lifted.

---

## 11. Open Questions / Future Iterations

- **Progressive overload recommendations:** Should the mini-app suggest weight increases based on last RPE (e.g., "You rated squats RPE 6 – try +5 kg next time")?
- **Integration with C25K:** C25K users have no strength days by default – should we add optional bodyweight circuits?
- **Export strength data:** Allow users to download CSV of all logs via `/strength export`.
- **Social leaderboards (opt-in):** Compare total volume with friends (requires new privacy controls).

These are **post-v1.6** items unless prioritised.

---

## 12. Notes on Existing Planning Files

Two companion planning files exist alongside this spec:

- `strength_schema_POSTFREEZE.sql` — SQL schema aligned to this spec's data model, with seed data for Foundation and Running Strength templates.
- `strength_miniapp_POSTFREEZE.html` — Working HTML5 skeleton covering Screens B and C (set-based mode). Needs updates for: time-based mode, Screen A dashboard + Chart.js, Screen D progress view, and schema alignment (per-set weight/reps/rpe input instead of simple set-tap).
