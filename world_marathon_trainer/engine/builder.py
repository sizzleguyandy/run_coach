"""Plan builder — orchestrates assessment + all phases into one plan.

Assembles a single continuous week list from the athlete's entry point through
to race day, renumbering weeks consecutively.
"""

from __future__ import annotations

from .models import AthleteInput, Plan
from .assessment import assess
from .race_profile import load_race
from . import phase1a, phase1b, phase2to5


def _first_rung_for(athlete: AthleteInput) -> int:
    """Where a beginner starts on the 0->10K ladder, based on current ability."""
    mins = athlete.longest_continuous_run_min
    if mins >= 30:
        return 9    # already runs 30 min -> start at the 5K rung
    if mins >= 20:
        return 6    # continuous-time stage
    if mins >= 8:
        return 3    # run-dominant stage
    return 0        # true beginner


def build_plan(athlete: AthleteInput) -> Plan:
    a = assess(athlete)
    race = load_race(athlete.race_id)
    weeks = []
    idx = 1

    # ---- Phase 1.a ---------------------------------------------------- #
    if a.entry_phase == "1a":
        rung = _first_rung_for(athlete)
        wk = phase1a.build_phase1a(idx, from_rung=rung)
        weeks.extend(wk)
        idx += len(wk)

    # ---- Phase 1.b ---------------------------------------------------- #
    # Base build consumes whatever runway remains before the 18-week block.
    if a.entry_phase in ("1a", "1b"):
        # Starting volume for the base build.
        start_km = athlete.current_weekly_km
        if a.entry_phase == "1a":
            start_km = max(start_km, 25.0)   # ~post-graduation volume
        b1 = phase1b.build_phase1b(
            start_index=idx,
            start_km=start_km,
            target_peak=a.target_peak_km,
            runway_weeks=a.runway_weeks,
            training_days=athlete.training_days_per_week,
            vdot=a.vdot,
        )
        weeks.extend(b1)
        idx += len(b1)

    # ---- Phase 2-5 (the race block) ----------------------------------- #
    block = phase2to5.build_phase2to5(
        start_index=idx,
        peak_km=a.target_peak_km,
        goal_vdot=a.vdot,
        training_days=athlete.training_days_per_week,
        race=race,
    )
    weeks.extend(block)

    return Plan(
        athlete=athlete.name,
        race_id=athlete.race_id,
        race_date=athlete.race_date,
        assessment=a,
        weeks=weeks,
    )
