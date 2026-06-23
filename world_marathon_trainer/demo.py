"""Demo: build and print a full plan for a test athlete targeting Cape Town.

Run:  python -m world_marathon_trainer.demo
  or: cd world_marathon_trainer && python demo.py
"""

from __future__ import annotations

import sys
from datetime import date

try:
    from engine import AthleteInput, build_plan
    from engine import vdot as vdot_mod
except ImportError:  # when run as a module from repo root
    from world_marathon_trainer.engine import AthleteInput, build_plan
    from world_marathon_trainer.engine import vdot as vdot_mod


def print_plan(plan, max_weeks_detail: int = 6):
    a = plan.assessment
    print("=" * 70)
    print(f"PLAN for {plan.athlete}  ->  {plan.race_id}  ({plan.race_date})")
    print("=" * 70)
    print(f"Entry phase     : {a.entry_phase}")
    print(f"VDOT (est)      : {a.vdot}")
    print(f"Runway weeks    : {a.runway_weeks}")
    print(f"Target peak     : {a.target_peak_km} km  (capped by: {a.peak_binding_constraint})")
    print(f"Daniels category: {a.daniels_category}")
    print(f"Feasible        : {a.feasible}")
    if a.flags:
        print("FLAGS:")
        for f in a.flags:
            print(f"   ! {f}")
    print("Rationale:")
    for r in a.rationale:
        print(f"   - {r}")
    print(f"\nTotal weeks: {plan.total_weeks}")

    # Phase summary
    print("\nPHASE OVERVIEW (volume km by week):")
    for w in plan.weeks:
        wtr = f"wtr{w.weeks_to_race}" if w.weeks_to_race is not None else "base"
        bar = "#" * int(w.target_volume_km / 3)
        print(f"  wk{w.index:>2} [{w.phase:>2}] {wtr:>5} {w.target_volume_km:>6.1f} {bar}")

    # Detail for the last N weeks (the sharp end)
    print(f"\nDETAIL — final {max_weeks_detail} weeks:")
    for w in plan.weeks[-max_weeks_detail:]:
        print(f"\n  Week {w.index} — {w.phase_label}  "
              f"(wtr {w.weeks_to_race}, {w.target_volume_km} km)")
        if w.note:
            print(f"    NOTE: {w.note}")
        for s in w.days:
            if s.kind == "rest":
                continue
            tag = f"  [{','.join(s.tags)}]" if s.tags else ""
            print(f"    {s.day}: {s.description}{tag}")


def main():
    athlete = AthleteInput(
        today=date(2026, 6, 23),
        race_id="cape_town",
        race_date=date(2027, 5, 23),     # ~48 weeks out
        current_weekly_km=35.0,
        training_days_per_week=5,
        can_run_10k_continuous=True,
        recent_race_distance_km=10.0,
        recent_race_time_min=52.0,
        name="Test Athlete",
    )
    plan = build_plan(athlete)

    # Show the goal paces too.
    paces = vdot_mod.paces_from_vdot(plan.assessment.vdot)
    print("GOAL PACES (at est. VDOT):")
    for z in ("E", "M", "T", "I", "R"):
        print(f"   {z}: {vdot_mod.fmt_pace(paces[z])}")
    print()

    print_plan(plan)


if __name__ == "__main__":
    sys.exit(main())
