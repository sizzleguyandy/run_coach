from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import date
from typing import Optional

from coach_core.database import get_db
from coach_core.models import Athlete, RunLog, VDOTHistory
from coach_core.engine.adaptation import adapt_next_week, WeekSummary, calculate_vdot_from_race
from coach_core.engine.plan_builder import build_full_plan, current_week_number

router = APIRouter(prefix="/log", tags=["log"])


class RunLogCreate(BaseModel):
    telegram_id: str
    week_number: int
    day_name: str
    planned_distance_km: Optional[float] = None
    actual_distance_km: float
    duration_minutes: Optional[float] = None
    rpe: Optional[int] = None
    notes: Optional[str] = None
    # v1.6: pace tracking (treadmill auto-log + pace-gap VDOT check)
    prescribed_pace_min_per_km: Optional[float] = None
    source: Optional[str] = "manual"   # "manual" | "treadmill"


class RaceResult(BaseModel):
    telegram_id: str
    race_distance_km: float
    finish_time_minutes: float
    race_date: date
    force: bool = False   # set True to accept a VDOT drop > 3 points


@router.post("/run", status_code=201)
async def log_run(data: RunLogCreate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == data.telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    log = RunLog(
        athlete_id=athlete.id,
        week_number=data.week_number,
        day_name=data.day_name,
        planned_distance_km=data.planned_distance_km,
        actual_distance_km=data.actual_distance_km,
        duration_minutes=data.duration_minutes,
        rpe=data.rpe,
        notes=data.notes,
        prescribed_pace_min_per_km=data.prescribed_pace_min_per_km,
        source=data.source or "manual",
    )
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return {"status": "logged", "log_id": log.id}


@router.get("/{telegram_id}/week/{week_number}/summary")
async def get_week_summary(telegram_id: str, week_number: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    logs_result = await db.execute(
        select(RunLog).where(
            RunLog.athlete_id == athlete.id,
            RunLog.week_number == week_number,
        )
    )
    logs = logs_result.scalars().all()

    actual_volume = sum(l.actual_distance_km for l in logs)
    rpe_values = [l.rpe for l in logs if l.rpe is not None]
    avg_rpe = round(sum(rpe_values) / len(rpe_values), 1) if rpe_values else None

    return {
        "week_number": week_number,
        "actual_volume_km": round(actual_volume, 1),
        "sessions_logged": len(logs),
        "avg_rpe": avg_rpe,
        "runs": [
            {
                "day": l.day_name,
                "actual_km": l.actual_distance_km,
                "planned_km": l.planned_distance_km,
                "rpe": l.rpe,
                "duration_min": l.duration_minutes,
            }
            for l in sorted(logs, key=lambda x: x.day_name)
        ],
    }


@router.post("/{telegram_id}/adapt")
async def run_weekly_adaptation(telegram_id: str, week_number: int, db: AsyncSession = Depends(get_db)):
    """
    Trigger closed-loop adaptation after completing a week.
    Returns adjusted volume + VDOT for next week, and saves updated VDOT if changed.
    """
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    # Get week summary
    logs_result = await db.execute(
        select(RunLog).where(
            RunLog.athlete_id == athlete.id,
            RunLog.week_number == week_number,
        )
    )
    logs = logs_result.scalars().all()
    actual_volume = sum(l.actual_distance_km for l in logs)
    rpe_values = [l.rpe for l in logs if l.rpe is not None]
    avg_rpe = sum(rpe_values) / len(rpe_values) if rpe_values else None

    # Get planned volume for week
    plan = build_full_plan(
        current_weekly_mileage=athlete.current_weekly_mileage,
        vdot=athlete.vdot,
        race_distance=athlete.race_distance,
        race_date=athlete.race_date,
        start_date=athlete.start_date,
        training_profile=athlete.training_profile or "conservative",
        extra_training_days=athlete.extra_training_days or "Thu",
    )
    planned_week = next((w for w in plan["weeks"] if w["week_number"] == week_number), None)
    planned_volume = planned_week["planned_volume_km"] if planned_week else actual_volume
    next_planned = None
    for w in plan["weeks"]:
        if w["week_number"] == week_number + 1:
            next_planned = w["planned_volume_km"]
            break

    rpe_coverage = len(rpe_values) / max(len(logs), 1)

    summary = WeekSummary(
        planned_volume=planned_volume,
        actual_volume=actual_volume,
        avg_rpe=avg_rpe,
        sessions_completed=len(logs),
    )

    adj_volume, new_vdot, notes = adapt_next_week(
        planned_next_volume=next_planned or planned_volume,
        summary=summary,
        current_vdot=athlete.vdot,
        training_profile=athlete.training_profile or "conservative",
    )

    # Persist VDOT change if updated
    if new_vdot != athlete.vdot:
        athlete.vdot = new_vdot
        db.add(VDOTHistory(
            athlete_id=athlete.id,
            vdot=new_vdot,
            source="adjusted",
            effective_date=date.today(),
        ))
        await db.commit()

    # ── Streak and badge tracking ─────────────────────────────────────────
    STREAK_THRESHOLD   = 0.80   # 80% compliance = completed week
    BADGE_WEEKS        = 4      # 4 consecutive completed weeks = 1 badge
    compliance_ratio   = actual_volume / max(planned_volume, 0.1)
    badge_earned       = False
    streak_before      = athlete.streak_weeks or 0

    if compliance_ratio >= STREAK_THRESHOLD:
        athlete.streak_weeks = streak_before + 1
        if athlete.streak_weeks >= BADGE_WEEKS:
            athlete.total_badges = (athlete.total_badges or 0) + 1
            athlete.streak_weeks = 0
            badge_earned = True
    else:
        athlete.streak_weeks = 0   # reset on missed week

    await db.commit()

    # ── VDOT level-up Mini App notification ───────────────────────────
    _vdot_before = round(athlete.vdot - (new_vdot - athlete.vdot), 1) if new_vdot != athlete.vdot else athlete.vdot
    if new_vdot != athlete.vdot and int(new_vdot) > int(_vdot_before):
        try:
            from telegram_bot.handlers.reminder import send_levelup_notification
            from telegram_bot.config import TELEGRAM_TOKEN
            from telegram import Bot
            import asyncio as _aio
            _aio.create_task(send_levelup_notification(
                Bot(token=TELEGRAM_TOKEN), telegram_id,
                athlete.name, _vdot_before, new_vdot, "adjusted",
            ))
        except Exception:
            pass

    # Append RPE nudge if more than half the week's runs have no RPE logged
    all_notes = list(notes) if isinstance(notes, list) else ([notes] if notes else [])
    if rpe_coverage < 0.5 and len(logs) > 0:
        all_notes.append(
            "Tip: logging RPE (effort level) for each run helps the adaptation engine "
            "tune your training load more accurately. Try rating your next run 1–10."
        )

    return {
        "week_number": week_number,
        "planned_volume": planned_volume,
        "actual_volume": round(actual_volume, 1),
        "compliance_pct": round(compliance_ratio * 100, 1),
        "adjusted_next_week_volume": adj_volume,
        "vdot_before": athlete.vdot if new_vdot == athlete.vdot else round(athlete.vdot - (new_vdot - athlete.vdot), 1),
        "vdot_after": new_vdot,
        "coaching_notes": all_notes,
        "streak_weeks": athlete.streak_weeks,
        "total_badges": athlete.total_badges or 0,
        "badge_earned": badge_earned,
    }


# How many VDOT points a race result may drop without needing force=True
VDOT_DROP_THRESHOLD = 3.0

@router.post("/race")
async def log_race_result(data: RaceResult, db: AsyncSession = Depends(get_db)):
    """
    Log a race result and update VDOT with a guard against unexpectedly large drops.

    Guard rules:
      new >= old           → accept immediately (improvement)
      old - new <= 3 pts   → accept with a caution note (rough race, still valid)
      old - new >  3 pts   → reject unless force=True is set
                             Returns vdot_updated=False + coaching message

    force=True bypasses the guard and always writes the new VDOT.
    Intended for confirmed poor performances (illness, wrong distance, etc.).
    All results are stored in VDOTHistory regardless of whether they update the live VDOT.
    """
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == data.telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    new_vdot = calculate_vdot_from_race(data.race_distance_km, data.finish_time_minutes)
    old_vdot = athlete.vdot or new_vdot   # no prior VDOT (C25K athlete) — always accept
    drop = old_vdot - new_vdot
    vdot_updated = False
    message = ""
    coaching_note = ""

    # Check whether the current VDOT was boosted by the adaptation engine
    # (i.e. the most recent prior entry is an "adjusted" source above the initial baseline)
    nudge_context = ""
    try:
        from sqlalchemy import desc as _desc
        hist_result = await db.execute(
            select(VDOTHistory)
            .where(VDOTHistory.athlete_id == athlete.id)
            .order_by(_desc(VDOTHistory.effective_date))
            .limit(5)
        )
        history = hist_result.scalars().all()
        # If the most recent VDOT change was an adaptation nudge, note that
        adjusted_entries = [h for h in history if h.source == "adjusted"]
        if adjusted_entries and adjusted_entries[0].vdot >= old_vdot:
            nudge_context = (
                " Your VDOT had been nudged up by the weekly adaptation engine — "
                "this race result recalibrates it back to a race-validated baseline."
            )
    except Exception:
        pass  # never block race logging on a history query failure

    if new_vdot >= old_vdot:
        # Improvement or equal — always accept
        vdot_updated = True
        gain = round(new_vdot - old_vdot, 1)
        message = (
            f"🎉 New PR! VDOT {old_vdot} → {new_vdot} (+{gain}). Paces updated."
            if gain > 0 else
            f"VDOT unchanged at {new_vdot}. Consistent performance."
        )

    elif drop <= VDOT_DROP_THRESHOLD:
        # Small drop — accept, add caution note
        vdot_updated = True
        message = f"VDOT {old_vdot} → {new_vdot} (−{round(drop, 1)}). Paces updated."
        coaching_note = (
            "Small dip — could be heat, fatigue, or a tough course. "
            "Your training paces will be recalculated to match."
            + nudge_context +
            " If this doesn't reflect your fitness, log another race or time trial soon."
        )

    elif data.force:
        # Large drop but athlete confirmed — accept
        vdot_updated = True
        message = f"VDOT {old_vdot} → {new_vdot} (−{round(drop, 1)}) — confirmed by you. Paces updated."
        coaching_note = (
            "Large drop accepted. Training paces have been adjusted accordingly."
            + nudge_context +
            " Focus on rebuilding gradually — your aerobic base is still there."
        )

    else:
        # Large drop, no confirmation — hold and ask
        vdot_updated = False
        message = (
            f"⚠️ This result would drop your VDOT from {old_vdot} → {new_vdot} "
            f"(−{round(drop, 1)} points). That's a significant change."
        )
        coaching_note = (
            "This could reflect a bad day, illness, heat, or wrong distance."
            + nudge_context +
            " If it's a true reflection of your fitness, resubmit with force=True. "
            "Otherwise, ignore this result and continue with your current paces."
        )

    # Always write to VDOTHistory for the record
    db.add(VDOTHistory(
        athlete_id=athlete.id,
        vdot=new_vdot,
        source="race",
        effective_date=data.race_date,
    ))

    if vdot_updated:
        athlete.vdot = new_vdot
        await db.commit()
    else:
        await db.commit()   # still save the history entry

    # ── VDOT level-up notification ──────────────────────────────────────
    if vdot_updated and new_vdot > old_vdot and int(new_vdot) > int(old_vdot):
        try:
            from telegram_bot.handlers.reminder import send_levelup_notification
            from telegram_bot.config import TELEGRAM_TOKEN
            from telegram import Bot
            import asyncio as _aio
            _aio.create_task(send_levelup_notification(
                Bot(token=TELEGRAM_TOKEN), data.telegram_id,
                athlete.name, old_vdot, new_vdot, "race",
            ))
        except Exception:
            pass

    return {
        "old_vdot": old_vdot,
        "new_vdot": new_vdot,
        "vdot_updated": vdot_updated,
        "drop_points": round(drop, 1) if drop > 0 else 0,
        "message": message,
        "coaching_note": coaching_note,
    }


# ── C25K endpoints ─────────────────────────────────────────────────────────

class C25KTimeTrial(BaseModel):
    telegram_id: str
    finish_time_minutes: float   # 5k time trial result
    week_run_km: Optional[float] = None  # avg km per 30-min session in last week


@router.post("/{telegram_id}/c25k/adapt")
async def adapt_c25k(telegram_id: str, week_number: int, db: AsyncSession = Depends(get_db)):
    """
    C25K weekly adaptation.
    Computes total run minutes logged vs planned, then advances/repeats/drops back.
    Updates athlete.c25k_week in the database.
    """
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")
    if athlete.plan_type != "c25k":
        raise HTTPException(status_code=400, detail="Athlete is not on a C25K plan.")

    from coach_core.engine.c25k import adapt_c25k_week, build_c25k_week, _total_run_minutes, get_week_schedule

    sched = get_week_schedule(week_number)
    planned_run_minutes = _total_run_minutes(sched) * 3   # 3 sessions per week

    # Sum actual run minutes from logged sessions this week
    logs_result = await db.execute(
        select(RunLog).where(
            RunLog.athlete_id == athlete.id,
            RunLog.week_number == week_number,
        )
    )
    logs = logs_result.scalars().all()
    actual_run_minutes = sum(l.duration_minutes or 0 for l in logs)

    next_week, notes = adapt_c25k_week(week_number, planned_run_minutes, actual_run_minutes)

    # Persist updated week
    athlete.c25k_week = next_week
    if next_week > week_number and week_number >= 12:
        athlete.c25k_completed = True
    await db.commit()

    return {
        "week_completed": week_number,
        "planned_run_minutes": planned_run_minutes,
        "actual_run_minutes": round(actual_run_minutes, 1),
        "compliance": round(actual_run_minutes / max(planned_run_minutes, 0.1), 2),
        "next_week": next_week,
        "c25k_completed": athlete.c25k_completed,
        "coaching_notes": notes,
    }


@router.post("/c25k/timetrial")
async def log_c25k_timetrial(data: C25KTimeTrial, db: AsyncSession = Depends(get_db)):
    """
    Log a 5k time trial result at end of C25K.
    Computes VDOT, updates athlete record, and returns transition data.
    """
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == data.telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    from coach_core.engine.c25k import compute_transition
    transition = compute_transition(
        time_trial_5k_minutes=data.finish_time_minutes,
        week11_avg_km=data.week_run_km,
    )

    athlete.vdot = transition["vdot"]
    athlete.current_weekly_mileage = transition["estimated_weekly_km"]
    athlete.c25k_completed = True

    db.add(VDOTHistory(
        athlete_id=athlete.id,
        vdot=transition["vdot"],
        source="c25k_graduation",
        effective_date=date.today(),
    ))
    await db.commit()

    return transition
