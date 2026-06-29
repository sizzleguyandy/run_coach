# World Marathon Trainer — Engine

Race-first marathon training engine. Each program is shaped by the **race profile**,
so two athletes targeting different races get genuinely different plans even at the
same fitness. Built on Jack Daniels' methodology (VDOT, Final-18 2Q structure).

Pure logic — no I/O, no DB, no network. FastAPI / n8n / agent layers sit on top later.

## Layout

```
world_marathon_trainer/
├── races/                  # one JSON per race (data, not code)
│   ├── _TEMPLATE.json      # blank race profile
│   ├── _RESEARCH_GUIDE.md  # how/where to research each field
│   └── cape_town.json      # first race (test bed)
├── engine/
│   ├── models.py           # dataclasses: AthleteInput, Session, Week, Plan, Assessment
│   ├── vdot.py             # VDOT <-> pace engine (E/M/T/I/R)
│   ├── race_profile.py     # loads race JSON, exposes training drivers
│   ├── assessment.py       # slot-in entry + peak selection (min of runway & days, gate 48km)
│   ├── phase1a.py          # 0->10K plan (graduate on steady 10K)
│   ├── phase1b.py          # base build to target peak
│   ├── phase2to5.py        # 18-week race block, Daniels-informed + race overlay
│   └── builder.py          # orchestrates all phases into one continuous plan
├── api/
│   ├── main.py             # FastAPI app — thin HTTP wrapper over the engine
│   └── schemas.py          # Pydantic request/response models
├── demo.py                 # builds + prints a full Cape Town plan
├── requirements.txt        # API + test deps (engine itself is stdlib-only)
└── tests/
    ├── test_engine.py      # 20 engine tests
    └── test_api.py         # 11 API tests
```

## API

```bash
cd world_marathon_trainer
pip install -r requirements.txt
uvicorn api.main:app --reload --port 8000      # docs at /docs
```

| Method | Path | Purpose |
|---|---|---|
| GET  | `/health` | liveness + races loaded |
| GET  | `/races` | list available races (add a JSON → appears here) |
| GET  | `/races/{id}` | full race profile (drives training + agent RAG) |
| GET  | `/vdot/{value}` | paces + race-time equivalents + pace card |
| POST | `/vdot/from-race` | VDOT from a race result |
| GET  | `/vdot-table` | full VDOT 30-85 lookup table |
| POST | `/plan` | build a complete plan (stateless — pass all inputs) |
| POST | `/plan/assessment` | slot-in assessment only (cheap onboarding call) |
| POST | `/athlete` | onboard athlete → returns id (n8n keys sync against it) |
| GET  | `/athletes` · `/athlete/{id}` | list / fetch |
| PATCH · DELETE | `/athlete/{id}` | update inputs / remove (cascades activities) |
| POST | `/athlete/{id}/plan` | build plan from the **stored** record |
| POST | `/athlete/{id}/sync` | **Strava ingest** — n8n posts activities here |
| GET  | `/athlete/{id}/activities` | stored activity history |
| POST | `/athlete/{id}/adapt` | evaluate last completed week → nudge VDOT + notes |
| GET  | `/athlete/{id}/adaptations` | audit trail of past adaptations |

The API contains **no training logic** — it validates, calls the engine, and
serialises. n8n and the agent call these endpoints; they never import the engine
directly (keeps the layers physically separate).

## Persistence & Strava sync

SQLite (via SQLAlchemy 2.0), file `wmt.db` (gitignored, auto-created). Configure
with `WMT_DB_URL` (swap to Postgres later — connection-string change).

- **`athletes`** — plan inputs + identity; cached VDOT. Built so plans rebuild
  without re-onboarding.
- **`activities`** — Strava 'truth', one row per activity, **deduped by Strava id**
  (re-syncing overlapping windows is safe). The history the adaptation loop reads.

Each athlete's n8n workflow POSTs to `/athlete/{id}/sync`. The contract is in
`docs/STRAVA_SYNC_CONTRACT.md` — one workflow or one-per-athlete, the contract is
identical, so the n8n topology can change without touching the backend.

## Adaptation loop

Plans are live-computed, so adaptation nudges **parameters**, not a stored plan.
After the weekly sync, n8n calls `POST /athlete/{id}/adapt`:

1. Build the current plan, find the most recently **completed** week (by date).
2. Pull that week's Strava activities + the last 4 weeks (fitness signal).
3. Compare planned vs actual (volume, sessions, long run, strain).
4. Decide a **bounded VDOT nudge**:
   - **Up** only on evidence — a hard recent effort implies more fitness
     (Daniels' "best recent performance"), moved toward by half the gap, ≤ +1.0/wk.
   - **Down** only when struggling (volume < 65% or sessions < 60%), −0.3, floor 30.
   - **Hold** when on track; **never raise during taper**.
5. Apply to `adapted_vdot` (wins over the race estimate) + write an
   `adaptation_log` row (auditable). Return metrics + coaching `notes`/`flags`
   for the agent to narrate.

Volume is partly self-correcting already — `current_weekly_km` is recomputed from
Strava on every sync, so the next build ramps from the real base.

## Phase model

```
Phase 1.a  Couch -> 10K        graduate on a steady 10K
Phase 1.b  Base build          grow to target peak, gate >= 48 km/week
Phase 2    wks 18-13           threshold base       (VDOT goal-2)
Phase 3    wks 12-7            marathon-specific     (VDOT goal-1, course work begins)
Phase 4    wks  6-3            peak + race rehearsal (VDOT goal, full course overlay)
Phase 5    wks  2-1            taper
```

Anchor: **Phase 2 always starts at race_date − 18 weeks.** Runway before that sets
the base-build length and, with training-days/week, the achievable peak:

```
target_peak = min(runway_ceiling, days_ceiling)   # floored at 48 km gate
```

## Run

```bash
cd world_marathon_trainer
python demo.py                       # build + print a Cape Town plan
python -m pytest tests -q            # tests (needs: pip install pytest)
```

The engine itself uses only the Python standard library.

## Adding a race

Drop a new `races/<id>.json` (copy `_TEMPLATE.json`, fill per `_RESEARCH_GUIDE.md`).
No code changes. The `training_overlay` block is what modulates Phase 3 & 4 sessions.

## Known v1 limitations (next refinements)

- **Long-runway hold:** if runway >> build time, Phase 1.b holds at peak for many
  weeks. Should cap consolidation / build later instead of holding peak.
- Easy-day volume is split evenly (crude); Daniels varies it.
- Quality sessions are phase archetypes scaled by volume, not exact Daniels reps.
- No adaptation loop yet (Strava-actuals -> re-plan).
