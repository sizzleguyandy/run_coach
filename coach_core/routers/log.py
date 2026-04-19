from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from datetime import date
from typing import Optional

from coach_core.database import get_db
from coach_core.models import Athlete, RunLog, VO2XHistory
from coach_core.engine.adaptation import adapt_next_week, WeekSummary, calculate_vo2x_from_race
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
    # v1.6: pace tracking (treadmill auto-log + pace-gap VO2X check)
    prescribed_pace_min_per_km: Optional[float] = None
    source: Optional[str] = "manual"   # "manual" | "treadmill"


class RaceResult(BaseModel):
    telegram_id: str
    race_distance_km: float
    finish_time_minutes: float
    race_date: date
    force: bool = False   # set True to accept a VO2X drop > 3 points


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


@router.get("/{telegram_id}/month/{year}/{month}/summary")
async def get_month_summary(telegram_id: str, year: int, month: int, db: AsyncSession = Depends(get_db)):
    """
    Return total running volume and session count for a calendar month.
    Sums all RunLog entries whose logged date falls within year/month.
    """
    from datetime import date as date_type
    import calendar as cal_module

    result = await db.execute(select(Athlete).where(Athlete.telegram_id == telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    # Determine the week numbers that overlap this calendar month so we can
    # query efficiently without a date column on RunLog.
    # We resolve by checking the athlete's start_date and mapping week → dates.
    start_date = athlete.start_date  # date object
    if not start_date:
        return {"year": year, "month": month, "actual_volume_km": 0.0, "sessions_logged": 0, "avg_rpe": None, "weeks": []}

    first_day = date_type(year, month, 1)
    last_day  = date_type(year, month, cal_module.monthrange(year, month)[1])

    # Find which plan week numbers cover this month
    def week_num_for_date(d):
        delta = (d - start_date).days
        if delta < 0:
            return None
        return delta // 7 + 1

    first_week = week_num_for_date(first_day)
    last_week  = week_num_for_date(last_day)
    if first_week is None:
        first_week = 1
    if last_week is None:
        last_week = first_week

    week_nums = list(range(first_week, last_week + 1))

    logs_result = await db.execute(
        select(RunLog).where(
            RunLog.athlete_id == athlete.id,
            RunLog.week_number.in_(week_nums),
        )
    )
    logs = logs_result.scalars().all()

    total_km   = round(sum(l.actual_distance_km for l in logs), 1)
    rpe_values = [l.rpe for l in logs if l.rpe is not None]
    avg_rpe    = round(sum(rpe_values) / len(rpe_values), 1) if rpe_values else None

    # Per-week breakdown
    week_breakdown = []
    for wn in week_nums:
        wk_logs = [l for l in logs if l.week_number == wn]
        week_breakdown.append({
            "week_number": wn,
            "actual_volume_km": round(sum(l.actual_distance_km for l in wk_logs), 1),
            "sessions_logged": len(wk_logs),
        })

    return {
        "year": year,
        "month": month,
        "actual_volume_km": total_km,
        "sessions_logged": len(logs),
        "avg_rpe": avg_rpe,
        "weeks": week_breakdown,
    }


@router.post("/{telegram_id}/adapt")
async def run_weekly_adaptation(telegram_id: str, week_number: int, db: AsyncSession = Depends(get_db)):
    """
    Trigger closed-loop adaptation after completing a week.
    Returns adjusted volume + VO2X for next week, and saves updated VO2X if changed.
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
        vo2x=athlete.vo2x,
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

    adj_volume, new_vo2x, notes = adapt_next_week(
        planned_next_volume=next_planned or planned_volume,
        summary=summary,
        current_vo2x=athlete.vo2x,
        training_profile=athlete.training_profile or "conservative",
    )

    # Persist VO2X change if updated
    if new_vo2x != athlete.vo2x:
        athlete.vo2x = new_vo2x
        db.add(VO2XHistory(
            athlete_id=athlete.id,
            vo2x=new_vo2x,
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

    # ── VO2X level-up Mini App notification ───────────────────────────
    _vo2x_before = round(athlete.vo2x - (new_vo2x - athlete.vo2x), 1) if new_vo2x != athlete.vo2x else athlete.vo2x
    if new_vo2x != athlete.vo2x and int(new_vo2x) > int(_vo2x_before):
        try:
            from telegram_bot.handlers.reminder import send_levelup_notification
            from telegram_bot.config import TELEGRAM_TOKEN
            from telegram import Bot
            import asyncio as _aio
            _aio.create_task(send_levelup_notification(
                Bot(token=TELEGRAM_TOKEN), telegram_id,
                athlete.name, _vo2x_before, new_vo2x, "adjusted",
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
        "vo2x_before": athlete.vo2x if new_vo2x == athlete.vo2x else round(athlete.vo2x - (new_vo2x - athlete.vo2x), 1),
        "vo2x_after": new_vo2x,
        "coaching_notes": all_notes,
        "streak_weeks": athlete.streak_weeks,
        "total_badges": athlete.total_badges or 0,
        "badge_earned": badge_earned,
    }


# How many VO2X points a race result may drop without needing force=True
VO2X_DROP_THRESHOLD = 3.0

@router.post("/race")
async def log_race_result(data: RaceResult, db: AsyncSession = Depends(get_db)):
    """
    Log a race result and update VO2X with a guard against unexpectedly large drops.

    Guard rules:
      new >= old           → accept immediately (improvement)
      old - new <= 3 pts   → accept with a caution note (rough race, still valid)
      old - new >  3 pts   → reject unless force=True is set
                             Returns vo2x_updated=False + coaching message

    force=True bypasses the guard and always writes the new VO2X.
    Intended for confirmed poor performances (illness, wrong distance, etc.).
    All results are stored in VO2XHistory regardless of whether they update the live VO2X.
    """
    result = await db.execute(select(Athlete).where(Athlete.telegram_id == data.telegram_id))
    athlete = result.scalar_one_or_none()
    if not athlete:
        raise HTTPException(status_code=404, detail="Athlete not found.")

    new_vo2x = calculate_vo2x_from_race(data.race_distance_km, data.finish_time_minutes)
    old_vo2x = athlete.vo2x or new_vo2x   # no prior VO2X (C25K athlete) — always accept
    drop = old_vo2x - new_vo2x
    vo2x_updated = False
    message = ""
    coaching_note = ""

    # Check whether the current VO2X was boosted by the adaptation engine
    # (i.e. the most recent prior entry is an "adjusted" source above the initial baseline)
    nudge_context = ""
    try:
        from sqlalchemy import desc as _desc
        hist_result = await db.execute(
            select(VO2XHistory)
            .where(VO2XHistory.athlete_id == athlete.id)
            .order_by(_desc(VO2XHistory.effective_date))
            .limit(5)
        )
        history = hist_result.scalars().all()
        # If the most recent VO2X change was an adaptation nudge, note that
        adjusted_entries = [h for h in history if h.source == "adjusted"]
        if adjusted_entries and adjusted_entries[0].vo2x >= old_vo2x:
            nudge_context = (
                " Your VO2X had been nudged up by the weekly adaptation engine — "
                "this race result recalibrates it back to a race-validated baseline."
            )
    except Exception:
        pass  # never block race logging on a history query failure

    if new_vo2x >= old_vo2x:
        # Improvement or equal — always accept
        vo2x_updated = True
        gain = round(new_vo2x - old_vo2x, 1)
        message = (
            f"🎉 New PR! VO2X {old_vo2x} → {new_vo2x} (+{gain}). Paces updated."
            if gain > 0 else
            f"VO2X unchanged at {new_vo2x}. Consistent performance."
        )

    elif drop <= VO2X_DROP_THRESHOLD:
        # Small drop — accept, add caution note
        vo2x_updated = True
        message = f"VO2X {old_vo2x} → {new_vo2x} (−{round(drop, 1)}). Paces updated."
        coaching_note = (
            "Small dip — could be heat, fatigue, or a tough course. "
            "Your training paces will be recalculated to match."
            + nudge_context +
            " If this doesn't reflect your fitness, log another race or time trial soon."
        )

    elif data.force:
        # Large drop but athlete confirmed — accept
        vo2x_updated = True
        message = f"VO2X {old_vo2x} → {new_vo2x} (−{round(drop, 1)}) — confirmed by you. Paces updated."
        coaching_note = (
            "Large drop accepted. Training paces have been adjusted accordingly."
            + nudge_context +
            " Focus on rebuilding gradually — your aerobic base is still there."
        )

    else:
        # Large drop, no confirmation — hold and ask
        vo2x_updated = False
        message = (
            f"⚠️ This result would drop your VO2X from {old_vo2x} → {new_vo2x} "
            f"(−{round(drop, 1)} points). That's a significant change."
        )
        coaching_note = (
            "This could reflect a bad day, illness, heat, or wrong distance."
            + nudge_context +
            " If it's a true reflection of your fitness, resubmit with force=True. "
            "Otherwise, ignore this result and continue with your current paces."
        )

    # Always write to VO2XHistory for the record
    db.add(VO2XHistory(
        athlete_id=athlete.id,
        vo2x=new_vo2x,
        source="race",
        effective_date=data.race_date,
    ))

    if vo2x_updated:
        athlete.vo2x = new_vo2x
        await db.commit()
    else:
        await db.commit()   # still save the history entry

    # ── VO2X level-up notification ──────────────────────────────────────
    if vo2x_updated and new_vo2x > old_vo2x and int(new_vo2x) > int(old_vo2x):
        try:
            from telegram_bot.handlers.reminder import send_levelup_notification
            from telegram_bot.config import TELEGRAM_TOKEN
            from telegram import Bot
            import asyncio as _aio
            _aio.create_task(send_levelup_notification(
                Bot(token=TELEGRAM_TOKEN), data.telegram_id,
                athlete.name, old_vo2x, new_vo2x, "race",
            ))
        except Exception:
            pass

    return {
        "old_vo2x": old_vo2x,
        "new_vo2x": new_vo2x,
        "vo2x_updated": vo2x_updated,
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
    Computes VO2X, updates athlete record, and returns transition data.
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

    athlete.vo2x = transition["vo2x"]
    athlete.current_weekly_mileage = transition["estimated_weekly_km"]
    athlete.c25k_completed = True

    db.add(VO2XHistory(
        athlete_id=athlete.id,
        vo2x=transition["vo2x"],
        source="c25k_graduation",
        effective_date=date.today(),
    ))
    await db.commit()

    return transition
