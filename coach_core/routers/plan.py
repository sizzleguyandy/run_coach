from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from datetime import date
import json
import math

from coach_core.database import get_db
from coach_core.models import Athlete
from coach_core.engine.plan_builder import build_full_plan, get_current_week
from coach_core.engine.c25k import build_c25k_week, TOTAL_WEEKS as C25K_TOTAL_WEEKS
from coach_core.engine.anchor_constants import (
    ANCHOR_FIXED_SESSIONS  as _FIXED_SESSIONS,
    ANCHOR_REST_SESSIONS   as _REST_SESSIONS,
    ANCHOR_BLOCKED_SESSIONS,
)

router = APIRouter(prefix="/plan", tags=["plan"])


def _apply_anchor_overlay(week: dict, anchor_runs: list[dict]) -> dict:
    """
    Overlay club/group anchor runs onto the generated week plan.

    Rules:
      - Anchor days → "Group Run" at the user-specified km (locked)
      - Fixed-quality days (Long Run, intervals, etc.) → untouched
      - All other run days → scaled proportionally so total weekly km is preserved
    """
    if not anchor_runs:
        return week

    # Deep-copy days so we never mutate the cached plan
    days = {k: dict(v) for k, v in week.get("days", {}).items()}
    anchor_map = {a["day"]: float(a["km"]) for a in anchor_runs if a["day"] in days}

    if not anchor_map:
        return week

    original_total = sum(d.get("km", 0) for d in days.values())

    # Apply anchor overrides
    for day, km in anchor_map.items():
        days[day] = {
            **days[day],
            "session": "Group Run",
            "km":      km,
            "anchor":  True,
            "notes":   "Club / group run — anchored",
        }

    # Identify adjustable run days: non-anchor, non-fixed, non-rest
    adjustable = [
        d for d in days
        if d not in anchor_map
        and days[d].get("km", 0) > 0
        and days[d].get("session") not in _FIXED_SESSIONS
        and days[d].get("session") not in _REST_SESSIONS
    ]
    adjustable_original_vol = sum(days[d]["km"] for d in adjustable)

    if adjustable and adjustable_original_vol > 0:
        fixed_vol = sum(
            days[d].get("km", 0) for d in days
            if d not in anchor_map and d not in adjustable
        )
        anchor_vol    = sum(anchor_map.values())
        adjustable_target = max(original_total - anchor_vol - fixed_vol, 0)
        scale = adjustable_target / adjustable_original_vol

        for d in adjustable:
            raw = days[d]["km"] * scale
            # Round to nearest 0.5 km, floor at 2 km
            days[d] = {**days[d], "km": max(round(raw * 2) / 2, 2.0)}

    new_total = round(sum(d.get("km", 0) for d in days.values()), 1)

    result = dict(week)
    result["days"]               = days
    result["planned_volume_km"]  = new_total
    return result


async def _get_athlete_or_404(telegram_id: str, db: AsyncSession) -> Athlete:
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")
    return athlete


def _current_c25k_week(athlete: Athlete) -> int:
    """Return the current C25K week based on stored c25k_week (set by adaptation)."""
    return max(1, min(athlete.c25k_week or 1, C25K_TOTAL_WEEKS))


@router.get("/{telegram_id}/current")
async def get_current_week_plan(telegram_id: str, db: AsyncSession = Depends(get_db)):
    """Return this week's plan. Routes to C25K or full plan based on plan_type."""
    athlete = await _get_athlete_or_404(telegram_id, db)

    if athlete.plan_type == "c25k":
        week_num = _current_c25k_week(athlete)
        # Fetch weather factor for C25K heat guidance (best-effort — never blocks plan delivery)
        weather_factor = 1.0
        if athlete.latitude and athlete.longitude:
            try:
                from coach_core.engine.truepace import fetch_weather, compute_adjustment
                run_hour = athlete.run_hour or 7
                weather = await fetch_weather(athlete.latitude, athlete.longitude, run_hour)
                if weather:
                    weather_factor = compute_adjustment(
                        weather["temperature"], weather["dew_point"]
                    ).factor
            except Exception:
                pass   # silently degrade — never break plan delivery
        return build_c25k_week(week_num, weather_factor=weather_factor)

    # Full plan
    plan = build_full_plan(
        current_weekly_mileage=athlete.current_weekly_mileage,
        vo2x=athlete.vo2x,
        race_distance=athlete.race_distance,
        race_date=athlete.race_date,
        start_date=athlete.start_date,
        race_hilliness=athlete.race_hilliness,
        long_run_day=athlete.long_run_day or "Sat",
        quality_day=athlete.quality_day or "Tue",
        training_profile=athlete.training_profile or "conservative",
        extra_training_days=athlete.extra_training_days or "Thu",
    )
    week = get_current_week(plan, date.today(), athlete.start_date)
    if not week:
        raise HTTPException(status_code=404, detail="No active training week.")

    total_weeks = plan.get("total_weeks")

    # Always shallow-copy so we can safely annotate without mutating the plan cache
    week = dict(week)
    week["total_weeks"] = total_weeks  # consumed by coach_chat and dashboard

    # Inject a coaching note for short plans that have no speed or quality phases
    if total_weeks < 14 and not week.get("plan_note"):
        week["plan_note"] = (
            f"Your {total_weeks}-week plan focuses entirely on aerobic base and a structured "
            "taper — no speed or quality phases are introduced at this window. "
            "This is by design: consistent easy running now will carry you to the start line "
            "healthy and well-paced. Plan your next race with more lead time to unlock "
            "threshold and interval training."
        )

    # Apply anchor run overlay (club / group runs that are fixed each week)
    if athlete.anchor_runs:
        try:
            anchors = json.loads(athlete.anchor_runs)
            week = _apply_anchor_overlay(week, anchors)
        except Exception:
            pass  # never break plan delivery over anchor parsing errors

    return week


@router.get("/{telegram_id}/week/{week_number}")
async def get_week_plan(telegram_id: str, week_number: int, db: AsyncSession = Depends(get_db)):
    """Return a specific week's plan by week number."""
    athlete = await _get_athlete_or_404(telegram_id, db)

    if athlete.plan_type == "c25k":
        if week_number < 1 or week_number > C25K_TOTAL_WEEKS:
            raise HTTPException(status_code=404, detail=f"C25K week {week_number} not found (1–{C25K_TOTAL_WEEKS}).")
        return build_c25k_week(week_number)

    plan = build_full_plan(
        current_weekly_mileage=athlete.current_weekly_mileage,
        vo2x=athlete.vo2x,
        race_distance=athlete.race_distance,
        race_date=athlete.race_date,
        start_date=athlete.start_date,
        race_hilliness=athlete.race_hilliness,
        long_run_day=athlete.long_run_day or "Sat",
        quality_day=athlete.quality_day or "Tue",
        training_profile=athlete.training_profile or "conservative",
        extra_training_days=athlete.extra_training_days or "Thu",
    )
    for week in plan["weeks"]:
        if week["week_number"] == week_number:
            # Apply anchor overlay so historical week views are consistent
            # with the /current endpoint
            if athlete.anchor_runs:
                try:
                    anchors = json.loads(athlete.anchor_runs)
                    week = _apply_anchor_overlay(week, anchors)
                except Exception:
                    pass
            return week
    raise HTTPException(status_code=404, detail=f"Week {week_number} not found.")


@router.get("/{telegram_id}")
async def get_full_plan(telegram_id: str, db: AsyncSession = Depends(get_db)):
    """Return the complete plan. For C25K, returns all 12 weeks."""
    athlete = await _get_athlete_or_404(telegram_id, db)

    if athlete.plan_type == "c25k":
        return {
            "plan_type": "c25k",
            "total_weeks": C25K_TOTAL_WEEKS,
            "current_week": _current_c25k_week(athlete),
            "weeks": [build_c25k_week(w) for w in range(1, C25K_TOTAL_WEEKS + 1)],
        }

    return build_full_plan(
        current_weekly_mileage=athlete.current_weekly_mileage,
        vo2x=athlete.vo2x,
        race_distance=athlete.race_distance,
        race_date=athlete.race_date,
        start_date=athlete.start_date,
        race_hilliness=athlete.race_hilliness,
    )
