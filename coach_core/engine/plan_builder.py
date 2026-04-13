import math
from datetime import date, timedelta
from typing import Optional
from coach_core.engine.phases import get_phases, get_phase_for_week, PhaseAllocation
from coach_core.engine.volume import build_volume_curve
from coach_core.engine.paces import calculate_paces, format_pace, Paces
from coach_core.engine.workouts import build_week_days


def build_full_plan(
    current_weekly_mileage: float,
    vdot: float,
    race_distance: str,
    race_date: date,
    start_date: date,
    race_hilliness: str = "low",
    long_run_day: str = "Sat",
    quality_day: str = "Tue",
    training_profile: str = "conservative",
    extra_training_days: str = "Thu",
) -> dict:
    """
    Build the complete training plan for an athlete.

    Returns a dict containing:
    - total_weeks, phases summary, paces summary
    - weeks: list of per-week dicts (week_number, phase, week_start, planned_volume_km, days)
    """
    days_to_race = (race_date - start_date).days
    total_weeks = max(6, min(24, round(days_to_race / 7)))

    phases = get_phases(total_weeks)
    volumes = build_volume_curve(current_weekly_mileage, race_distance, phases, training_profile)
    paces = calculate_paces(vdot)

    weeks = []
    for i, volume in enumerate(volumes, start=1):
        phase = get_phase_for_week(i, phases)
        week_start = start_date + timedelta(weeks=i - 1)
        days = build_week_days(i, phase, volume, paces, race_distance, phases, race_hilliness, long_run_day, quality_day, extra_training_days)
        weeks.append({
            "week_number": i,
            "phase": phase,
            "week_start": week_start.isoformat(),
            "planned_volume_km": volume,
            "days": days,
        })

    return {
        "total_weeks": total_weeks,
        "phases": {
            "phase_I_weeks": phases.phase_I,
            "phase_II_weeks": phases.phase_II,
            "phase_III_weeks": phases.phase_III,
            "phase_IV_weeks": phases.phase_IV,
        },
        "paces": {
            "vdot": vdot,
            "easy": format_pace(paces.easy_min_per_km),
            "marathon": format_pace(paces.marathon_min_per_km),
            "threshold": format_pace(paces.threshold_min_per_km),
            "interval": format_pace(paces.interval_min_per_km),
            "repetition": format_pace(paces.repetition_min_per_km),
        },
        "weeks": weeks,
    }


def get_current_week(plan: dict, today: date, start_date: date) -> Optional[dict]:
    """Return the current week dict based on today's date."""
    days_elapsed = (today - start_date).days
    week_number = min(math.floor(days_elapsed / 7) + 1, plan["total_weeks"])
    for week in plan["weeks"]:
        if week["week_number"] == week_number:
            return week
    return None


def current_week_number(start_date: date) -> int:
    """Return current 1-based week number from start_date."""
    return math.floor((date.today() - start_date).days / 7) + 1
