import math
from datetime import date, timedelta
from typing import Optional
from coach_core.engine.phases import (
    get_phases, get_phases_with_base, get_phase_for_week, PhaseAllocation,
    TEMPLATE_BASE_KM, TEMPLATE_BASE_DISTANCES,
)
from coach_core.engine.volume import build_volume_curve, base_phase_for_distance
from coach_core.engine.paces import calculate_paces, format_pace, Paces
from coach_core.engine.workouts import build_week_days


def _resolve_phases(
    total_weeks: int,
    current_weekly_mileage: float,
    race_distance: str,
    training_profile: str,
) -> tuple[PhaseAllocation, Optional[dict]]:
    """
    Choose the phase allocation for the plan.

    For races 21 km and up (TEMPLATE_BASE_DISTANCES), Phase 1 base-building is
    extended so the Phase 2 quality templates only begin once weekly volume
    reaches the 48 km template base. Returns (phases, base_building_info) where
    base_building_info is None for ungated races, or a dict describing how the
    base requirement was handled (including a human-readable warning).
    """
    if race_distance not in TEMPLATE_BASE_DISTANCES:
        return get_phases(total_weeks), None

    phase2_start, reachable, target_peak = base_phase_for_distance(
        current_weekly_mileage, race_distance, training_profile,
    )
    phases, status = get_phases_with_base(total_weeks, phase2_start, reachable)

    base_km   = int(TEMPLATE_BASE_KM)
    start_km  = round(current_weekly_mileage)
    peak_km   = round(target_peak)

    if status is None:
        info = {
            "template_base_km": TEMPLATE_BASE_KM,
            "phase2_start_week": phase2_start,
            "reachable": True,
            "status": "ok",
            "warning": None,
        }
    elif status == "extended":
        info = {
            "template_base_km": TEMPLATE_BASE_KM,
            "phase2_start_week": phase2_start,
            "reachable": True,
            "status": "extended",
            "warning": (
                f"ℹ️ Phase 1 base-building extended to {phases.phase_I} weeks so your "
                f"weekly volume reaches the {base_km} km base required for "
                f"{race_distance} quality templates before Phase 2 begins."
            ),
        }
    elif status == "no_time":
        info = {
            "template_base_km": TEMPLATE_BASE_KM,
            "phase2_start_week": phase2_start,
            "reachable": reachable,
            "status": "no_time",
            "warning": (
                f"⚠️ There isn't enough time before your race to build from {start_km} km/wk "
                f"up to the {base_km} km base needed for {race_distance} quality templates. "
                f"This plan is base-building + taper only — no speed or quality phases are "
                f"included. Choose a later race date or raise your starting mileage to unlock "
                f"the full template plan."
            ),
        }
    else:  # "unreachable"
        if target_peak < TEMPLATE_BASE_KM:
            reason = (
                f"your {training_profile} build caps at about {peak_km} km/wk — below the "
                f"{base_km} km base"
            )
        else:
            reason = (
                f"your {training_profile} build progresses too gradually to reach the "
                f"{base_km} km base in a normal plan from {start_km} km/wk"
            )
        info = {
            "template_base_km": TEMPLATE_BASE_KM,
            "phase2_start_week": None,
            "reachable": False,
            "status": "unreachable",
            "warning": (
                f"⚠️ Starting at {start_km} km/wk, {reason} required for {race_distance} "
                f"quality templates. This plan is base-building + taper only. Raise your starting "
                f"mileage (or switch to the aggressive profile) to unlock the full template plan."
            ),
        }

    return phases, info


def build_full_plan(
    current_weekly_mileage: float,
    vo2x: float,
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
    - base_building: how the 48km template-base requirement was handled (21km+ only)
    - weeks: list of per-week dicts (week_number, phase, week_start, planned_volume_km, days)
    """
    days_to_race = (race_date - start_date).days
    total_weeks = max(6, min(24, round(days_to_race / 7)))

    phases, base_building = _resolve_phases(
        total_weeks, current_weekly_mileage, race_distance, training_profile,
    )
    volumes = build_volume_curve(current_weekly_mileage, race_distance, phases, training_profile)
    paces = calculate_paces(vo2x)

    weeks = []
    # Race day-of-week (Mon..Sun) — used to anchor the race-week template
    race_day_name = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][race_date.weekday()]

    for i, volume in enumerate(volumes, start=1):
        phase = get_phase_for_week(i, phases)
        week_start = start_date + timedelta(weeks=i - 1)
        days = build_week_days(i, phase, volume, paces, race_distance, phases, race_hilliness, long_run_day, quality_day, extra_training_days, race_day_name)
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
        "base_building": base_building,
        "paces": {
            "vo2x": vo2x,
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
