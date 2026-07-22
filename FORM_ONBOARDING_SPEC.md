# Google Form Onboarding Spec

**Status:** Design — build target is an n8n workflow; no core changes required.
**Goal:** Onboard a client without the app, using a Google Form whose questions
mirror the app's onboarding flow exactly, submitted through the same public
onboarding endpoints the app uses. The core cannot tell a form-onboarded
athlete from an app-onboarded one.

```
Google Form → linked Sheet (automatic) → n8n new-row trigger
           → validate & map answers → TR3D core /v1 endpoints
           → send week-1 plan via WhatsApp gateway
```

---

## 1. Form structure (mirrors the app, one addition)

Google Forms sections + "go to section based on answer" reproduce the app's
dynamic branching. Question numbering below matches the app's flow.

### Section 1 — Shared (everyone)

| # | Question | Type | Maps to |
|---|---|---|---|
| 0 | **WhatsApp number** (e.g. +27821234567) | short text, regex `^\+\d{9,15}$` | `telegram_id` — see §3 Identity |
| 1 | Welcome | form description text | — |
| 2 | Country | choice: South Africa / United Kingdom | selects which race list Q5 shows |
| 3 | What's your name? | short text | `name` |
| 4 | Can you run 5 km without stopping in under 35 minutes? | choice: Yes / Not yet | **branch**: Yes → Section 3, Not yet → Section 2 |

Q0 is the one question the app doesn't ask (the app knows its user). The
WhatsApp number is the athlete's identity key across form, gateway, and
Strava sync.

### Section 2 — Path A: "Not yet" → TR3DMILL25K (C25K)

| # | Question | Type | Maps to |
|---|---|---|---|
| 5 | Your city | dropdown (30 SA cities / UK list) | `latitude`/`longitude` via city lookup |
| 6 | C25K intro (3×/week walk-run, 12 weeks) | description text | — |

→ Submit.

### Section 3 — Path B: "Yes" → Full race plan

| # | Question | Type | Maps to |
|---|---|---|---|
| 5 | Pick your race | dropdown: country's presets + "Custom" | `preset_race_id` (+ auto `race_distance`, `race_hilliness`, `race_date`, `race_name`); Custom → Section 3a |
| 6 | Experience | choice: Recent race / Beginner / I know my VO2X | **branch** to 6a variants |
| 6a-race | Distance of recent race (km) + finish time (h:mm:ss) | short text, regex `^\d{1,2}:\d{2}(:\d{2})?$` | predictor input |
| 6a-beg | Ability level | choice (5 options, §2 table) | `beginner_ability` key |
| 6a-vo2x | Your VO2X (20–90) | number, validated 20–90 | `vo2x` directly |
| 7 | Weekly mileage | choice buckets: <20 / 20–35 / 35–50 / 50–70 / 70+ km | `current_weekly_mileage` (bucket midpoint: 15/28/42/60/75) |
| 8 | Longest run in last 6 weeks | choice buckets: <5 / 5–10 / 10–15 / 15–21 / 21+ km | predictor `longest_run_km` (midpoint: 4/8/12/18/25) — **not stored on athlete**, see §4 |
| 9 | Training approach | choice: Balanced / Conservative / Aggressive | `training_profile` — Balanced & Conservative → `conservative`, Aggressive → `aggressive` |
| 10 | Long run day | dropdown Mon–Sun | `long_run_day` |
| 11 | Quality session day | dropdown Mon–Sun (form can't enforce ≠Q10 — n8n validates) | `quality_day` |
| 12 | First easy day | dropdown Mon–Sun | `extra_training_days` (joined) |
| 13 | Second easy day | dropdown Mon–Sun / None | `extra_training_days` (joined) |
| 14 | Group runs (optional): day + distance km, up to 2 | short text e.g. "Tue 10km" | anchors PATCH |
| 15 | Your city | dropdown | `latitude`/`longitude` |

### Section 3a — Custom race (only if Q5 = Custom)

| # | Question | Maps to |
|---|---|---|
| 5a | Distance (km) | `race_distance` via bucket table §2 |
| 5b | Terrain: flat / rolling / hilly / very hilly | `race_hilliness`: flat→`low`, rolling→`medium`, hilly→`high`, very hilly→`high` |
| 5c | Race date + optional race name | `race_date`, `race_name`; `preset_race_id` = null |

---

## 2. Mapping tables n8n owns

**Custom distance → `race_distance`** (mirror of `predictor.km_to_race_distance`):
≤5.5 → `5k` · ≤11 → `10k` · ≤22 → `half` · ≤43 → `marathon` · ≤60 → `ultra_56` · else `ultra_90`

**Beginner ability → `beginner_ability` key** (must match `predictor.BEGINNER_5K_TIMES`):

| Form label | key |
|---|---|
| I can't run yet / starting from zero | `couch` |
| I walk more than I run, short jogs OK | `occasional` |
| I can run 5 km, slowly | `run5k_slow` |
| I run 5 km+ regularly | `run5k_reg` |
| I run 10 km+ regularly | `run10k_reg` |

(Note: `couch`/`occasional` answers on Path B suggest the athlete belongs in
C25K — n8n should flag these rather than silently build a race plan.)

**City → coordinates**: use the `sa_cities.py` name/alias table (n8n copy or
future `GET /v1/cities`). UK deployment needs the UK equivalent.

**Race presets**: do NOT hardcode. Fetch `GET /v1/predict/races` at runtime for
the preset catalog (ids, distances, hill factors, next dates) so form and
server never drift.

---

## 3. n8n call sequence

### Identity (both paths)
`telegram_id` = WhatsApp number normalized to E.164 (`+27821234567`). It is a
plain unique string in the core — nothing requires it to be from Telegram —
and it is exactly how the WhatsApp gateway will address this athlete later.
Normalize hard (strip spaces/dashes, `0…` → `+27…`/`+44…` by country answer),
or the same human re-submitting creates a duplicate.

### Path A — C25K
1. `POST /v1/athlete/c25k` — `{telegram_id, name, start_date: <submission date>}`
2. `PATCH /v1/athlete/{id}/location` — from Q5 city
3. Confirm to client via gateway: `GET /v1/plan/{id}/current`

### Path B — Full plan
1. **VO2X** — one branch per Q6 answer:
   - *I know my VO2X* → use the number, skip predict.
   - *Recent race* or *Beginner* → `POST /v1/predict/` with
     `{race_name, race_distance_km, hill_factor, race_date}` (from preset
     catalog or custom answers), `has_recent_race` +
     `recent_race_distance_km`/`recent_race_time_minutes` (h:mm:ss → minutes)
     OR `beginner_ability`, plus `weekly_mileage_km` and `longest_run_km`
     (bucket midpoints). Take `vo2x` from the response. This is the exact
     converter the app uses — including the sanity warnings, which n8n should
     relay to the client.
2. `POST /v1/athlete/` — everything in one call (`latitude`/`longitude`/
   `run_hour` are accepted at create; no separate location PATCH needed):
   `{telegram_id, name, current_weekly_mileage, vo2x, race_distance,
   race_hilliness, race_date, start_date: <submission date>, race_name,
   preset_race_id, long_run_day, quality_day, extra_training_days,
   training_profile, latitude, longitude}`
3. **Anchors** (only if Q14 answered): `PATCH /v1/athlete/{id}/anchors` —
   `{"anchors": [{"day": "Tue", "km": 10.0}]}` (max 2, easy days only —
   endpoint validates).
4. Confirm to client via gateway: `GET /v1/plan/{id}/current` + prediction
   summary from step 1.

---

## 4. Validation & edge cases (n8n responsibilities)

- **Duplicate (HTTP 409)** — athlete exists: don't fail silently. Notify the
  client ("you're already onboarded") via the gateway, or route to a human.
- **Q11 = Q10** (quality day = long run day) — the core will build a plan but
  the form shouldn't allow it; n8n rejects with a fix-it message.
- **Q13 handling** — "None" → `extra_training_days` from Q12 only; day names
  must be `Mon..Sun` three-letter form.
- **Longest run (Q8)** is consumed by `POST /v1/predict/` only — the athlete
  record has no such column. Don't try to store it; keep it in the Sheet row
  as the audit record.
- **start_date** — send submission date; the core normalizes full-plan starts
  to that week's Monday (`_monday_of_week`).
- **Race date sanity** — must be in the future; plans clamp to 6–24 weeks, so
  a race <6 weeks out deserves a warning to the client (predict step already
  returns `warnings` — relay them).
- **Country vs deployment** — the core stores any `preset_race_id` string and
  race profiles/knowledge cover both countries, but a single deployment's
  preset *catalog* (`GET /v1/predict/races`, keyboard lists) follows its
  `RACE_COUNTRY` env. Keep the form's preset list per country in sync with
  the deployment(s) it feeds.
- **Every row is untrusted** — Forms validation is weak. Re-validate types,
  ranges (mileage 5–150, VO2X 20–90), and enum values in n8n before any API
  call; failed rows get a "we'll contact you" reply, never a garbage athlete.

## 5. Invariants

- Form-onboarded athletes are indistinguishable from app-onboarded ones in
  the core — same endpoints, same fields, same plan output.
- The form/n8n layer never computes coaching math. VO2X conversion, plan
  length, paces: all core calls.
- Contract stability: this spec relies only on endpoints verified unchanged
  by the OpenAPI contract diff (see session audit) — `POST /v1/athlete/`,
  `POST /v1/athlete/c25k`, `POST /v1/predict/`, `GET /v1/predict/races`,
  `PATCH .../location`, `PATCH .../anchors`, `GET /v1/plan/...`.
