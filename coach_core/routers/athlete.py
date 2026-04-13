from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import date
from typing import Optional

from coach_core.database import get_db
from coach_core.models import Athlete, VDOTHistory, RunLog
from coach_core.engine.paces import calculate_paces, format_pace

router = APIRouter(prefix="/athlete", tags=["athlete"])


def _monday_of_week(d) -> "date":
    """Return the Monday of the week containing date d.
    Ensures week boundaries always fall on Monday regardless of signup day."""
    from datetime import date as date_type, timedelta
    if hasattr(d, "weekday"):
        return d - timedelta(days=d.weekday())
    return d


# ── Full plan creation ─────────────────────────────────────────────────────

class AthleteCreate(BaseModel):
    telegram_id: str
    name: str
    current_weekly_mileage: float
    vdot: float
    race_distance: str           # "5k" | "10k" | "half" | "marathon" | "ultra"
    race_hilliness: str = "low"  # "low" | "medium" | "high"
    race_date: date
    start_date: date
    race_name: Optional[str] = None
    preset_race_id: Optional[str] = None
    long_run_day: str = "Sat"   # preferred long run day
    quality_day: str = "Tue"    # preferred hard session day
    training_profile: str = "conservative"  # "conservative" or "aggressive"
    extra_training_days: str = "Thu"           # comma-separated e.g. "Wed,Thu"
    plan_type: str = "full"
    latitude:  Optional[float] = None
    longitude: Optional[float] = None
    run_hour:  Optional[int] = 7


# ── C25K creation ──────────────────────────────────────────────────────────

class AthleteCreateC25K(BaseModel):
    telegram_id: str
    name: str
    start_date: date


# ── Graduation: C25K → full plan ──────────────────────────────────────────

class GraduateC25K(BaseModel):
    vdot: float
    current_weekly_mileage: float
    race_distance: str
    race_hilliness: str = "low"
    race_date: date


# ── Shared response ────────────────────────────────────────────────────────

class AthleteResponse(BaseModel):
    id: int
    telegram_id: str
    name: str
    plan_type: str
    current_weekly_mileage: Optional[float]
    vdot: Optional[float]
    race_distance: Optional[str]
    race_date: Optional[date]
    start_date: date
    race_hilliness: str
    race_name: Optional[str] = None
    preset_race_id: Optional[str] = None
    training_profile: Optional[str] = None
    streak_weeks: Optional[int] = None
    total_badges: Optional[int] = None
    anchor_runs: Optional[str] = None   # JSON: [{"day":"Tue","km":10.0}, ...]
    c25k_week: Optional[int]
    c25k_completed: bool
    latitude:  Optional[float]
    longitude: Optional[float]
    run_hour:  Optional[int]

    model_config = {"from_attributes": True}


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.post("/", response_model=AthleteResponse, status_code=201)
async def create_athlete(data: AthleteCreate, db: AsyncSession = Depends(get_db)):
    """Create a full (phase-based) athlete profile."""
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == data.telegram_id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Profile already exists.")

    athlete = Athlete(
        telegram_id=data.telegram_id,
        name=data.name,
        plan_type="full",
        current_weekly_mileage=data.current_weekly_mileage,
        vdot=data.vdot,
        race_distance=data.race_distance,
        race_hilliness=data.race_hilliness,
        race_date=data.race_date,
        start_date=_monday_of_week(data.start_date),
        race_name=data.race_name,
        preset_race_id=data.preset_race_id,
        long_run_day=data.long_run_day,
        quality_day=data.quality_day,
        training_profile=data.training_profile,
        extra_training_days=data.extra_training_days,
        latitude=data.latitude,
        longitude=data.longitude,
    )
    db.add(athlete)
    await db.flush()
    db.add(VDOTHistory(
        athlete_id=athlete.id,
        vdot=data.vdot,
        source="initial",
        effective_date=data.start_date,
    ))
    await db.commit()
    await db.refresh(athlete)
    return athlete


@router.post("/c25k", response_model=AthleteResponse, status_code=201)
async def create_c25k_athlete(data: AthleteCreateC25K, db: AsyncSession = Depends(get_db)):
    """Create a C25K beginner profile. No VDOT or race details required."""
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == data.telegram_id))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Profile already exists.")

    athlete = Athlete(
        telegram_id=data.telegram_id,
        name=data.name,
        plan_type="c25k",
        start_date=data.start_date,
        c25k_week=1,
        c25k_completed=False,
    )
    db.add(athlete)
    await db.commit()
    await db.refresh(athlete)
    return athlete


@router.post("/{telegram_id}/graduate", response_model=AthleteResponse)
async def graduate_c25k(
    telegram_id: str,
    data: GraduateC25K,
    db: AsyncSession = Depends(get_db),
):
    """
    Graduate an athlete from C25K to a full plan.
    Updates VDOT, mileage, race details, and flips plan_type to 'full'.
    """
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")
    if athlete.plan_type != "c25k":
        raise HTTPException(status_code=400, detail="Athlete is not on a C25K plan.")

    athlete.plan_type = "full"
    athlete.c25k_completed = True
    athlete.vdot = data.vdot
    athlete.current_weekly_mileage = data.current_weekly_mileage
    athlete.race_distance = data.race_distance
    athlete.race_hilliness = data.race_hilliness
    athlete.race_date = data.race_date
    athlete.start_date = date.today()  # reset so week 1 of full plan starts today

    db.add(VDOTHistory(
        athlete_id=athlete.id,
        vdot=data.vdot,
        source="c25k_graduation",
        effective_date=date.today(),
    ))
    await db.commit()
    await db.refresh(athlete)
    return athlete


@router.get("/all")
async def get_all_athletes(db: AsyncSession = Depends(get_db)):
    """Return all athletes — used by the daily reminder scheduler."""
    result = await db.execute(select(Athlete))
    athletes = result.scalars().all()
    return [
        {
            "telegram_id":           a.telegram_id,
            "name":                  a.name,
            "plan_type":             a.plan_type,
            "run_hour":              a.run_hour or 7,
            "training_profile":      a.training_profile or "conservative",
            "long_run_day":          a.long_run_day or "Sat",
            "quality_day":           a.quality_day or "Tue",
            "vdot":                  a.vdot,
            "race_date":             a.race_date.isoformat() if a.race_date else None,
            # fields needed for race-eve & report predictions
            "race_name":             a.race_name,
            "race_distance":         a.race_distance,
            "race_hilliness":        a.race_hilliness,
            "preset_race_id":        a.preset_race_id,
            "current_weekly_mileage": a.current_weekly_mileage,
        }
        for a in athletes
    ]


@router.get("/{telegram_id}", response_model=AthleteResponse)
async def get_athlete(telegram_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")
    return athlete


@router.get("/{telegram_id}/paces")
async def get_paces(telegram_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")
    if not athlete.vdot:
        raise HTTPException(status_code=400, detail="No VDOT yet — complete C25K first.")

    paces = calculate_paces(athlete.vdot)
    return {
        "vdot": athlete.vdot,
        "easy": format_pace(paces.easy_min_per_km),
        "marathon": format_pace(paces.marathon_min_per_km),
        "threshold": format_pace(paces.threshold_min_per_km),
        "interval": format_pace(paces.interval_min_per_km),
        "repetition": format_pace(paces.repetition_min_per_km),
    }


@router.delete("/{telegram_id}", status_code=204)
async def delete_athlete(telegram_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    # Explicitly delete related records first.
    # SQLite reuses primary keys when a table empties — without this, a new
    # athlete created immediately after reset would inherit the deleted athlete's
    # run logs and VDOT history (same athlete.id reassigned by SQLite).
    from sqlalchemy import delete as _delete
    await db.execute(_delete(RunLog).where(RunLog.athlete_id == athlete.id))
    await db.execute(_delete(VDOTHistory).where(VDOTHistory.athlete_id == athlete.id))

    await db.delete(athlete)
    await db.commit()


# ── Anchor Runs ───────────────────────────────────────────────────────────

import json as _json

class AnchorRunsUpdate(BaseModel):
    """
    List of anchor runs for the athlete. Each entry:
      {"day": "Tue", "km": 10.0}
    Max 2 entries. Pass [] to clear all anchors.
    """
    anchors: list[dict]


@router.get("/{telegram_id}/anchors")
async def get_anchors(telegram_id: str, db: AsyncSession = Depends(get_db)):
    """Return the athlete's current anchor runs."""
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")
    anchors = _json.loads(athlete.anchor_runs) if athlete.anchor_runs else []
    return {"anchors": anchors}


@router.patch("/{telegram_id}/anchors")
async def update_anchors(
    telegram_id: str,
    data: AnchorRunsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Set or clear anchor runs. Validates max 2, positive km."""
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    anchors = data.anchors
    if len(anchors) > 2:
        raise HTTPException(status_code=400, detail="Maximum 2 anchor runs allowed.")

    valid_days = {"Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"}
    seen_days: set = set()
    for a in anchors:
        day = a.get("day", "")
        km  = a.get("km", 0)
        if day not in valid_days:
            raise HTTPException(status_code=400, detail=f"Invalid day: {day}")
        if not isinstance(km, (int, float)) or km <= 0:
            raise HTTPException(status_code=400, detail=f"km must be > 0 for {day}")
        if day in seen_days:
            raise HTTPException(status_code=400, detail=f"Duplicate day: {day}")
        seen_days.add(day)

    athlete.anchor_runs = _json.dumps(anchors) if anchors else None
    await db.commit()
    return {"status": "updated", "anchors": anchors}


# ── Location / TRUEPACE ───────────────────────────────────────────────────

class LocationUpdate(BaseModel):
    latitude: float
    longitude: float
    run_hour: Optional[int] = 7   # preferred run start hour (0–23)


@router.patch("/{telegram_id}/location", response_model=AthleteResponse)
async def update_location(
    telegram_id: str,
    data: LocationUpdate,
    db: AsyncSession = Depends(get_db),
):
    """Store athlete's GPS location and preferred run hour for TRUEPACE."""
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    athlete.latitude  = data.latitude
    athlete.longitude = data.longitude
    athlete.run_hour  = data.run_hour
    await db.commit()
    await db.refresh(athlete)
    return athlete
