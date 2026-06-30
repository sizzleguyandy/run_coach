"""Seed a demo athlete with synthetic Strava history, so there's someone to talk
to while testing the agent.

Creates an athlete targeting Cape Town from ~8 weeks of generated runs (derived
fitness, not self-reported), stores the activities, and prints the athlete_id.

Run:  python scripts/seed_demo.py
Honours WMT_DB_URL. Idempotent-ish: re-running creates another demo athlete.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from api.db import SessionLocal, init_db  # noqa: E402
from api import store                      # noqa: E402


def _synthetic_activities(weeks: int = 8) -> list[dict]:
    """~weeks of running: 4 runs/week, one long, plus a recent sharp 5K."""
    today = date.today()
    base = today - timedelta(weeks=weeks)
    acts: list[dict] = []
    for w in range(weeks):
        for d, dist in enumerate([19, 11, 11, 11]):   # long + 3 steady
            day = base + timedelta(weeks=w, days=d)
            acts.append({
                "id": f"seed-{w}-{d}",
                "activity_type": "Run",
                "start_date": f"{day.isoformat()}T05:30:00",
                "distance_km": dist,
                "moving_time_min": dist * 5.6,
                "average_heartrate": 150,
            })
    # a sharp parkrun last weekend → gives the engine a real fitness read
    acts.append({
        "id": "seed-parkrun",
        "activity_type": "Run",
        "start_date": f"{(today - timedelta(days=6)).isoformat()}T08:00:00",
        "distance_km": 5.0,
        "moving_time_min": 21.5,
        "suffer_score": 80,
    })
    return acts


def main() -> int:
    init_db()
    db = SessionLocal()
    try:
        # Pass real date objects (the API layer normally does this via Pydantic;
        # here we call the store directly).
        result = store.onboard(db, {
            "name": "Demo Athlete",
            "race_id": "cape_town",
            "race_date": date.today() + timedelta(weeks=30),
            "training_days_committed": 5,
            "today": date.today(),
            "activities": _synthetic_activities(),
        }, preview=False)
    finally:
        db.close()

    aid = result["athlete_id"]
    prof = result["profile"]
    ass = result["assessment"]
    print("[seed_demo] created demo athlete")
    print(f"  athlete_id : {aid}")
    print(f"  derived    : {prof['weekly_km']} km/wk, longest {prof['longest_run_km']} km, "
          f"VDOT {prof['implied_vdot']} ({prof['confidence']} confidence)")
    print(f"  slot-in    : entry phase {ass['entry_phase']}, peak {ass['target_peak_km']} km "
          f"(cap: {ass['peak_binding_constraint']})")
    print(f"\n  Try in the agent:  get_today('{aid}')  /  get_paces('{aid}')")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
