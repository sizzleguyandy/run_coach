"""
Strength module router — v1.6

Endpoints:
  GET  /strength/templates             — list templates (filter by phase, difficulty)
  GET  /strength/templates/{id}        — single template
  POST /strength/log                   — log a completed session + apply intensity block
  GET  /strength/logs                  — athlete's recent strength logs (last 30 days)
  PATCH /athlete/{telegram_id}/strength — update strength settings on athlete
"""

import json
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import and_

from coach_core.database import get_db
from coach_core.models import Athlete, StrengthTemplate, StrengthLog, RunLog, VDOTHistory
from coach_core.engine.strength_adaptation import (
    compute_strength_block_hours,
    is_running_blocked,
    block_expires_in_hours,
    check_pace_gap,
    pace_gap_cooldown_active,
    pace_gap_bot_message,
    PACE_GAP_WINDOW_DAYS,
    PACE_GAP_VDOT_DROP,
    PACE_GAP_COOLDOWN_DAYS,
)

router = APIRouter(tags=["strength"])

_log = logging.getLogger(__name__)


# ── Pydantic schemas ───────────────────────────────────────────────────────

class StrengthLogCreate(BaseModel):
    telegram_id: str
    log_date: Optional[date] = None          # defaults to today
    template_id: Optional[int] = None
    session_name: str
    exercises_done: Optional[list] = None    # list of dicts (per-set weight/reps/rpe)
    total_volume_load: Optional[float] = None
    session_rpe: Optional[int] = None
    duration_min: Optional[int] = None
    notes: Optional[str] = None


class StrengthSettingsUpdate(BaseModel):
    strength_frequency: Optional[int] = None   # 0–4
    strength_level: Optional[str] = None       # beginner|intermediate|advanced
    strength_days: Optional[str] = None        # "Tue,Thu"


# ── Helper ─────────────────────────────────────────────────────────────────

async def _get_athlete(telegram_id: str, db: AsyncSession) -> Athlete:
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")
    return athlete


# ── GET /strength/templates ────────────────────────────────────────────────

@router.get("/strength/templates")
async def list_templates(
    phase: Optional[str] = Query(None, description="Filter by phase_name (Base, Repetitions, Intervals, Taper, C25K)"),
    difficulty: Optional[str] = Query(None, description="Filter by difficulty (beginner, intermediate, advanced)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns all strength templates, optionally filtered by phase and/or difficulty.
    Structure JSON is included so the mini-app can render exercises directly.
    """
    stmt = select(StrengthTemplate)
    if phase:
        stmt = stmt.where(StrengthTemplate.phase_name == phase)
    if difficulty:
        stmt = stmt.where(StrengthTemplate.difficulty == difficulty)
    stmt = stmt.order_by(StrengthTemplate.phase_name, StrengthTemplate.difficulty, StrengthTemplate.id)

    result = await db.execute(stmt)
    templates = result.scalars().all()

    return [
        {
            "id":            t.id,
            "phase_name":    t.phase_name,
            "template_name": t.template_name,
            "difficulty":    t.difficulty,
            "structure":     json.loads(t.structure) if t.structure else {},
        }
        for t in templates
    ]


# ── GET /strength/templates/{template_id} ─────────────────────────────────

@router.get("/strength/templates/{template_id}")
async def get_template(template_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(StrengthTemplate).where(StrengthTemplate.id == template_id))
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found.")
    return {
        "id":            t.id,
        "phase_name":    t.phase_name,
        "template_name": t.template_name,
        "difficulty":    t.difficulty,
        "structure":     json.loads(t.structure) if t.structure else {},
    }


# ── POST /strength/log ─────────────────────────────────────────────────────

@router.post("/strength/log", status_code=201)
async def log_strength_session(data: StrengthLogCreate, db: AsyncSession = Depends(get_db)):
    """
    Log a completed strength session and apply the running intensity block.

    Block rules (from strength_adaptation engine):
      - First 30 days of plan → 36h always
      - Volume unchanged       → 24h
      - Volume increased       → 48h

    Updates athlete.strength_last_volume and athlete.strength_load_expires_at.
    """
    athlete = await _get_athlete(data.telegram_id, db)

    session_date = data.log_date or date.today()
    volume = data.total_volume_load or 0.0

    # Compute intensity block duration
    block_hours = compute_strength_block_hours(
        new_volume=volume,
        last_volume=athlete.strength_last_volume,
        plan_start_date=athlete.start_date,
        log_date=session_date,
    )

    # Write strength log
    strength_log = StrengthLog(
        athlete_id=athlete.id,
        log_date=session_date,
        template_id=data.template_id,
        session_name=data.session_name,
        exercises_done=json.dumps(data.exercises_done) if data.exercises_done else None,
        total_volume_load=volume,
        session_rpe=data.session_rpe,
        duration_min=data.duration_min,
        notes=data.notes,
    )
    db.add(strength_log)

    # Update athlete strength fields
    athlete.strength_last_volume = volume
    athlete.strength_load_expires_at = datetime.utcnow() + timedelta(hours=block_hours)

    await db.commit()
    await db.refresh(strength_log)

    return {
        "status":          "logged",
        "log_id":          strength_log.id,
        "block_hours":     block_hours,
        "block_expires_at": athlete.strength_load_expires_at.isoformat(),
        "message": (
            f"Session logged. Next run kept easy for {block_hours}h "
            f"({'first month recovery' if block_hours == 36 else 'volume increased' if block_hours == 48 else 'recovery'})."
        ),
    }


# ── GET /strength/logs ─────────────────────────────────────────────────────

@router.get("/strength/logs")
async def get_strength_logs(
    telegram_id: str = Query(..., description="Athlete telegram ID"),
    days: int = Query(30, description="How many days of history to return (default 30)"),
    db: AsyncSession = Depends(get_db),
):
    """
    Returns the athlete's strength logs for the last N days (default 30).
    """
    athlete = await _get_athlete(telegram_id, db)

    since = date.today() - timedelta(days=days)
    result = await db.execute(
        select(StrengthLog)
        .where(StrengthLog.athlete_id == athlete.id)
        .where(StrengthLog.log_date >= since)
        .order_by(desc(StrengthLog.log_date))
    )
    logs = result.scalars().all()

    # Also include running block status
    blocked = is_running_blocked(athlete.strength_load_expires_at)
    expires_in = block_expires_in_hours(athlete.strength_load_expires_at)

    return {
        "telegram_id":      telegram_id,
        "running_blocked":  blocked,
        "block_expires_in_hours": expires_in,
        "logs": [
            {
                "id":                strength_log.id,
                "log_date":          strength_log.log_date.isoformat(),
                "session_name":      strength_log.session_name,
                "template_id":       strength_log.template_id,
                "total_volume_load": strength_log.total_volume_load,
                "session_rpe":       strength_log.session_rpe,
                "duration_min":      strength_log.duration_min,
                "exercises_done":    json.loads(strength_log.exercises_done) if strength_log.exercises_done else None,
                "notes":             strength_log.notes,
            }
            for strength_log in logs
        ],
    }


# ── PATCH /athlete/{telegram_id}/strength ─────────────────────────────────

@router.patch("/athlete/{telegram_id}/strength")
async def update_strength_settings(
    telegram_id: str,
    data: StrengthSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update the athlete's strength preferences:
      strength_frequency — sessions per week (0–4)
      strength_level     — beginner | intermediate | advanced
      strength_days      — comma-separated day names e.g. "Tue,Thu"
    """
    athlete = await _get_athlete(telegram_id, db)

    if data.strength_frequency is not None:
        if not (0 <= data.strength_frequency <= 4):
            raise HTTPException(status_code=400, detail="strength_frequency must be 0–4.")
        athlete.strength_frequency = data.strength_frequency

    if data.strength_level is not None:
        if data.strength_level not in ("beginner", "intermediate", "advanced"):
            raise HTTPException(status_code=400, detail="strength_level must be beginner, intermediate, or advanced.")
        athlete.strength_level = data.strength_level

    if data.strength_days is not None:
        athlete.strength_days = data.strength_days

    await db.commit()

    return {
        "status":             "updated",
        "strength_frequency": athlete.strength_frequency,
        "strength_level":     athlete.strength_level,
        "strength_days":      athlete.strength_days,
    }


# ── POST /strength/pace-gap-check ─────────────────────────────────────────

@router.post("/strength/pace-gap-check")
async def run_pace_gap_check(
    telegram_id: str = Query(..., description="Athlete telegram ID"),
    db: AsyncSession = Depends(get_db),
):
    """
    Daily pace-gap VDOT check for a single athlete.
    Called by the APScheduler at 06:00 SA for all full-plan athletes.

    Logic:
      - Fetch run_logs from last 14 days with prescribed_pace_min_per_km set
      - If ≥70% of sessions slower than prescribed by >5%  → apply −0.5 VDOT
      - Cooldown: 14 days after any adjustment
      - Returns triggered=True if VDOT was adjusted (scheduler sends bot message)
    """
    athlete = await _get_athlete(telegram_id, db)

    # Only full-plan athletes have VDOT-based paces
    if athlete.plan_type != "full" or not athlete.vdot:
        return {"triggered": False, "reason": "not_applicable"}

    today = date.today()

    # Check cooldown
    if pace_gap_cooldown_active(athlete.vdot_pace_check_cooldown_until, today):
        return {
            "triggered": False,
            "reason": "cooldown_active",
            "cooldown_until": athlete.vdot_pace_check_cooldown_until.isoformat(),
        }

    # Fetch run logs from last 14 days
    since = today - timedelta(days=PACE_GAP_WINDOW_DAYS)
    logs_result = await db.execute(
        select(RunLog).where(
            and_(
                RunLog.athlete_id == athlete.id,
                RunLog.logged_at >= datetime.combine(since, datetime.min.time()),
                RunLog.actual_distance_km > 0,
                RunLog.duration_minutes > 0,
            )
        )
    )
    logs = logs_result.scalars().all()

    runs = [
        {
            "actual_distance_km":           l.actual_distance_km,
            "duration_minutes":             l.duration_minutes,
            "prescribed_pace_min_per_km":   l.prescribed_pace_min_per_km,
        }
        for l in logs
    ]

    result = check_pace_gap(runs)

    if not result["trigger"]:
        return {
            "triggered":        False,
            "sessions_checked": result["sessions_checked"],
            "below_pace_pct":   result["below_pace_pct"],
            "reason":           "threshold_not_met" if result["eligible"] else "insufficient_data",
        }

    # Apply −0.5 VDOT
    old_vdot = athlete.vdot
    new_vdot = round(max(25.0, old_vdot - PACE_GAP_VDOT_DROP), 1)
    athlete.vdot = new_vdot
    athlete.vdot_pace_check_cooldown_until = today + timedelta(days=PACE_GAP_COOLDOWN_DAYS)

    db.add(VDOTHistory(
        athlete_id=athlete.id,
        vdot=new_vdot,
        source="pace_adjusted",
        effective_date=today,
    ))
    await db.commit()

    _log.info(
        f"Pace-gap VDOT drop: {telegram_id} {old_vdot} → {new_vdot} "
        f"({result['below_pace_count']}/{result['sessions_checked']} sessions below pace)"
    )

    return {
        "triggered":        True,
        "old_vdot":         old_vdot,
        "new_vdot":         new_vdot,
        "sessions_checked": result["sessions_checked"],
        "below_pace_count": result["below_pace_count"],
        "below_pace_pct":   result["below_pace_pct"],
        "message":          pace_gap_bot_message(old_vdot, new_vdot),
    }
