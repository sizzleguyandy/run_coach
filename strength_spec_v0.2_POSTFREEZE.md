# TR3D Strength Module Specification – v0.2

**Target Release:** Post-v1.5 (v1.6)
**Status:** Brainstorm complete — ready for implementation
**Author:** @SizweUmoya_bot team
**Last updated:** 2026-04-06

---

## 1. Module Overview

The TR3D Strength Module replaces the parked Hevy integration with a built-in HTML5 mini-app that delivers phase-appropriate strength workouts, logs user progress (sets, reps, weight, RPE), and automatically adjusts running intensity when a user lifts heavy.

Alongside this, the existing **treadmill session player** (`session.html`) is upgraded to auto-log completed sessions and allow real-time pace adjustment during intervals.

A new **VO2X pace-gap check** closes the feedback loop by detecting when users are consistently running below prescribed paces and nudging VO2X down by 0.5.

### Key Principles

- Phase-aligned — strength templates match the running phase (Base, Repetitions, Intervals, Taper).
- Set-based only — no circuit/time-based mode. A rest timer is available between sets.
- Zero external dependency — no third-party fitness apps.
- Telegram-native — mini-app opened directly by the bot, no install required.
- Andrew authors all strength sessions — the mini-app is a template that renders whatever sessions are provided.

---

## 2. Architecture Additions

The existing stack (FastAPI + SQLAlchemy + python-telegram-bot + SQLite) is extended:

| Component | Technology | Role |
|-----------|------------|------|
| Strength mini-app | HTML5 / vanilla JS (GitHub Pages) | Set-based workout logger with rest timer |
| Treadmill session player | `session.html` (GitHub Pages, existing) | Guided treadmill intervals — upgraded with auto-log + pace buttons |
| Bot integration | `python-telegram-bot` | `/strength` command, scheduler reminders |
| Backend endpoints | FastAPI (new routes) | Template CRUD, strength log storage, adaptation flag |
| Database | SQLite | 2 new tables, 5 new `athletes` columns, 1 new `run_logs` column |
| Scheduler | APScheduler (existing) | Strength day reminders, pace-gap VO2X check |
| n8n | Existing workflows | Strength summary injected into weekly/monthly reports |

All new backend code lives in `coach_core/routers/strength.py` and `coach_core/engine/strength_adaptation.py`.

---

## 3. Database Schema Changes

### 3.1 New table: `strength_templates`

Stores preset workouts per running phase. Andrew authors all session content.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | |
| `phase_name` | TEXT | `Base`, `Repetitions`, `Intervals`, `Taper`, `C25K` |
| `template_name` | TEXT | e.g., `Full Body A` |
| `structure` | JSON | Exercise list (see format below) |
| `difficulty` | TEXT | `beginner`, `intermediate`, `advanced` |
| `created_at` | DATETIME | |

**`structure` JSON format (set-based only):**
```json
{
  "exercises": [
    {
      "name": "Goblet Squat",
      "sets": 3,
      "reps": "10-12",
      "rest_sec": 60,
      "notes": "bodyweight or light KB"
    }
  ]
}
```

Notes on format:
- `reps` is a string to allow ranges (`"8-10"`) and unilateral notation (`"8/side"`).
- `rest_sec` drives the in-app rest timer between sets.
- `notes` is optional coaching cue shown under the exercise name.
- No `weight_unit` or `load_pct` — users enter their own weights during the session.

### 3.2 New table: `strength_logs`

One row per completed strength session.

| Column | Type | Description |
|--------|------|-------------|
| `id` | INTEGER PK | |
| `athlete_id` | INTEGER FK → `athletes.id` | |
| `log_date` | DATE | |
| `template_id` | INTEGER NULL | FK to `strength_templates` (NULL = custom) |
| `session_name` | TEXT | Snapshot of template name at log time |
| `exercises_done` | JSON | Per-exercise log: sets with weight, reps, RPE per set |
| `total_volume_load` | FLOAT | Sum of (sets × reps × weight_kg) across session |
| `session_rpe` | INTEGER | 1–10 overall effort |
| `duration_min` | INTEGER NULL | Actual session length in minutes |
| `notes` | TEXT NULL | |
| `created_at` | DATETIME | |

**`exercises_done` JSON format:**
```json
[
  {
    "name": "Goblet Squat",
    "sets": [
      { "weight_kg": 16, "reps": 12, "rpe": 6 },
      { "weight_kg": 16, "reps": 11, "rpe": 7 },
      { "weight_kg": 16, "reps": 10, "rpe": 8 }
    ]
  }
]
```

### 3.3 `athletes` table — new columns

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| `strength_frequency` | INTEGER | 2 | Planned strength sessions per week (0–4) |
| `strength_level` | TEXT | `beginner` | `beginner`, `intermediate`, `advanced` |
| `strength_days` | TEXT | `Tue,Thu` | Comma-separated day names |
| `strength_last_volume` | FLOAT | 0 | `total_volume_load` from most recent session |
| `strength_load_expires_at` | DATETIME NULL | NULL | When the running intensity block clears. NULL = no block active. |

`strength_load_expires_at` replaces the previously planned in-memory flag — persisted to DB so it survives bot restarts.

### 3.4 `run_logs` table — new column

| Column | Type | Description |
|--------|------|-------------|
| `prescribed_pace_min_per_km` | FLOAT NULL | Pace the system prescribed for this session at log time. Stored once — does not change if VO2X is later adjusted. |

Actual pace is always derived from existing data: `duration_minutes / actual_distance_km`. No additional column needed.

### 3.5 Migration notes

- Run as `ALTER TABLE` statements (no destructive changes to existing tables).
- After any `models.py` change: delete `coach.db` and restart (SQLite does not auto-add columns).
- All new columns are nullable or have defaults — no existing rows break.
- Single migration script: `migrations/v1.6_strength.sql`.

---

## 4. Strength Templates — Content

Andrew supplies all session content. The format to supply is plain text per session:

```
Session name:
Phase: Base / Repetitions / Intervals / Taper / C25K
Difficulty: beginner / intermediate / advanced

1. Exercise name — sets × reps — rest (seconds) — notes
2. ...
```

Once supplied, sessions are converted into `structure` JSON and inserted as SQL seed data.

### C25K strength programme

Twice weekly. Bands and bodyweight only — no gym access required. Fixed programme (same two sessions each week, authored by Andrew). No progression or adaptation logic — log it done or not done.

### Full-plan strength phases

| Phase | Frequency | Character |
|-------|-----------|-----------|
| Base | 2–3x / week | Foundational strength, higher reps |
| Repetitions | 2x / week | Explosive / speed-strength focus |
| Intervals | 2x / week | Maintenance, lower load |
| Taper | 1x / week | Very light, recovery-focused |

All sessions authored by Andrew and seeded at deployment.

---

## 5. Strength Mini-App (HTML5)

### Delivery

Bot opens the mini-app via Telegram WebApp. The workout structure is encoded inline in the WebApp URL `startParam` — no API fetch required on load. Simple and offline-tolerant.

Bot encodes: `template_id`, `template_name`, `exercises` JSON array.

### Screens

**A. Active session logger**

For each exercise:
- Name, sets × reps, coaching notes displayed.
- Per set: user enters weight (kg) and reps. RPE slider 1–10.
- After each set: rest timer counts down (driven by `rest_sec` from template).
- After all sets: moves to next exercise automatically.
- Progress bar across top: exercises completed / total.

**B. Finish screen**

- Overall session RPE (1–10 tap buttons).
- Optional free-text notes.
- Duration auto-calculated from session start time (editable).
- Submit → POSTs to `POST /strength/log`. Mini-app closes. Bot sends confirmation message.

### Rest timer

Countdown timer between sets, driven by `rest_sec` in the exercise definition. User can skip or add 30 seconds. No circuit/interval timing mode — timer is rest-only.

### Volume load calculation (client-side)

`total_volume_load = sum(weight_kg × reps)` across all completed sets. Sent in the POST payload. Backend stores it and uses it for adaptation logic.

---

## 6. Treadmill Session Player Upgrades (`session.html`)

Two changes to the existing mini-app:

### 6.1 Auto-log on completion

When the session ends, the mini-app POSTs directly to `POST /log/run` — same endpoint as the bot's manual `/log` ConversationHandler. No user input required.

Payload sent:
```json
{
  "telegram_id": "...",
  "week_number": ...,
  "day_name": "...",
  "planned_distance_km": ...,
  "actual_distance_km": ...,
  "duration_minutes": ...,
  "prescribed_pace_min_per_km": ...,
  "source": "treadmill",
  "rpe": null,
  "notes": null
}
```

Auth uses Telegram `initData` passed in the request header.

Distance and duration are computed from the session structure (intervals × paces × durations) — the mini-app already has this information to display the session.

### 6.2 Pace adjust buttons

During each interval block, two buttons — ▲ and ▼ — allow the user to shift their actual treadmill speed in **0.5 km/h increments**.

- Prescribed pace is displayed and remains unchanged (greyed reference).
- Actual pace updates live as the user adjusts.
- Both prescribed pace and actual pace (derived from final speed setting) are included in the auto-log POST.
- If the user adjusts pace, the session still counts as completed — compliance is not penalised.

---

## 7. Strength Adaptation Logic (Running Intensity Block)

When a strength session is logged (`POST /strength/log`), the backend compares the new `total_volume_load` against `athletes.strength_last_volume`.

### Recovery window rules

| Condition | Recovery block |
|-----------|---------------|
| First 30 days of athlete's plan | 36 hours always |
| Weight unchanged (same volume) | 24 hours |
| Weight increased (higher volume) | 48 hours |

"First 30 days" is determined by `log_date - athlete.start_date <= 30`.

The block is written as:
```python
athlete.strength_load_expires_at = datetime.utcnow() + timedelta(hours=recovery_hours)
athlete.strength_last_volume = new_total_volume_load
```

### Effect on running

When the bot fetches today's session (`GET /plan/{id}/current`) or sends a reminder:
- If `strength_load_expires_at` is in the future → downgrade any non-Easy session to Easy. Duration unchanged.
- Add coaching note: *"Heavy session yesterday — keeping today's run easy for recovery."*
- If the session is already Easy or a rest day → no change.
- Block clears automatically when `strength_load_expires_at` passes (no manual reset needed).

### Interaction with existing adaptation

The existing `adaptation.py` VO2X nudge (compliance-based) is unchanged. The strength block only affects session type for the duration of the block — it does not alter VO2X or weekly volume targets.

---

## 8. VO2X Pace-Gap Check

A new scheduled check runs once per day (suggested: 06:00 SA time, alongside existing scheduler).

### Logic

For each athlete with `plan_type = "full"`:

1. Fetch all `run_logs` from the past 14 days where `actual_distance_km > 0` and `duration_minutes > 0`.
2. For each log, calculate actual pace: `actual_pace = duration_minutes / actual_distance_km`.
3. Compare to `prescribed_pace_min_per_km`. If actual pace is **slower** than prescribed by more than 5% → mark as "below pace".
4. If ≥ 70% of sessions in the window are "below pace" → trigger VO2X adjustment.
5. Apply **−0.5 VO2X** to the athlete.
6. Write to `vo2x_history` with `source = "pace_adjusted"`.
7. Set a 14-day cooldown: do not re-evaluate this athlete until the cooldown expires.
8. Send a bot message explaining the change:
   > *"Your recent runs suggest your current paces may be a little high. I've adjusted your VO2X from 42.0 to 41.5 — your new paces will feel more manageable. Keep showing up and it'll come back up."*

### Applies to all logged runs

Both treadmill (auto-logged) and outdoor runs (manually logged via `/log`). The check is source-agnostic.

### Does not apply to

- C25K athletes (`plan_type = "c25k"`) — they have no VO2X.
- Athletes with fewer than 5 logged runs in the 14-day window — insufficient data.
- Athletes currently in a cooldown period.

### Cooldown storage

A new column `vo2x_pace_check_cooldown_until` (DATE NULL) on `athletes`. NULL = no cooldown active.

> **Note:** This adds a 6th new column to `athletes` beyond the 5 strength columns. Migration script covers all.

---

## 9. API Endpoints (New)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/strength/templates` | List templates. Query params: `phase`, `difficulty` (both optional). |
| `GET` | `/strength/templates/{id}` | Single template with full structure JSON. |
| `POST` | `/strength/log` | Log a completed strength session. Triggers adaptation block write. |
| `GET` | `/strength/logs` | Athlete's strength logs, last 30 days. Query: `telegram_id`. |
| `PATCH` | `/athlete/{telegram_id}/strength` | Update `strength_frequency`, `strength_days`, `strength_level`. |

Auth: `telegram_id` passed as query param or `X-Telegram-Init-Data` header (matching existing pattern).

---

## 10. Bot Commands & Scheduler

### New commands

| Command | Description |
|---------|-------------|
| `/strength` | Opens inline menu: `Log workout \| View history \| Settings` |

### Scheduler additions (in `reminder.py`)

- **Strength day reminder:** If today is in `athlete.strength_days` and time = `athlete.run_hour - 2h`, send: *"💪 Strength day — your [Phase] session is ready. [Open]"*
- **Missed sessions nudge:** If athlete has missed 3 consecutive scheduled strength days, send a single nudge (not repeated daily).
- **VO2X pace-gap check:** Daily at 06:00 SA — runs the pace-gap evaluation for all full-plan athletes.

### Settings update

Existing `Settings → Training Days` flow gains a **Strength days** step: user selects days from buttons (Mon–Sun, max 4). Saves to `athletes.strength_days`.

---

## 11. n8n Workflow Updates

Three existing workflows receive strength context fields in their payloads:

**`today-coach`** — if strength is scheduled today:
```json
{
  "strength_today": true,
  "strength_template": "Full Body A",
  "strength_note": "Do strength before your run — today's run is easy."
}
```

**`help-coach`** — always included if athlete has strength logs:
```json
{
  "strength_summary": "Last session: 2 days ago, volume 1840 kg (+12% vs previous).",
  "strength_level": "intermediate"
}
```

**`report-coach`** — weekly and monthly reports:
- Weekly: sessions logged vs planned, volume change vs prior week.
- Monthly: total volume, trend direction, phase-appropriate recommendation.

---

## 12. Implementation Plan

All work on branch `feature/strength-module`. Merged only after v1.5 freeze is lifted.

| Step | Tasks | Notes |
|------|-------|-------|
| 1. Migration script | `migrations/v1.6_strength.sql` — new tables + all new columns | Run once on deploy |
| 2. SQLAlchemy models | Add new tables + columns to `models.py` | Delete `coach.db` after |
| 3. Seed templates | Convert Andrew's sessions to SQL seed data | Awaiting session content |
| 4. FastAPI routes | `routers/strength.py` — 5 endpoints + adaptation logic | |
| 5. Pace-gap check | `engine/strength_adaptation.py` + scheduler hook | |
| 6. `/log/run` update | Add `prescribed_pace_min_per_km` field to log endpoint | |
| 7. Strength mini-app | Build `strength/index.html` on GitHub Pages | Set-based, rest timer |
| 8. Treadmill upgrades | Add auto-log POST + pace ▲▼ buttons to `session.html` | Read existing file first |
| 9. Bot commands | `/strength` command, settings flow, scheduler reminders | |
| 10. n8n updates | Add strength fields to 3 existing workflow payloads | |
| 11. Testing | Unit tests for adaptation logic + pace-gap check | |

---

## 13. Open Questions

None. All questions resolved during brainstorm on 2026-04-06.

---

## 14. Related Planning Files

- `strength_schema_POSTFREEZE.sql` — early schema draft (superseded by this spec v0.2; use this spec for implementation)
- `strength_miniapp_POSTFREEZE.html` — early mini-app skeleton (reference only; rebuild from this spec)
- `SYSTEM_BREAKDOWN.md` — full v2.x system reference (read before implementing any changes)
