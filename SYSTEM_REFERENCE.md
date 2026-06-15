# Tr3d Coaching Engine — Complete System Reference

> Single-file reference for the entire system: architecture, data flows,
> formulas, plan logic, and the full API surface.
> The engine is a **Daniels-based deterministic running coach** exposed as a
> FastAPI service that frontend apps (mobile + white-label web clients) call
> directly over HTTP. All values described here are live outputs from the
> actual engine.

---

## 1. System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    FRONTEND CLIENTS                                │
│  Mobile app + white-label web clients                              │
│  Each client stores a per-device athlete ID (UUID) and calls the   │
│  API directly. Onboarding, plan display, logging, coach chat.      │
└─────────────────────┬──────────────────────────────────────────────┘
                      │ HTTPS (JSON)  — CORS-gated
┌─────────────────────▼──────────────────────────────────────────────┐
│              FASTAPI ENGINE — "Tr3d Coaching Engine" v1.0.0          │
│                                                                     │
│  Routes are served twice:                                           │
│    /v1/...   versioned (preferred for all new integrations)         │
│    /...      legacy, unversioned (kept for existing clients)        │
│                                                                     │
│  routers/athlete.py   create / fetch / paces / location / anchors   │
│                       graduate / delete                             │
│  routers/plan.py      current week / week N / full plan             │
│  routers/log.py       run / week+month summary / adapt / race /c25k │
│  routers/weather.py   TRUEPACE adjustment / raw conditions          │
│  routers/predict.py   race presets / race-time prediction (V2)      │
│  routers/mobile.py    vo2x calc / link-code lookup / coach chat     │
│  routers/admin.py     dashboard / stats / athletes / vo2x override  │
│  (routers/strength.py  DISABLED — strength is static, app-side)     │
│                                                                     │
│  GET /health  → {"status":"ok","version":"1.0.0"}                   │
└─────────────────────┬──────────────────────────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────────────────────────┐
│                CORE ENGINE  (coach_core/engine/)                    │
│                                                                     │
│  phases.py            Phase allocation (I/II/III/IV)                │
│  volume.py            Volume curve + distance-specific taper        │
│  training_profiles.py Conservative vs Aggressive constants          │
│  paces.py             Daniels VO2X→pace formula + race prediction   │
│  workouts.py          Day-aware session builder (3/4/5-day weeks)   │
│  workout_templates.py Daniels session library (rotation, ultra)     │
│  plan_builder.py      Assembles the full plan from all modules      │
│  hills.py             Hill-work replacement logic                   │
│  adaptation.py        Closed-loop weekly adjustment + VO2X from race│
│  c25k.py              Couch-to-5K programme (standalone)            │
│  truepace.py          Weather-based pace adjustment                 │
│  predictor.py         V2 race-time predictor (onboarding)           │
│  race_presets*.py     SA + UK race preset catalogues                │
│  race_knowledge.py    RAG context for the coach chat                │
└─────────────────────┬──────────────────────────────────────────────┘
                      │
┌─────────────────────▼──────────────────────────────────────────────┐
│                DATABASE  (SQLite / aiosqlite, async)                │
│  athletes   run_logs   vo2x_history                                 │
│  strength_templates   strength_logs  (tables exist; module frozen)  │
└─────────────────────────────────────────────────────────────────────┘
                      ▲                         ▲
                      │ fetch_weather()         │ POST /mobile/coach
          ┌───────────┴──────────┐   ┌──────────┴───────────┐
          │  Open-Meteo API      │   │  n8n chat webhook    │
          │  (no key required)   │   │  (URL kept server-   │
          │  temp + dew point    │   │   side, never to     │
          └──────────────────────┘   │   the client)        │
                                     └──────────────────────┘
```

---

## 2. Data Model

### `athletes` table

| Field | Type | Notes |
|---|---|---|
| id | int PK | |
| telegram_id | string | **Unique external athlete ID.** Frontends store a per-device UUID and send it here as the athlete identifier. (Column name is historical — it is just the opaque client ID.) |
| name | string | |
| plan_type | string | `"full"` (phase-based) or `"c25k"` |
| current_weekly_mileage | float (km) | Nullable for C25K until graduation |
| vo2x | float | 30–85. Nullable for C25K |
| race_distance | string | `"5k"` `"10k"` `"half"` `"marathon"` `"ultra_56"` `"ultra_90"` (`"ultra"` kept as legacy alias) |
| race_hilliness | string | `"low"` `"medium"` `"high"` (default `low`) |
| race_date | date | |
| race_name | string | Free-text or preset display name |
| preset_race_id | string | ID of a chosen race preset (e.g. `two_oceans_marathon`) |
| start_date | date | First day of plan (snapped to the Monday of the signup week) |
| long_run_day | string | Preferred long-run day. Default `Sat` |
| quality_day | string | Preferred hard-session day. Default `Tue` |
| extra_training_days | string | Comma-separated, e.g. `"Wed,Thu"`. Default `Thu` |
| training_profile | string | `"conservative"` (default) or `"aggressive"` |
| c25k_week | int | Current C25K week (1–12). Null for full plans |
| c25k_completed | bool | True after week 12 + transition |
| latitude / longitude | float | For TRUEPACE. Optional |
| run_hour | int | Preferred run start hour 0–23. Default 7 |
| streak_weeks | int | Consecutive compliant weeks (≥80%) |
| total_badges | int | Badges earned (4 compliant weeks = 1 badge) |
| link_code | string | Short code (e.g. `ANDY-4821`) to link a profile across clients |
| anchor_runs | string (JSON) | Up to 2 fixed club/group runs: `[{"day":"Tue","km":10.0}]` |
| created_at / updated_at | datetime | |

### `run_logs` table

| Field | Type | Notes |
|---|---|---|
| athlete_id | FK | |
| week_number | int | |
| day_name | string | Mon…Sun |
| planned_distance_km | float | From plan (nullable) |
| actual_distance_km | float | What was run (required) |
| duration_minutes | float | Optional |
| rpe | int | 1–10. Optional |
| notes | string | Optional |
| prescribed_pace_min_per_km | float | Stored at log time; doesn't shift with VO2X |
| source | string | `"manual"` or `"treadmill"` |
| logged_at | datetime | |

### `vo2x_history` table

Tracks every VO2X change. `source` values: `initial`, `race`, `adjusted`,
`c25k_graduation`, `admin_adjusted`, `pace_adjusted`.

### `strength_templates` / `strength_logs`

Tables exist in the schema but the strength **router and tracking are disabled**.
Strength sessions are currently static and rendered app-side; the backend only
flags which days are strength days. Re-enable when DB-backed strength tracking
is built.

---

## 3. API Surface

Every route below is available **both** at `/v1/<path>` (preferred) and at the
unversioned `/<path>` (legacy). CORS origins are controlled by the
`ALLOWED_ORIGINS` env var (comma-separated; defaults to `*` for local dev).

### Athlete — `/athlete`

| Method | Path | Purpose |
|---|---|---|
| POST | `/athlete/` | Create a full (phase-based) profile |
| POST | `/athlete/c25k` | Create a C25K beginner profile (no VO2X/race needed) |
| POST | `/athlete/{id}/graduate` | C25K → full plan (sets VO2X, mileage, race; start_date reset to today) |
| GET | `/athlete/{id}` | Fetch profile |
| GET | `/athlete/{id}/paces` | Five Daniels training paces for current VO2X |
| GET | `/athlete/{id}/anchors` | Current anchor runs |
| PATCH | `/athlete/{id}/anchors` | Set/clear anchors (max 2, unique valid days, km > 0) |
| PATCH | `/athlete/{id}/location` | Store lat/lon + run_hour for TRUEPACE |
| DELETE | `/athlete/{id}` | Delete athlete + cascade run logs + VO2X history |
| GET | `/athlete/all` | **Admin** (`X-Admin-Key`) — all athletes, used by schedulers |

### Plan — `/plan`

| Method | Path | Purpose |
|---|---|---|
| GET | `/plan/{id}/current` | This week's plan (routes to C25K or full). Adds heat note for C25K, a `plan_note` for short (<14wk) plans, and applies the anchor overlay |
| GET | `/plan/{id}/week/{n}` | A specific week |
| GET | `/plan/{id}` | The complete plan (all weeks; C25K returns all 12) |

### Log — `/log`

| Method | Path | Purpose |
|---|---|---|
| POST | `/log/run` | Log a run (supports `source`, `prescribed_pace_min_per_km`) |
| GET | `/log/{id}/week/{n}/summary` | Week volume / sessions / avg RPE / per-run breakdown |
| GET | `/log/{id}/month/{year}/{month}/summary` | Calendar-month totals + per-week breakdown |
| POST | `/log/{id}/adapt?week_number=` | Closed-loop weekly adaptation (volume + VO2X + streaks/badges) |
| POST | `/log/race` | Log a race result → recompute VO2X (with drop guard, see §8) |
| POST | `/log/{id}/c25k/adapt?week_number=` | C25K weekly progression |
| POST | `/log/c25k/timetrial` | Log 5K time trial → compute VO2X + transition data |

### Weather (TRUEPACE) — `/weather`

| Method | Path | Purpose |
|---|---|---|
| GET | `/weather/{id}/adjustment?session_type=&run_hour=` | Weather-adjusted paces (C25K gets effort note only) |
| GET | `/weather/{id}/conditions?run_hour=` | Raw conditions + adjustment factor |

### Predict (V2 onboarding) — `/predict`

| Method | Path | Purpose |
|---|---|---|
| GET | `/predict/races?country=` | Race presets for the picker. `country` = `ZA` or `GB`; omit for all. Always appends a `custom` entry |
| POST | `/predict/` | Finish-time range, goal time, VO2X, training focus, warnings |

### Mobile / white-label — `/mobile`

| Method | Path | Purpose |
|---|---|---|
| GET | `/mobile/vo2x?distance_km=&time_minutes=` | Convert a race result to a VO2X score |
| GET | `/mobile/athlete/by-code/{code}` | Resolve a `link_code` to an athlete's ID + race summary |
| POST | `/mobile/coach` | Enriched coach-chat proxy → n8n (webhook URL stays server-side) |

### Admin — `/admin` (all require `X-Admin-Key: <ADMIN_SECRET>`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/admin/dashboard` | Web admin UI (browser login with the admin key) |
| GET | `/admin/stats` | Total athletes + plan-type breakdown |
| GET | `/admin/athletes` | All athletes with key fields + run-log counts |
| DELETE | `/admin/athletes/{id}` | Delete athlete + all their data |
| PATCH | `/admin/athletes/{id}/vo2x` | Override VO2X (20–85) and record in history |
| POST | `/admin/broadcast` | Mass push (legacy channel — see §13) |

### Meta

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness — `{"status":"ok","version":"1.0.0"}` |

---

## 4. Plan Types

### 4A. Full plan (phase-based)

**Inputs:** mileage, VO2X, race distance, race date, hilliness, training profile,
and the athlete's chosen training days.

**Duration:** `round((race_date - start_date).days / 7)` clamped to **6–24 weeks**.

#### Phase allocation (`phases.py`)

Phase I = Base. Phase II = Early Quality (R-pace). Phase III = Peak Quality
(Intervals). Phase IV = Race Prep & Taper.

- **Plans ≥ 12 weeks:** Phase I = 6, Phase IV = 6, the remaining weeks split
  between II and III. For plans **≥ 14 weeks**, Phase II is guaranteed a minimum
  of 2 weeks (`min(4, max(2, remaining // 3))`), Phase III takes the rest. A
  12-week plan has no Phase II (all remaining weeks → Phase III).
- **Short plans (6–11 weeks):** base + taper only — Phase I = `floor(weeks/2)`,
  Phase IV = `ceil(weeks/2)`, no II/III.
- Phase I and Phase IV are each floored at 3 weeks.

| Total weeks | I | II | III | IV |
|---|---|---|---|---|
| 6 | 3 | 0 | 0 | 3 |
| 8 | 4 | 0 | 0 | 4 |
| 10 | 5 | 0 | 0 | 5 |
| 12 | 6 | 0 | 0 | 6 |
| 14 | 6 | 2 | 0 | 6 |
| 16 | 6 | 2 | 2 | 6 |
| 18 | 6 | 2 | 4 | 6 |
| 20 | 6 | 2 | 6 | 6 |
| 22 | 6 | 3 | 7 | 6 |
| 24 | 6 | 4 | 8 | 6 |

#### Volume progression (`volume.py` + `training_profiles.py`)

Build rate, cutback depth, and the peak cap are governed by `training_profile`:

| Constant | Conservative (default) | Aggressive |
|---|---|---|
| Weekly build rate | ×1.08 (+8%) | ×1.12 (+12%) |
| Cutback (every 4th week) | ×0.82 | ×0.88 |
| Peak cap factor | 2.0 × starting mileage | 3.0 × starting mileage |

```
target_peak = min(PEAK_VOLUME_KM[race], mileage × peak_cap_factor)
target_peak = max(target_peak, mileage)

Build phase:
  Week 1        = current_weekly_mileage
  Week i % 4=0  = previous × cutback_factor
  Week i (else) = min(previous × build_rate, target_peak)
```

**Peak volume targets by distance:**

| Race | Target peak (km, midpoint) |
|---|---|
| 5k | 70 |
| 10k | 80 |
| half | 90 |
| marathon | 120 |
| ultra_56 (Two Oceans) | 100 |
| ultra_90 (Comrades) | 160 |

**Distance-specific taper (`TAPER_WEEKS`):** 5k/10k = 1, half = 2, marathon = 3,
ultra_56 = 2, ultra_90 = 3. Phase IV holds at the actual achieved peak, then
linearly tapers over the taper window down to `target_peak × 0.35` on race week.

#### Template base requirement — 48 km (21 km+ races)

Races **21 km and up** (`half`, `marathon`, `ultra_56`, `ultra_90`) require a
weekly base of **48 km** before the Phase 2 quality templates begin. Phase 1 is
therefore **dynamic**: it is extended as far as needed so that weekly volume
reaches 48 km by the time Phase 2 starts. Phase IV (taper) is always preserved;
the extra base weeks come out of Phases II/III. 5k/10k are exempt.

This is wired in `plan_builder._resolve_phases()` using
`phases.get_phases_with_base()` and `volume.base_phase_for_distance()`. The
result is reported on the plan in a `base_building` block (also surfaced on the
live week as `base_building_warning`), with one of four statuses:

| Status | Meaning |
|---|---|
| `ok` | Athlete reaches 48 km within the standard 6-week base — no change. |
| `extended` | Phase 1 lengthened so Phase 2 starts at ≥48 km; II/III shrink. |
| `no_time` | Not enough weeks before the taper to reach 48 km → base-building + taper only (Phase II = III = 0), with a warning. |
| `unreachable` | The profile's peak cap (`mileage × peak_cap_factor`) can't reach 48 km → base-only, with a warning. |

Because the conservative profile builds slowly (+8%/wk with ×0.82 cutbacks), a
marathoner generally needs to start around **40 km/wk** to unlock templates in an
18-week plan; lower starts come out base-only. The lever to change that is the
build/cutback constants in `training_profiles.py`, not the gate.

#### Weekly session structure (`workouts.py`)

The number of **running days scales with volume**, and sessions are placed on the
athlete's explicitly chosen days:

| Weekly volume | Running days | Layout |
|---|---|---|
| < 30 km | 3 | quality + medium-long + long |
| 30–50 km | 4 | quality + recovery + medium-long + long |
| > 50 km | 5 | quality + 2× recovery + medium-long + long |

Day placement: `long_run_day` → long run; `quality_day` → the hard session;
`extra_training_days` → recovery / medium-long, assigned by their position in the
week (the day right after quality is a recovery flush; mid-week extras become
medium-long). All other days are rest. Minimum session distances are enforced
(recovery ≥ 4 km, medium-long ≥ 5 km, long ≥ 7 km), and if planned distances
exceed 110% of weekly volume they're scaled back to ~105%.

**Long run:** `min(0.25 + 0.025 × distance_factor, 0.35)` of weekly volume;
marathon capped at 32 km; ultra fixed at 30%. Quality sessions are pulled from
the Daniels template library (`workout_templates.py`) and **rotate** week to week
within a phase. In Phase III/IV, long runs gain goal-pace / M-pace finishes for
half and marathon athletes.

**Race week** is anchored to the actual race day-of-week (`race_day_name`) and
counts backwards, so the taper structure is identical whether the race is on a
Saturday, Sunday, or any other day:

| Days before race | Session |
|---|---|
| 4 | 3 km easy + 4 × 100m strides |
| 3 | 4 km very easy |
| 2 | 3 km easy + 2 × 100m strides |
| 1 | Rest / 15 min walk |
| 0 | 🏁 Race day |
| (other) | Rest |

**Ultra athletes** (`ultra_56` / `ultra_90`) get back-to-back long runs on the
day after `long_run_day` in Phases II and III (both capped at 40 km), with
walk-break + nutrition guidance baked into the notes.

### 4B. C25K — see §7.

---

## 5. VO2X Pace System (`paces.py`)

Paces are **computed directly from the Daniels VO2-velocity formula**, not a
hard-coded lookup table, so they're exact across the full VO2X range (clamped
30–85).

**Intensity zones (% of VO2X):**

| Zone | % VO2X | Use |
|---|---|---|
| E — Easy | 70.0% | Conversational |
| M — Marathon | 81.0% | Goal marathon effort |
| T — Threshold | 88.0% | Comfortably hard, ~1-hour effort |
| I — Interval | 97.5% | Near VO2max repetitions |
| R — Repetition | 104.0% | Fast short reps, full recovery |

**Formula:**
```
VO2(v) = -4.60 + 0.182258·v + 0.000104·v²     (v in m/min)
v = (-0.182258 + √(0.182258² + 4·0.000104·(VO2X·pct + 4.60))) / (2·0.000104)
pace (min/km) = 1000 / v
```

Verified anchor (Daniels 4th ed.) — **VO2X 39:** E 6:14, M 5:33, T 5:12,
I 4:47, R 4:33 /km.

### Race-time prediction (`paces.py: predict_race_time`)

- **5k–marathon:** Daniels velocity formula at the appropriate intensity, with a
  ±2–2.5% spread.
- **Ultra:** Modified Riegel from the marathon baseline —
  `T = T_marathon × (D/42.195)^exp`. Exponent **1.10** for Two Oceans (56 km,
  ±3%), **1.12** for Comrades (90 km, ±5%).
- **Comrades** also applies a direction factor (up-run ×1.08, down-run ×1.0;
  down-run years: 2026, 2028, 2030, 2032) and maps the predicted time to a
  medal tier (Wally Hayward < 6:00, Gold < 7:30, Bill Rowan < 9:00,
  Silver < 10:00, Bronze < 11:00, Vic Clapham < 12:00).

### VO2X from race performance (`adaptation.py: calculate_vo2x_from_race`)

Daniels oxygen-cost + %VO2max equations; result clamped to 25–85. The inverse
(`vo2x_to_5k_minutes`) recovers a 5K time from a VO2X via binary search.

---

## 6. Hill Work System (`hills.py`)

Hill replacement depends on phase and `race_hilliness`:

| Phase | Low | Medium | High |
|---|---|---|---|
| I (Base) | Standard strides | Standard strides | Standard strides |
| II (R-work) | Standard reps | Alternates weekly | All replaced (hill sprints) |
| III (Intervals) | Standard intervals | Alternates weekly | All replaced (hill repeats) |
| IV (Threshold) | Standard cruise | Standard cruise | Standard cruise |

"Alternates weekly" = odd weeks within the phase get hills, even weeks stay flat.
For **high** hilliness in Phases III/IV, **downhill repeats** are added on
alternating Fridays (reduced volume during the Phase IV taper), and long runs in
Phases III/IV carry a hilly-course note.

---

## 7. Couch to 5K (`c25k.py`)

Standalone programme — no VO2X, no volume curve, no phases. **12 weeks ×
3 sessions/week (Mon/Wed/Fri)**; Tue/Thu rest-or-walk, Sat cross-train, Sun rest.
All runs are conversational effort.

| Week | Session |
|---|---|
| 1 | 6 × (1 min run / 2 min walk) |
| 2 | 6 × (1m30s run / 2 min walk) |
| 3 | 6 × (2 min run / 2 min walk) |
| 4 | 5 × (3 min run / 2 min walk) |
| 5 | 4 × (4 min run / 2 min walk) |
| 6 | 4 × (5 min run / 2 min walk) |
| 7 | 3 × (8 min run / 2 min walk) |
| 8 | 10 / 2 / 10 / 2 / 5 |
| 9–10 | 30 min continuous |
| 11 | 30 min continuous + 4 strides |
| 12 | 30 min continuous + 6 strides + optional 5K time trial |

Distance is estimated at a beginner pace of 8:00 /km.

**Weather guidance:** C25K runs are time-based, so pace is never prescribed.
When the TRUEPACE factor > 1.05 a warm note is appended; > 1.10 a high-heat note.

**Weekly adaptation (`adapt_c25k_week`):** compliance = actual ÷ planned run
minutes (3 sessions × that week's run minutes). ≥ 80% advance, 60–79% repeat,
< 60% drop back one week (floored at week 1).

**Transition to full plan (`compute_transition`):**

1. 5K time trial logged → VO2X via Daniels formula.
2. No trial → estimate from the week 11/12 continuous-run pace.
3. Fallback → VO2X 32 (conservative beginner default).

Then `POST /athlete/{id}/graduate` flips `plan_type` to `full` and resets
`start_date` so week 1 of the full plan begins today.

---

## 8. Closed-Loop Adaptation (`adaptation.py`)

Run after each completed week via `POST /log/{id}/adapt?week_number=`.

### Volume modifier (profile-driven)

```
compliance = actual_volume / planned_volume

< 80%    → under_penalty   (conservative −15% / aggressive −10%)
80–89%   → ×0.95  (−5%)
90–105%  → ×1.00  (no change)
> 105%   → over_boost      (conservative +1% / aggressive +3%)
```

### RPE override (applied after the volume modifier)

```
avg_rpe ≥ 9.0  → modifier = min(modifier, rpe9_cap)   (cons 0.85 / aggr 0.90)
avg_rpe ≥ 8.5  → modifier = min(modifier, rpe85_cap)  (cons 0.90 / aggr 0.95)
avg_rpe ≤ 5.0 AND compliance ≥ 0.95  → VO2X += 0.5 (max 85)
```

A VO2X change is persisted to `vo2x_history` (`source="adjusted"`). If fewer than
half the week's runs have an RPE, an "add RPE" tip is appended to the notes.

### Streaks & badges

`compliance ≥ 80%` counts as a completed week and increments `streak_weeks`;
4 consecutive completed weeks award a badge (`total_badges += 1`, streak resets).
A missed week resets the streak to 0.

### Race-result guard (`POST /log/race`)

```
new ≥ old            → accept (PR / unchanged)
old − new ≤ 3 pts    → accept with a caution note
old − new > 3 pts    → reject unless force=true (returns vo2x_updated=false)
```

All race results are written to `vo2x_history` regardless of whether they update
the live VO2X. If the current VO2X had been nudged up by the adaptation engine,
the response explains that the race recalibrates it to a race-validated baseline.

---

## 9. TRUEPACE — Weather Adjustment (`truepace.py`)

**Source:** Open-Meteo (free, no key) — `temperature_2m` + `dew_point_2m`,
`timezone=auto`. Cached in-memory for 1 hour per (lat, lon rounded to 2dp,
run_hour) key.

**Formula:**
```
factor = 1.0
if dew_point  > 10°C: factor += (dew_point  - 10) × 0.005
if temperature > 25°C: factor += (temperature - 25) × 0.005
factor = min(factor, 1.15)        # capped at 15% slowdown
```

`high_heat_warning` fires when factor > 1.10; for quality sessions a "focus on
RPE not pace" note is added. Adjusted paces are derived by scaling each planned
pace string by the factor. Degrades silently to planned paces if the weather
fetch fails.

---

## 10. Race-Time Predictor (`predictor.py`) — V2 Onboarding

Used during onboarding to set expectations and recommend a plan. Two paths:

- **Experienced / known fitness:** VO2X from a recent race (or a directly entered
  VO2X) → scaled to the target race via the Daniels formula. For distances ≤
  marathon with a known VO2X, no fitness modifier is applied (it would
  double-count); for ultras the volume-based modifier is applied.
- **Beginner:** a predicted 5K time from an ability bucket
  (`couch` 42m, `occasional` 38m, `run5k_slow` 35m, `run5k_reg` 30m,
  `run10k_reg` 27m; plus legacy aliases) → scaled up to the target distance.

The pipeline then applies a fitness modifier (from weekly mileage + longest run),
an improvement factor (depends on weeks-to-race, plan type, and whether VO2X is
known), and a final confidence range. Output: finish-time range, goal time,
VO2X, up to 5 training-focus tips, and warnings. `plan_type` maps to a training
profile: `balanced → aggressive`, `conservative → conservative`,
`injury_prone → conservative`.

### Race presets

`GET /predict/races` returns the catalogue (SA + UK), each with display name,
emoji, exact distance, hilliness, hill factor, elevation gain, and country.
`PRESET_HILL_FACTORS` is the **source of truth** for course hilliness — frontends
should fetch these at runtime rather than hardcoding them.

Current presets include — **SA:** Comrades, Two Oceans, Cape Town, Soweto,
Durban, Knysna Forest. **UK:** London, Manchester, Brighton, Edinburgh,
Yorkshire, Loch Ness. A `custom` option is always appended.

---

## 11. Coach Chat (`mobile.py: POST /mobile/coach`)

The client sends `{athlete_id, question}`. The server enriches it with the full
athlete + current-week context (name, race, training profile, paces, phase,
week number, weeks-to-race, taper-start week/date, RAG race knowledge), forwards
it to the configured **n8n** webhook, and returns the AI reply. The webhook URL
(`N8N_CHAT_WEBHOOK`) is never exposed to the client. If n8n is unconfigured or
times out (90 s read), a friendly fallback message is returned.

---

## 12. File Map

```
run_coach/
├── run.sh
├── requirements.txt
├── .env.example
│
├── coach_core/
│   ├── main.py            FastAPI app, CORS, /v1 + legacy routers, /health
│   ├── database.py        Async SQLite session + init_db()
│   ├── models.py          Athlete, RunLog, VO2XHistory, Strength* (frozen)
│   │
│   ├── engine/
│   │   ├── phases.py
│   │   ├── volume.py
│   │   ├── training_profiles.py
│   │   ├── paces.py
│   │   ├── workouts.py
│   │   ├── workout_templates.py
│   │   ├── plan_builder.py
│   │   ├── hills.py
│   │   ├── adaptation.py
│   │   ├── c25k.py
│   │   ├── truepace.py
│   │   ├── predictor.py
│   │   ├── race_presets.py / race_presets_sa.py / race_presets_uk.py
│   │   ├── race_knowledge.py
│   │   └── anchor_constants.py
│   │
│   └── routers/
│       ├── athlete.py
│       ├── plan.py
│       ├── log.py
│       ├── weather.py
│       ├── predict.py
│       ├── mobile.py
│       └── admin.py
│
└── (telegram_bot/ — legacy, no longer the primary client; see §13)
```

---

## 13. Deployment & Configuration

```bash
cp .env.example .env       # then edit values
./run.sh                   # starts the API
# API docs:  http://localhost:8000/docs
# Admin UI:  http://localhost:8000/v1/admin/dashboard
```

**Environment variables:**

| Variable | Required | Default | Purpose |
|---|---|---|---|
| ALLOWED_ORIGINS | No | `*` | Comma-separated CORS allowlist (set in production) |
| ADMIN_SECRET | For admin routes | — | `X-Admin-Key` value for `/admin/*` and `/athlete/all` |
| N8N_CHAT_WEBHOOK | For coach chat | — | n8n webhook for `/mobile/coach` |
| API_BASE_URL | No | `http://localhost:8000` | Used internally by the coach-chat context builder |
| DATABASE_URL | No | `sqlite+aiosqlite:///./coach.db` | DB connection string |

### Known legacy references (cleanup backlog)

The Telegram bot has been retired as the primary client, but a few server-side
references still assume it and should be cleaned up:

- `POST /admin/broadcast` pushes via the Telegram Bot API and needs
  `TELEGRAM_BOT_TOKEN`. It has no frontend-app equivalent yet.
- `log.py` fires VO2X "level-up" notifications by importing `telegram_bot`
  (wrapped in try/except, so it no-ops if the package is absent).
- The `athletes.telegram_id` column and `link_code` flow are named after the
  bot but now serve as the generic client athlete ID / cross-client link.
- The `telegram_bot/` package and its mentions in `run.sh` / `.env.example`
  are dormant.
```
