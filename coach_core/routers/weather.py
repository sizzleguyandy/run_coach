from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional

from coach_core.database import get_db
from coach_core.models import Athlete
from coach_core.engine.truepace import get_truepace_block, compute_adjustment, fetch_weather
from coach_core.engine.paces import calculate_paces, format_pace

router = APIRouter(prefix="/weather", tags=["weather"])


async def _get_athlete_or_404(telegram_id: str, db: AsyncSession) -> Athlete:
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")
    return athlete


@router.get("/{telegram_id}/adjustment")
async def get_pace_adjustment(
    telegram_id: str,
    session_type: str = Query(default="easy", description="easy | quality | long"),
    run_hour: Optional[int] = Query(default=None, description="Override preferred run hour (0–23)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Fetch weather for the athlete's location and return TRUEPACE-adjusted paces.

    If no location is stored, returns unadjusted paces and a prompt to add location.
    C25K athletes get a simplified response (no pace zones).
    """
    athlete = await _get_athlete_or_404(telegram_id, db)

    # C25K athletes have no VO2X yet
    if athlete.plan_type == "c25k" or not athlete.vo2x:
        if not athlete.latitude or not athlete.longitude:
            return {"available": False, "reason": "Add your location with /location to enable TRUEPACE."}

        hour = run_hour or athlete.run_hour or 7
        weather = await fetch_weather(athlete.latitude, athlete.longitude, hour)
        if not weather:
            return {"available": False, "reason": "Weather data unavailable."}

        adj = compute_adjustment(weather["temperature"], weather["dew_point"])
        return {
            "available": True,
            "weather": weather,
            "factor": adj.factor,
            "adjustment_pct": adj.adjustment_pct,
            "high_heat_warning": adj.high_heat_warning,
            "note": "Keep effort conversational regardless of conditions.",
        }

    # Full plan athletes
    if not athlete.latitude or not athlete.longitude:
        return {
            "available": False,
            "reason": "No location stored. Use /location to enable TRUEPACE weather adjustments.",
        }

    paces = calculate_paces(athlete.vo2x)
    paces_dict = {
        "vo2x":        athlete.vo2x,
        "easy":        format_pace(paces.easy_min_per_km),
        "marathon":    format_pace(paces.marathon_min_per_km),
        "threshold":   format_pace(paces.threshold_min_per_km),
        "interval":    format_pace(paces.interval_min_per_km),
        "repetition":  format_pace(paces.repetition_min_per_km),
    }

    hour = run_hour or athlete.run_hour or 7
    block = await get_truepace_block(
        lat=athlete.latitude,
        lon=athlete.longitude,
        paces_dict=paces_dict,
        run_hour=hour,
        session_type=session_type,
    )
    return block


@router.get("/{telegram_id}/conditions")
async def get_current_conditions(
    telegram_id: str,
    run_hour: Optional[int] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Return raw weather conditions only (no pace calculation)."""
    athlete = await _get_athlete_or_404(telegram_id, db)
    if not athlete.latitude or not athlete.longitude:
        raise HTTPException(status_code=400, detail="No location stored.")

    hour = run_hour or athlete.run_hour or 7
    weather = await fetch_weather(athlete.latitude, athlete.longitude, hour)
    if not weather:
        raise HTTPException(status_code=503, detail="Weather service unavailable.")

    adj = compute_adjustment(weather["temperature"], weather["dew_point"])
    return {
        **weather,
        "adjustment_factor": adj.factor,
        "adjustment_pct": adj.adjustment_pct,
        "high_heat_warning": adj.high_heat_warning,
    }
