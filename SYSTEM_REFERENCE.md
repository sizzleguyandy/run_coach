# Run Coach — Complete System Reference

> Single-file reference for the entire system: architecture, data flows,
> formulas, onboarding paths, and worked examples.
> All values are live outputs from the actual engine.

---

## 1. System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        TELEGRAM BOT                             │
│  /start → onboarding  /plan  /log  /progress  /paces /location  │
└─────────────────────┬───────────────────────────────────────────┘
                      │ HTTP (httpx)
┌─────────────────────▼───────────────────────────────────────────┐
│                     FASTAPI ENGINE  :8000                        │
│                                                                  │
│  routers/athlete.py   POST /athlete/  POST /athlete/c25k        │
│                        GET /athlete/{id}/paces                   │
│                       PATCH /athlete/{id}/location               │
│                        POST /athlete/{id}/graduate               │
│                                                                  │
│  routers/plan.py       GET /plan/{id}/current                   │
│                        GET /plan/{id}/week/{n}                   │
│                        GET /plan/{id}                            │
│                                                                  │
│  routers/log.py        POST /log/run                            │
│                        GET  /log/{id}/week/{n}/summary          │
│                        POST /log/{id}/adapt                      │
│                        POST /log/race                            │
│                        POST /log/{id}/c25k/adapt                 │
│                        POST /log/c25k/timetrial                  │
│                                                                  │
│  routers/weather.py    GET /weather/{id}/adjustment             │
│                        GET /weather/{id}/conditions              │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                     CORE ENGINE  (coach_core/engine/)            │
│                                                                  │
│  phases.py       Phase allocation (I/II/III/IV)                 │
│  volume.py       Volume progression + distance-specific taper   │
│  paces.py        VDOT → pace lookup table (Daniels 4th ed)      │
│  workouts.py     7-day session builder                           │
│  plan_builder.py Assembles full plan from all engine modules    │
│  hills.py        Hill work replacement logic                     │
│  adaptation.py   Closed-loop weekly adjustment + VDOT from race │
│  c25k.py         Couch to 5K programme (standalone)             │
│  truepace.py     Weather-based pace adjustment                   │
│  sa_cities.py    SA city → lat/lon lookup (30 cities)           │
└─────────────────────┬───────────────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────────────┐
│                     DATABASE  (SQLite / aiosqlite)               │
│  athletes    RunLog    VDOTHistory                               │
└─────────────────────────────────────────────────────────────────┘
                      ▲
                      │ fetch_weather()
          ┌───────────┴──────────┐
          │  Open-Meteo API      │
          │  (no key required)   │
          │  temp + dew point    │
          └──────────────────────┘
```

---

## 2. Data Model

### Athlete table

| Field | Type | Notes |
|---|---|---|
| telegram_id | string | Unique — Telegram user ID |
| name | string | |
| plan_type | string | `"full"` or `"c25k"` |
| current_weekly_mileage | float (km) | Nullable for C25K |
| vdot | float | 30–85. Nullable for C25K |
| race_distance | string | `"5k"` `"10k"` `"half"` `"marathon"` `"ultra"` |
| race_hilliness | string | `"low"` `"medium"` `"high"` |
| race_date | date | |
| start_date | date | First day of plan |
| c25k_week | int | Current C25K week (1–12). Null for full plans |
| c25k_completed | bool | True after week 12 + time trial |
| latitude | float | For TRUEPACE. Optional |
| longitude | float | For TRUEPACE. Optional |
| run_hour | int | Preferred run start hour 0–23. Default 7 |

### RunLog table

| Field | Type | Notes |
|---|---|---|
| athlete_id | FK | |
| week_number | int | |
| day_name | string | Mon/Tue/Wed/Thu/Fri/Sat/Sun |
| planned_distance_km | float | From plan |
| actual_distance_km | float | What was run |
| duration_minutes | float | Optional |
| rpe | int | 1–10. Optional |

### VDOTHistory table

Tracks every VDOT change. Source values: `initial` `race` `time_trial` `adjusted` `c25k_graduation`

---

## 3. Telegram Onboarding — Complete Flow

### Question sequence

```
ALL PATHS
  Q1  Experience level      [3 buttons]
  Q2  Name                  [free text]

  ↓                         ↓                        ↓
BEGINNER                RETURNING               EXPERIENCED
(C25K)                  (full plan)             (full plan)

  Q3  City / TRUEPACE   Q3  Weekly mileage       Q3  Weekly mileage
      [city keyboard]       [number, km/wk]          [number, km/wk]
      [Skip option]
                        Q4  VDOT method           Q4  VDOT method
                            [3 buttons]               [3 buttons]
  ↓
DONE (2 questions           ↓           ↓           ↓
+ location = 3)        Know it    Estimate    Not sure
                            │      from race       │
                        Q4a VDOT  Q4b Dist    (estimated
                            no.   Q4c Time     silently)
                            │          │           │
                            └──────────┴───────────┘
                                       │
                        Q5  Race distance     [buttons]
                        Q6  Race date         [YYYY-MM-DD]
                        Q7  Hilliness         [3 buttons]
                        Q8  City / TRUEPACE   [city keyboard]
                            [Skip option]
                        ↓
                        DONE (7–9 questions)
```

### Q1 buttons and what they trigger

| Button | Detected by | Plan type | VDOT needed |
|---|---|---|---|
| 🌱 Complete beginner | "beginner" or "never" | c25k | No |
| 🏃 Getting back into it | "back" or "occasionally" | full | Yes (with escape) |
| 💪 I run regularly | anything else | full | Yes (with escape) |

### Q4 VDOT method detail

**"I know my VDOT"** → athlete enters a number (25–85)

**"Estimate from race"** → pick distance → enter time → auto-calculated:
```
Example: 10k in 45:00 → VDOT 45.3
Example: Half in 1:45:00 → VDOT 42.6
Example: Marathon in 3:30:00 → VDOT 44.6
```
Time formats accepted: `mm:ss`, `h:mm:ss`, or decimal minutes

**"Not sure"** → VDOT estimated from mileage:
```
< 20 km/wk  → VDOT 35
20–34       → VDOT 38
35–49       → VDOT 42
50–69       → VDOT 46
70+         → VDOT 50
```

### Q8 / Q3 City (TRUEPACE)

- Shows 30-city keyboard (SA cities, 2 columns, alphabetical)
- "⏭️ Skip for now" always available — TRUEPACE is optional
- City resolves to lat/lon and is stored on the athlete profile
- Can be changed anytime via `/location`

---

## 4. Plan Types

### 4A. Full plan (phase-based)

**Entry requirements:** mileage, VDOT, race distance, race date, hilliness

**Plan duration:** `round((race_date - start_date).days / 7)` clamped to 6–24 weeks

#### Phase allocation

| Total weeks | Phase I | Phase II | Phase III | Phase IV |
|---|---|---|---|---|
| 6 | 3 | 0 | 0 | 3 |
| 8 | 4 | 0 | 0 | 4 |
| 10 | 5 | 0 | 0 | 5 |
| 12 | 6 | 0 | 0 | 6 |
| 14 | 6 | 0 | 2 | 6 |
| 16 | 6 | 0 | 4 | 6 |
| 18 | 6 | 0 | 6 | 6 |
| 20 | 6 | 2 | 6 | 6 |
| 22 | 6 | 4 | 6 | 6 |
| 24 | 6 | 6 | 6 | 6 |

Phase I = Base & Foundation. Phase II = Early Quality (R-pace). Phase III = Peak Quality (Intervals). Phase IV = Race Prep & Taper.

#### Target peak volume

| Race | Target peak (km) | Guardrail |
|---|---|---|
| 5k | 70 | min(70, mileage × 2.5) |
| 10k | 80 | min(80, mileage × 2.5) |
| Half | 90 | min(90, mileage × 2.5) |
| Marathon | 120 | min(120, mileage × 2.5) |
| Ultra | 140 | min(140, mileage × 2.5) |

Example: athlete at 40 km/wk targeting marathon → `min(120, 100) = 100 km` target peak

#### Volume progression

**Build phase:**
```
Week 1:        = current_weekly_mileage
Week i % 4=0:  = previous × 0.85   (cutback)
Week i (else): = min(previous × 1.10, target_peak)
```

**Phase IV taper (distance-specific):**

| Race | Taper weeks | Phase IV behaviour |
|---|---|---|
| 5k / 10k | 1 | Gradual –10%/wk to 70% floor, race week = target×35% |
| Half | 2 | Gradual then 2-week linear taper to target×35% |
| Marathon | 3 | Gradual then 3-week linear taper to target×35% |
| Ultra | 3 | Same as marathon |

**Worked example — 18-week marathon, start 40 km/wk:**

| Week | Volume | Note |
|---|---|---|
| 1 | 40.0 km | Start |
| 2 | 44.0 km | +10% |
| 3 | 48.4 km | +10% |
| 4 | 41.1 km | Cutback ×0.85 |
| 5 | 45.2 km | +10% |
| 6 | 49.7 km | +10% |
| 7 | 54.7 km | +10% |
| 8 | 46.5 km | Cutback ×0.85 |
| 9 | 51.2 km | +10% |
| 10 | 56.3 km | +10% |
| 11 | 61.9 km | +10% — actual peak |
| 12 | 52.6 km | Cutback ×0.85 |
| 13 | 61.9 km | Phase IV — hold at actual peak |
| 14 | 61.9 km | Hold at actual peak |
| 15 | 61.9 km | Hold at actual peak — final hard week |
| 16 | 52.9 km | Taper begins (step 0.333) |
| 17 | 44.0 km | Taper (step 0.667) |
| 18 | 35.0 km | Race week — forced target×0.35 |

> **Phase IV design intent:** The early Phase IV weeks hold at `actual_peak` — these are the final opportunity for high-volume quality work before the taper window begins. Holding (rather than gradually reducing) also eliminates a counter-intuitive volume spike that would otherwise occur if gradual reduction ran below the peak and then the taper interpolation reset back up to `actual_peak` at `taper_start_week`.

#### Weekly session structure

| Day | Session | % of weekly volume |
|---|---|---|
| Mon | Rest | 0% |
| Tue | Quality | ~10–12% (incl WU/CD) |
| Wed | Recovery | 9% |
| Thu | Medium-long | 18% |
| Fri | Recovery (or downhill reps for high hilliness) | 9% |
| Sat | Long run | 25–30% (race-dependent, marathon capped 32 km) |
| Sun | Cross-train / Rest (ultra: back-to-back long run) | 0% |

**Long run distances by race:**

| Weekly vol | 5k | 10k | Half | Marathon (capped) |
|---|---|---|---|---|
| 50 km | 12.5 km | 12.5 km | 13.8 km | 15.0 km |
| 70 km | 17.5 km | 17.5 km | 19.2 km | 21.0 km |
| 90 km | 22.5 km | 22.5 km | 24.8 km | 27.0 km |
| 110 km | 27.5 km | 27.5 km | 30.3 km | 32.0 km ← cap |
| 120 km | 30.0 km | 30.0 km | 33.0 km | 32.0 km ← cap |

**Ultra-specific weekly structure (Phases II and III only):**

| Day | Session | Rule |
|---|---|---|
| Sat | Long run | 30% of weekly volume, capped at 40 km |
| Sun | Back-to-back long run | 20% of weekly volume, capped at 40 km |

Both runs at easy (E) pace. Example at 140 km/wk: Saturday = 40 km (capped), Sunday = 28 km. Back-to-back structure is specific to Phases II and III — Phase I and IV use standard Sunday rest.

**Race week sessions (fixed distances, not % of volume):**

| Day | Session |
|---|---|
| Mon | Rest |
| Tue | 3 km easy + 4 × 100m strides |
| Wed | 4 km very easy |
| Thu | 3 km easy + 2 × 100m strides |
| Fri | Rest |
| Sat | 🏁 Race day |
| Sun | Rest |

---

## 5. VDOT Pace System

### Lookup table (Daniels 4th edition, selected values)

| VDOT | Easy | Marathon | Threshold | Interval | Repetition |
|---|---|---|---|---|---|
| 35 | 5:40 /km | 4:54 /km | 4:15 /km | 3:44 /km | 3:04 /km |
| 40 | 5:16 /km | 4:30 /km | 3:54 /km | 3:25 /km | 2:50 /km |
| 45 | 4:54 /km | 4:08 /km | 3:35 /km | 3:07 /km | 2:36 /km |
| 50 | 4:37 /km | 3:50 /km | 3:20 /km | 2:51 /km | 2:23 /km |
| 55 | 4:21 /km | 3:34 /km | 3:05 /km | 2:38 /km | 2:11 /km |
| 60 | 4:07 /km | 3:20 /km | 2:53 /km | 2:26 /km | 2:00 /km |
| 65 | 3:53 /km | 3:07 /km | 2:41 /km | 2:15 /km | 1:51 /km |
| 70 | 3:41 /km | 2:56 /km | 2:31 /km | 2:05 /km | 1:42 /km |

Full table covers VDOT 30–85. Non-integer values linearly interpolated.

### Quality session prescription

| Phase | Workout | Pace zone | Min reps | Max reps | Recovery |
|---|---|---|---|---|---|
| I | 100m strides | R | 6 | 12 | None (strides) |
| II | 200m repeats | R | 6 | 16 | Walk/jog 2–3 min |
| III | 800m intervals | I | 4 | 12 | Jog = rep time |
| IV | 1600m cruise | T | 3 | 8 | 1 min rest |

**Rep count formula:** `floor(weekly_volume × 0.20 / (rep_dist × recovery_factor))` clamped to min/max

**Example — VDOT 50, Phase III, 70 km/wk:**
```
quality_km = 70 × 0.20 = 14 km
reps = floor(14 / (0.8 × 1.5)) = floor(11.67) = 11 → capped at 12
Workout: 12 × 800m @ 2:51 /km, jog recovery
Total session: 2 km WU + 12×800m + 1 km CD ≈ 12.6 km
```

### VDOT from race performance

Formula: Daniels VO₂max equations

| Race | Time | Calculated VDOT |
|---|---|---|
| 5k | 15:00 | 69.6 |
| 5k | 20:00 | 49.8 |
| 5k | 25:00 | 38.3 |
| 5k | 30:00 | 30.8 |
| 10k | 40:00 | 51.9 |
| 10k | 45:00 | 45.3 |
| Half | 1:30:00 | 51.0 |
| Half | 1:45:00 | 42.6 |
| Marathon | 3:00:00 | 53.5 |
| Marathon | 3:30:00 | 44.6 |
| Marathon | 4:00:00 | 37.9 |

---

## 6. Hill Work System

### Replacement matrix

| Phase | Low hilliness | Medium hilliness | High hilliness |
|---|---|---|---|
| I (Base) | Standard strides | Standard strides | Standard strides |
| II (R-work) | Standard 200s | Alternates weekly | All replaced |
| III (Intervals) | Standard 800s | Alternates weekly | All replaced |
| IV (Threshold) | Standard cruise | Standard cruise | Standard cruise |

"Alternates weekly" = odd weeks within phase get hills, even weeks get flat intervals.

### Hill workout prescriptions

**Short hill sprints** (replaces Phase II R-work, high hilliness)
- Grade: 6–10% (steep)
- Duration: 10–15 sec all-out
- Reps: `clamp(floor(weekly_volume / 10), 8, 12)`
- Recovery: walk/jog back down

**Long hill repeats** (replaces Phase III I-work, high hilliness)
- Grade: 4–6% (moderate)
- Duration: ~3 min at I-pace effort
- Reps: `clamp(floor(weekly_volume / 15), 4, 8)`
- Recovery: jog down, equal time

**Downhill repeats** (added for high hilliness, Phase III/IV, even weeks only)
- Grade: 2–4% (gentle)
- Distance: 600m at T-pace effort
- Reps: `clamp(floor(weekly_volume / 12), 6, 10)` — reduced to 4 in Phase IV
- Placed on Friday recovery day

---

## 7. Closed-Loop Adaptation

Run after each completed week via `POST /log/{id}/adapt`.

### Volume modifier

```
compliance = actual_volume / planned_volume

< 80%    → modifier = 0.85  (–15%)
80–89%   → modifier = 0.95  (–5%)
90–105%  → modifier = 1.00  (no change)
> 105%   → modifier = 1.02  (+2%)
```

### RPE override (applied after volume modifier)

```
avg_rpe ≥ 9.0  → modifier = min(modifier, 0.85)
avg_rpe ≥ 8.5  → modifier = min(modifier, 0.90)
avg_rpe ≤ 5.0 AND compliance ≥ 0.95  → VDOT += 0.5 (max 85)
```

### Example outcomes

| Compliance | avg RPE | Result |
|---|---|---|
| 95% | 6 | Volume unchanged |
| 70% | 7 | –15% next week |
| 100% | 9.5 | –15% (RPE override) |
| 108% | 4.5 | +2% volume + VDOT nudge |
| 75% | 9.2 | –15% (both agree) |

---

## 8. Couch to 5K (C25K)

### Programme schedule

| Week | Session | Run minutes | Format |
|---|---|---|---|
| 1 | Mon/Wed/Fri | 6 min | 6 × (1 min run / 2 min walk) |
| 2 | Mon/Wed/Fri | 9 min | 6 × (1m30s run / 2 min walk) |
| 3 | Mon/Wed/Fri | 12 min | 6 × (2 min run / 2 min walk) |
| 4 | Mon/Wed/Fri | 15 min | 5 × (3 min run / 2 min walk) |
| 5 | Mon/Wed/Fri | 16 min | 4 × (4 min run / 2 min walk) |
| 6 | Mon/Wed/Fri | 20 min | 4 × (5 min run / 2 min walk) |
| 7 | Mon/Wed/Fri | 24 min | 3 × (8 min run / 2 min walk) |
| 8 | Mon/Wed/Fri | 25 min | 10 / 2 / 10 / 2 / 5 |
| 9–10 | Mon/Wed/Fri | 30 min | Continuous |
| 11 | Mon/Wed/Fri | 30 min | Continuous + 4 strides |
| 12 | Mon/Wed/Fri | 30 min | Continuous + 6 strides + 5k time trial |

No VDOT. All sessions at conversational effort only.

**Weather guidance (TRUEPACE for C25K):**

C25K runs are time-based — pace is never prescribed. When weather conditions are challenging, the system appends an effort note to the session description:

| Factor | Threshold | Message shown |
|---|---|---|
| ≤ 1.05 | Normal | No note |
| 1.05–1.10 | Warm | 🌡️ Warm conditions — keep effort conversational, slow down more than usual |
| > 1.10 | High heat | 🔴 High heat/humidity — run at very easy effort, shorten session if needed |

Requires athlete location to be set. Degrades silently if weather API is unavailable.

### Weekly adaptation

```
compliance = actual_run_minutes / planned_run_minutes (3 sessions × week's minutes)

≥ 80%  → advance to next week
60–79% → repeat same week
< 60%  → drop back one week (min week 1)
```

### Graduation to full plan

After week 12, system needs VDOT. Options:

1. **5k time trial logged** → VDOT calculated directly
2. **No time trial** → estimated from week 11/12 continuous run pace: `pace × 5.0 km = estimated 5k time`
3. **Fallback** → VDOT 32 (conservative beginner default)

| 5k Time Trial | VDOT | Starting weekly km |
|---|---|---|
| 20:00 | 49.8 | ~9.7 km/wk |
| 25:00 | 38.3 | ~9.7 km/wk |
| 30:00 | 30.8 | ~9.7 km/wk |

After graduation: `POST /athlete/{id}/graduate` with VDOT + race details → athlete flips to full plan.

---

## 9. TRUEPACE — Weather Adjustment

### Data source

Open-Meteo API (free, no key): `https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,dew_point_2m&timezone=auto`

Cached 1 hour per (lat, lon, run_hour) key.

### Adjustment formula

```
adjustment = 1.0
if dew_point > 10°C:   adjustment += (dew_point - 10) × 0.005
if temperature > 25°C: adjustment += (temperature - 25) × 0.005
adjustment = min(adjustment, 1.15)   ← capped at 15% slowdown
```

### Worked examples (VDOT 50, planned easy pace 4:37 /km)

| Temp | Dew Point | Factor | Easy pace | Warning |
|---|---|---|---|---|
| 20°C | 5°C | ×1.000 | 4:37 /km | — |
| 28°C | 18°C | ×1.055 | 4:52 /km | — |
| 30°C | 20°C | ×1.075 | 4:58 /km | — |
| 35°C | 25°C | ×1.125 | 5:12 /km | ⚠️ High heat |
| 45°C | 30°C | ×1.150 | 5:19 /km | ⚠️ High heat |

High heat warning fires when factor > 1.10. At this threshold the system also shows: *"Consider running earlier or reducing distance."*

For quality sessions (intervals, threshold): additional note shown — *"Focus on RPE rather than exact pace."*

### SA city lookup (for `/location` command)

30 cities pre-loaded with exact coordinates. Aliases supported.

| Input | Resolves to | Coordinates |
|---|---|---|
| "Cape Town", "CT", "capetown", "kaapstad" | Cape Town | -33.9249, 18.4241 |
| "Joburg", "Jozi", "JHB", "Egoli" | Johannesburg | -26.2041, 28.0473 |
| "PE", "Port Elizabeth", "Nelson Mandela Bay" | Gqeberha | -33.9608, 25.6022 |
| "PMB", "Maritzburg" | Pietermaritzburg | -29.6006, 30.3794 |
| "Mbombela" | Nelspruit | -25.4660, 30.9707 |
| "Mangaung", "Bloem" | Bloemfontein | -29.1167, 26.2167 |
| "Tshwane", "PTA" | Pretoria | -25.7479, 28.2293 |

Matching order: exact name → exact alias → prefix match → alias prefix match.

---

## 10. Complete User Journey Examples

### Example A — Beginner (C25K → Full plan)

```
Day 1
  /start
  → Q1: "🌱 Complete beginner"
  → Q2: "Sarah"
  → Q3: "Cape Town" (selected from keyboard)
  → Profile created. Week 1 delivered:
    Mon/Wed/Fri: 6 × (1 min run / 2 min walk)
    TRUEPACE active (Cape Town weather)

Week 12 (12 weeks later)
  Athlete runs 5k in 28:00
  → /log → logs time trial
  → POST /log/c25k/timetrial → VDOT 32.5 calculated
  → System sends graduation message

  Athlete chooses to target a half marathon:
  → POST /athlete/{id}/graduate
    { vdot: 32.5, race_distance: "half", race_date: "2027-03-15",
      race_hilliness: "low", current_weekly_mileage: 18 }
  → Full plan generated: 16 weeks, VDOT 32.5
```

### Example B — Experienced runner (full plan, race estimate)

```
/start
→ Q1: "💪 I run regularly"
→ Q2: "James"
→ Q3: 55 km/wk
→ Q4: "🏁 Estimate from a recent race time"
→ Q4b: "10k"
→ Q4c: "44:15"   (parsed as 44.25 min)
   → VDOT calculated: 45.8
   → System confirms: "Your VDOT is 45.8"
→ Q5: "marathon"
→ Q6: "2026-09-20"   (26 weeks out)
→ Q7: "🌄 Medium (rolling hills)"
→ Q8: "Durban"
→ Plan generated:
   - 24 weeks, VDOT 45.8
   - Phases: I=6, II=6, III=6, IV=6
   - Target peak: min(120, 55×2.5=137.5) = 120 km
   - Hill work: Phase II and III alternate weeks (medium hilliness)
   - TRUEPACE: Durban coords stored, 7 AM default
```

### Example C — `/plan` command with TRUEPACE

```
Athlete in Johannesburg, early March (T=28°C, dew=16°C)

/plan
→ Week 15 (Phase III): 6 × 800m @ I pace
  Planned I pace: 3:07 /km (VDOT 50)

→ TRUEPACE block appended:
  "🌡️ 28°C, dew point 16°C — adjust pace by +5.0%"
  Adjusted paces:
    Easy:        4:37 → 4:51 /km
    Threshold:   3:20 → 3:30 /km
    Interval:    2:51 → 3:00 /km  ← use this for today's 800s
  "⚡ For hard efforts, focus on RPE rather than exact pace."
```

### Example D — Weekly adaptation

```
Athlete planned 80 km, ran 62 km (77% compliance), avg RPE 8.7

POST /log/{id}/adapt
→ compliance = 0.775 → modifier = 0.85 (–15%)
→ RPE 8.7 ≥ 8.5 → modifier = min(0.85, 0.90) = 0.85 (unchanged)
→ planned_next_week = 76 km
→ adjusted_next_week = 76 × 0.85 = 64.6 km
→ coaching_notes:
   "⚠️ Only 77% of planned volume — next week reduced by 15%."
   "🟠 High RPE (8.7) — volume trimmed, keep intensity low."
```

---

## 11. File Map

```
run_coach/
├── run.sh                              Starts API + bot
├── requirements.txt
├── .env.example                        TELEGRAM_BOT_TOKEN, API_BASE_URL, DATABASE_URL
│
├── coach_core/
│   ├── main.py                         FastAPI app + lifespan init_db()
│   ├── database.py                     Async SQLite session
│   ├── models.py                       Athlete, RunLog, VDOTHistory
│   │
│   ├── engine/
│   │   ├── phases.py                   get_phases(weeks) → PhaseAllocation
│   │   ├── volume.py                   build_volume_curve() + get_taper_weeks()
│   │   ├── paces.py                    calculate_paces(vdot) → Paces (lookup table)
│   │   ├── workouts.py                 build_week_days() → 7-day session dict
│   │   ├── plan_builder.py             build_full_plan() → complete plan dict
│   │   ├── hills.py                    should_replace_with_hills() + hill prescriptions
│   │   ├── adaptation.py               adapt_next_week() + calculate_vdot_from_race()
│   │   ├── c25k.py                     build_c25k_week() + adapt_c25k_week() + compute_transition()
│   │   ├── truepace.py                 fetch_weather() + compute_adjustment() + get_truepace_block()
│   │   └── sa_cities.py                find_city() + 30-city lookup table
│   │
│   └── routers/
│       ├── athlete.py                  /athlete  (create, get, paces, location, graduate)
│       ├── plan.py                     /plan     (current, week/{n}, full)
│       ├── log.py                      /log      (run, summary, adapt, race, c25k)
│       └── weather.py                  /weather  (adjustment, conditions)
│
└── telegram_bot/
    ├── bot.py                          Application builder + all handlers registered
    ├── config.py                       TELEGRAM_TOKEN + API_BASE_URL from .env
    ├── formatting.py                   format_week() + format_paces() + format_c25k_week()
    │
    └── handlers/
        ├── onboarding.py               11-state ConversationHandler (all 3 paths)
        ├── plan_handler.py             /plan  /paces  /location
        └── log_handler.py             /log  /progress
```

---

## 12. Deployment

```bash
# 1. Clone and configure
cp .env.example .env
# Edit .env: set TELEGRAM_BOT_TOKEN

# 2. Start both services
./run.sh

# API docs:  http://localhost:8000/docs
# Bot:       active in Telegram once token is set
```

**Environment variables:**

| Variable | Required | Default |
|---|---|---|
| TELEGRAM_BOT_TOKEN | Yes | — |
| API_BASE_URL | No | http://localhost:8000 |
| DATABASE_URL | No | sqlite+aiosqlite:///./coach.db |
