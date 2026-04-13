# Run Coach — Complete Calculations Reference

> All formulas as implemented in code. Verified outputs included.  
> ⚠️ flags items that may need your review before going further.

---

## 1. Plan Duration

```
days_to_race   = race_date − start_date  (in days)
total_weeks    = round(days_to_race / 7)
total_weeks    = clamp(total_weeks, 6, 24)   ← hard floor 6, hard cap 24
```

---

## 2. Phase Allocation

### Rule

```
IF total_weeks >= 12:
    phase_I   = 6
    phase_IV  = 6
    remaining = total_weeks − 12
    phase_III = min(6, remaining)
    phase_II  = remaining − phase_III

IF total_weeks < 12:
    phase_I   = floor(total_weeks / 2)
    phase_IV  = ceil(total_weeks / 2)
    phase_II  = 0
    phase_III = 0

Minimums enforced: phase_I >= 3, phase_IV >= 3
```

### Full Output Table (every valid plan length)

| Weeks | Phase I | Phase II | Phase III | Phase IV |
|-------|---------|----------|-----------|----------|
| 6     | 3       | 0        | 0         | 3        |
| 7     | 3       | 0        | 0         | 4        |
| 8     | 4       | 0        | 0         | 4        |
| 9     | 4       | 0        | 0         | 5        |
| 10    | 5       | 0        | 0         | 5        |
| 11    | 5       | 0        | 0         | 6        |
| 12    | 6       | 0        | 0         | 6        |
| 13    | 6       | 0        | 1         | 6        |
| 14    | 6       | 0        | 2         | 6        |
| 15    | 6       | 0        | 3         | 6        |
| 16    | 6       | 0        | 4         | 6        |
| 17    | 6       | 0        | 5         | 6        |
| 18    | 6       | 0        | 6         | 6        |
| 19    | 6       | 1        | 6         | 6        |
| 20    | 6       | 2        | 6         | 6        |
| 21    | 6       | 3        | 6         | 6        |
| 22    | 6       | 4        | 6         | 6        |
| 23    | 6       | 5        | 6         | 6        |
| 24    | 6       | 6        | 6         | 6        |

> ⚠️ **Note:** Phase II only appears at 19+ weeks. Plans of 13–18 weeks skip Phase II entirely and go I → III → IV. Is this intended, or should Phase II get some weeks in the 13–18 range?

---

## 3. Target Peak Volume

```
Target peak = midpoint of Daniels range per race distance
Guardrail:  target_peak = min(target_peak, current_weekly_mileage × 2.5)
Guardrail:  target_peak = max(target_peak, current_weekly_mileage)
```

| Race Distance | Target Peak (km) |
|---------------|-----------------|
| 5k            | 70              |
| 10k           | 80              |
| Half Marathon | 90              |
| Marathon      | 120             |
| Ultra         | 140             |

**Example — athlete at 40 km/wk targeting marathon:**
```
target_peak = min(120, 40 × 2.5) = min(120, 100) = 100 km
```

---

## 4. Volume Progression Curve

### Build phase (weeks 1 → weeks_to_peak)

```
week 1:        volume = current_weekly_mileage
week i (i%4 == 0):  volume = previous × 0.85    ← cutback week
week i (otherwise): volume = min(previous × 1.10, target_peak)
```

`weeks_to_peak = phase_I + phase_II + phase_III`  
For plans < 12 weeks with no Phase II/III: `weeks_to_peak = phase_I`

### Phase IV taper

```
phase_iv_week = current_week − weeks_to_peak   (1-based within Phase IV)

taper_factor  = max(0.35,  1.0 − 0.10 × phase_iv_week)

IF phase_iv_week >= (phase_IV_length − 1):     ← final 2 weeks
    taper_factor = 0.35

volume = taper_factor × target_peak
```

### Sample — 18-week marathon, start 40 km/wk (target_peak = 100 km)

| Week | Volume (km) | Note                     |
|------|-------------|--------------------------|
| 1    | 40.0        | Start                    |
| 2    | 44.0        | +10%                     |
| 3    | 48.4        | +10%                     |
| 4    | 41.1        | Cutback ×0.85            |
| 5    | 45.2        | +10%                     |
| 6    | 49.7        | +10%                     |
| 7    | 54.7        | +10%                     |
| 8    | 46.5        | Cutback ×0.85            |
| 9    | 51.2        | +10%                     |
| 10   | 56.3        | +10%                     |
| 11   | 61.9        | +10% — actual peak hit   |
| 12   | 52.6        | Cutback ×0.85            |
| 13   | **90.0**    | Phase IV starts — 90% of 100 |
| 14   | 80.0        | Taper ×0.80              |
| 15   | 70.0        | Taper ×0.70              |
| 16   | 60.0        | Taper ×0.60              |
| 17   | 35.0        | Final 2 weeks ×0.35      |
| 18   | 35.0        | Race week                |

> ⚠️ **BUG — Phase IV volume jump:** Athlete peaks at **61.9 km** during the build but Phase IV opens at **90.0 km** — a **45% single-week jump**. This is because Phase IV anchors to `target_peak` (100 km), not to the athlete's actual achieved peak. An athlete starting at 40 km/wk cannot reach 100 km in 12 weeks under the +10%/cutback rules. The fix would be: `phase_iv_start = max(actual_peak_reached, target_peak × 0.90)` — i.e. anchor Phase IV to the *highest volume the athlete actually ran*, not the theoretical target peak.

---

## 5. VDOT → Training Paces

### Formulas (Daniels linear approximation, valid VDOT 30–80)

```
Easy (E)        = 8.500 − 0.080 × VDOT   (min/km)
Marathon (M)    = 7.500 − 0.075 × VDOT
Threshold (T)   = 6.700 − 0.070 × VDOT
Interval (I)    = 5.800 − 0.060 × VDOT
Repetition (R)  = 4.800 − 0.050 × VDOT
```

VDOT input is clamped to [30, 80] before calculation.

### Full Pace Table

| VDOT | Easy     | Marathon | Threshold | Interval | Repetition |
|------|----------|----------|-----------|----------|------------|
| 30   | 6:06 /km | 5:15 /km | 4:36 /km  | 4:00 /km | 3:18 /km   |
| 35   | 5:42 /km | 4:53 /km | 4:15 /km  | 3:42 /km | 3:03 /km   |
| 40   | 5:18 /km | 4:30 /km | 3:54 /km  | 3:24 /km | 2:48 /km   |
| 45   | 4:54 /km | 4:07 /km | 3:33 /km  | 3:06 /km | 2:33 /km   |
| 50   | 4:30 /km | 3:45 /km | 3:12 /km  | 2:48 /km | 2:18 /km   |
| 55   | 4:06 /km | 3:23 /km | 2:51 /km  | 2:30 /km | 2:03 /km   |
| 60   | 3:42 /km | 3:00 /km | 2:30 /km  | 2:12 /km | 1:48 /km   |
| 65   | 3:18 /km | 2:37 /km | 2:09 /km  | 1:54 /km | 1:33 /km   |
| 70   | 2:54 /km | 2:15 /km | 1:48 /km  | 1:36 /km | 1:18 /km   |
| 75   | 2:30 /km | 1:53 /km | 1:27 /km  | 1:18 /km | 1:03 /km   |
| 80   | 2:06 /km | 1:30 /km | 1:06 /km  | 1:00 /km | 0:48 /km   |

> ⚠️ **Accuracy note:** These are linear approximations. The Daniels tables use a non-linear curve. At VDOT 70+ the approximation produces paces that are likely too fast. At VDOT 30 the Easy pace (6:06/km) is plausible but you should cross-reference against the actual Daniels tables for your target VDOT range. E.g. Daniels' actual VDOT 50 Easy pace is ~4:37/km vs this model's 4:30/km — a meaningful difference at higher volumes.

---

## 6. Weekly Session Distances

### Standard day allocations (% of weekly volume)

| Day | Session          | % of Weekly Volume    |
|-----|------------------|-----------------------|
| Mon | Rest             | 0%                    |
| Tue | Quality          | ~10–12% (incl WU/CD)  |
| Wed | Recovery         | 9%                    |
| Thu | Medium-Long      | 18%                   |
| Fri | Recovery         | 9%                    |
| Sat | Long Run         | 25–30% (race-dependent)|
| Sun | Cross-Train/Rest | 0% (20% for ultra back-to-back)|

```
recovery_km      = weekly_volume × 0.09
medium_long_km   = weekly_volume × 0.18
```

### Long run %

```
long_run_pct = 0.25 + (0.025 × distance_factor)
long_run_pct = min(long_run_pct, 0.35)

distance_factor: 5k=0, 10k=0, half=1, marathon=2, ultra=3(overridden to 0.30)
```

| Volume | 5k        | 10k       | Half      | Marathon  | Ultra     |
|--------|-----------|-----------|-----------|-----------|-----------|
| 40 km  | 10.0 (25%)| 10.0 (25%)| 11.0 (28%)| 12.0 (30%)| 12.0 (30%)|
| 60 km  | 15.0 (25%)| 15.0 (25%)| 16.5 (28%)| 18.0 (30%)| 18.0 (30%)|
| 80 km  | 20.0 (25%)| 20.0 (25%)| 22.0 (28%)| 24.0 (30%)| 24.0 (30%)|
| 100 km | 25.0 (25%)| 25.0 (25%)| 27.5 (28%)| 30.0 (30%)| 30.0 (30%)|
| 120 km | 30.0 (25%)| 30.0 (25%)| 33.0 (28%)| 36.0 (30%)| 36.0 (30%)|

> ⚠️ **Marathon long run cap:** Marathon long run peaks at 30% (no cap applied), producing a 36 km long run at 120 km/wk. Daniels typically caps marathon long runs at 29–32 km regardless of weekly volume. You may want to add `long_run_km = min(long_run_km, 32)` for marathon.

---

## 7. Quality Session Prescription

### Volume budget

```
quality_km = weekly_volume × 0.20
```

### Rep counts by phase

| Volume | Phase I: 100m strides | Phase II: 200m R | Phase III: 800m I | Phase IV: 1600m T |
|--------|----------------------|------------------|-------------------|-------------------|
| 40 km  | 10                   | 12               | 6                 | 4                 |
| 60 km  | 10 (capped)          | 12 (capped)      | 8 (capped)        | 5 (capped)        |
| 80 km  | 10 (capped)          | 12 (capped)      | 8 (capped)        | 5 (capped)        |
| 100 km | 10 (capped)          | 12 (capped)      | 8 (capped)        | 5 (capped)        |
| 120 km | 10 (capped)          | 12 (capped)      | 8 (capped)        | 5 (capped)        |

### Rep count formulas

```
Phase I  (strides):        reps = clamp(floor(quality_km / 0.5),        6, 10)
Phase II (200m R-pace):    reps = clamp(floor(quality_km / (0.2 × 1.5)), 6, 12)
Phase III (800m I-pace):   reps = clamp(floor(quality_km / (0.8 × 1.5)), 4, 8)
Phase IV (1600m T-pace):   reps = clamp(floor(quality_km / (1.6 × 1.2)), 3, 5)

The × 1.5 or × 1.2 accounts for recovery jog distance between reps.
```

> ⚠️ **Rep caps hit early:** All phases hit their upper cap at 60 km/wk or below. Athletes above 60 km/wk never get more reps — only the pace changes. If the 80/20 rule targets 20% quality, an 80 km/wk athlete has 16 km of quality budget but only uses ~8.6 km (5 × 1600m + WU/CD). Consider either raising the caps or adding a second quality session for higher-volume athletes.

### Warmup / cooldown

| Phase | Warmup | Cooldown | Total session = WU + quality + CD |
|-------|--------|----------|------------------------------------|
| I     | 2.0 km | 2.0 km   | reps×0.1 + 4.0 km                 |
| II    | 2.0 km | 1.0 km   | reps×0.2 + 3.0 km                 |
| III   | 2.0 km | 1.0 km   | reps×0.8 + 3.0 km                 |
| IV    | 2.0 km | 1.0 km   | reps×1.6 + 3.0 km                 |

---

## 8. Steady-State & Fast-Finish Long Run Rules

```
IF hilliness == "high" AND phase in (3, 4):
    → Hilly terrain long run (overrides all pace-based notes)

ELSE IF race_distance in ("marathon", "ultra") AND phase == 3:
    steady_km = min(16, long_run_km × 0.35)
    → First (long_run_km − steady_km) easy, last steady_km at M pace

ELSE IF race_distance in ("half", "marathon") AND phase in (3, 4):
    finish_km = 5 (half) or 8 (marathon)
    → Last finish_km at goal pace
```

---

## 9. Race Week Sessions

```
Tue: weekly_volume × 0.10 km — easy + 4 strides
Wed: weekly_volume × 0.07 km — very easy
Thu: weekly_volume × 0.08 km — easy + 2 strides
Fri: Rest
Sat: Race
Sun: Rest
```

---

## 10. Hill Work Replacement Matrix

### Replacement decision

```
IF hilliness == "low":                  → never replace
IF phase == 1 OR phase == 4:            → never replace
IF hilliness == "high" AND phase in (2, 3):   → always replace
IF hilliness == "medium" AND phase in (2, 3): → replace if week_in_phase is ODD
    (alternates: odd = hill, even = flat)
```

### Full matrix

| Phase | Low      | Medium                  | High          |
|-------|----------|-------------------------|---------------|
| I     | Strides  | Strides                 | Strides       |
| II    | R 200s   | Alternates (hills/R)    | Short Hill Sprints |
| III   | I 800s   | Alternates (hills/I)    | Long Hill Repeats  |
| IV    | T Cruise | T Cruise                | T Cruise      |

### Hill session prescriptions

**Short Hill Sprints (Phase II replacement)**
```
grade:  6–10% (steep)
effort: all-out (10–15 seconds)
reps:   clamp(floor(weekly_volume / 10),  8, 12)
recovery: walk/jog back down
quality_km: reps × 0.05 km
total_km:   quality_km + 3.5 km
```

**Long Hill Repeats (Phase III replacement)**
```
grade:  4–6% (moderate)
effort: I-pace equivalent (~3 min each)
reps:   clamp(floor(weekly_volume / 15),  4, 8)
recovery: jog down (equal to rep time)
quality_km: reps × (3 / 5.5) km
total_km:   quality_km + 3.0 km
```

**Downhill Repeats (Phase III/IV addition, high hilliness only)**
```
grade:    2–4% (gentle)
distance: 600m per rep
effort:   T-pace
reps (Phase III): clamp(floor(weekly_volume / 12), 6, 10)
reps (Phase IV):  4 (reduced for taper)
trigger:  every even week within the phase (wip % 2 == 0)
total_km: reps × 0.6 + 2.0 km
```

---

## 11. Closed-Loop Adaptation (Weekly)

### Volume modifier

```
compliance = actual_volume / planned_volume

IF compliance < 0.80:   vol_modifier = 0.85  (−15%)
IF compliance < 0.90:   vol_modifier = 0.95  (−5%)
IF compliance > 1.05:   vol_modifier = 1.02  (+2%)
ELSE:                   vol_modifier = 1.00  (no change)

adjusted_next_volume = planned_next_volume × vol_modifier
```

### RPE overrides

```
IF avg_rpe >= 9.0:  vol_modifier = min(vol_modifier, 0.85)  ← hard ceiling
IF avg_rpe >= 8.5:  vol_modifier = min(vol_modifier, 0.90)
IF avg_rpe <= 5.0 AND compliance >= 0.95:  vdot += 0.5 (max 80)
```

### Interaction — worst case stacking

| Compliance | avg RPE | Vol modifier |
|------------|---------|-------------|
| < 80%      | ≥ 9.0   | 0.85 (same — they agree) |
| 90–105%    | ≥ 9.0   | 0.85 (RPE overrides neutral) |
| > 105%     | ≥ 9.0   | 0.85 (RPE overrides the +2%) |

---

## 12. VDOT from Race Performance

### Daniels VO₂max formula

```
v         = (race_distance_km × 1000) / finish_time_minutes   [m/min]

VO₂       = −4.60 + 0.182258 × v + 0.000104 × v²

%VO₂max   = 0.8
          + 0.1894393 × exp(−0.012778 × t)
          + 0.2989558 × exp(−0.1932605 × t)
          where t = finish_time_minutes

VDOT      = VO₂ / %VO₂max
VDOT      = clamp(VDOT, 25, 85)
```

### Verified outputs vs known benchmarks

| Race | Finish Time | Calculated VDOT | Reference VDOT* |
|------|-------------|-----------------|-----------------|
| 5k   | 15:00       | 69.6            | ~70             |
| 5k   | 20:00       | 49.8            | ~50             |
| 10k  | 40:00       | 51.9            | ~52             |
| 10k  | 45:00       | 45.3            | ~45             |
| HM   | 1:30:00     | 51.0            | ~52             |
| HM   | 1:45:00     | 42.6            | ~43             |
| Mar  | 3:00:00     | 53.5            | ~54             |
| Mar  | 3:30:00     | 44.6            | ~45             |
| Mar  | 4:00:00     | 37.9            | ~38             |

*Reference values from Daniels Running Formula tables. Formula output is within ±1 VDOT across the tested range. ✅

---

## Summary of Items Flagged for Review

| # | Location              | Issue                                                                                    | Severity |
|---|-----------------------|------------------------------------------------------------------------------------------|----------|
| 1 | Phase allocation      | Phase II skipped entirely for plans 13–18 weeks (I → III → IV)                         | Low — may be intentional |
| 2 | Volume curve          | Phase IV opens at 90% of `target_peak`, not 90% of `actual_peak_reached`. For low-starting athletes this creates a large volume jump (45% in the 40km/wk marathon example) | **High** |
| 3 | Pace formulas         | Linear approximation diverges from actual Daniels tables at high VDOT (70+). Easy pace may be ~7 sec/km too fast at VDOT 70 | Medium |
| 4 | Marathon long run     | No absolute cap — at 120 km/wk the long run is 36 km. Daniels caps marathon long runs at ~29–32 km | Medium |
| 5 | Rep count caps        | All phases hit upper rep cap by 60 km/wk. Higher-volume athletes get no additional quality volume | Low — conservative |
