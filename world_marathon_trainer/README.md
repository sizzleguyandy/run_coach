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
├── demo.py                 # builds + prints a full Cape Town plan
└── tests/test_engine.py    # 13 tests
```

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
