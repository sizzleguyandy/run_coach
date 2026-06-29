"""Persistence helpers — athlete CRUD + Strava activity ingest.

Keeps DB access out of the route handlers. No training logic lives here either;
the only engine call is VDOT computation when caching an athlete's fitness index.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from engine import vdot as vdot_mod
from engine import build_plan
from engine.adaptation import evaluate_week
from engine.models import AthleteInput

from .orm import Athlete, Activity, AdaptationLog


# --------------------------------------------------------------------------- #
# Athletes
# --------------------------------------------------------------------------- #
_ATHLETE_FIELDS = (
    "name", "strava_athlete_id", "race_id", "race_date",
    "current_weekly_km", "training_days_per_week", "can_run_10k_continuous",
    "longest_continuous_run_min", "recent_race_distance_km",
    "recent_race_time_min", "goal_marathon_time_min",
)


def _recache_vdot(a: Athlete) -> None:
    a.vdot = round(vdot_mod.estimate_vdot(_to_input(a, today=None)), 1)


def create_athlete(db: Session, data: dict) -> Athlete:
    athlete = Athlete(id=data.get("id") or uuid.uuid4().hex)
    for f in _ATHLETE_FIELDS:
        if f in data and data[f] is not None:
            setattr(athlete, f, data[f])
    _recache_vdot(athlete)
    db.add(athlete)
    db.commit()
    db.refresh(athlete)
    return athlete


def get_athlete(db: Session, athlete_id: str) -> Athlete | None:
    return db.get(Athlete, athlete_id)


def list_athletes(db: Session) -> list[Athlete]:
    return list(db.scalars(select(Athlete).order_by(Athlete.created_at)))


def update_athlete(db: Session, athlete_id: str, data: dict) -> Athlete | None:
    athlete = db.get(Athlete, athlete_id)
    if not athlete:
        return None
    for f in _ATHLETE_FIELDS:
        if f in data and data[f] is not None:
            setattr(athlete, f, data[f])
    _recache_vdot(athlete)
    db.commit()
    db.refresh(athlete)
    return athlete


def delete_athlete(db: Session, athlete_id: str) -> bool:
    athlete = db.get(Athlete, athlete_id)
    if not athlete:
        return False
    db.delete(athlete)
    db.commit()
    return True


def _to_input(a: Athlete, today) -> AthleteInput:
    """Map a stored athlete row to the engine's AthleteInput."""
    from datetime import date as _date
    return AthleteInput(
        today=today or _date.today(),
        race_id=a.race_id,
        race_date=a.race_date,
        current_weekly_km=a.current_weekly_km,
        training_days_per_week=a.training_days_per_week,
        can_run_10k_continuous=a.can_run_10k_continuous,
        longest_continuous_run_min=a.longest_continuous_run_min,
        recent_race_distance_km=a.recent_race_distance_km,
        recent_race_time_min=a.recent_race_time_min,
        goal_marathon_time_min=a.goal_marathon_time_min,
        vdot_override=a.adapted_vdot,
        name=a.name,
    )


def to_athlete_input(a: Athlete, today=None) -> AthleteInput:
    return _to_input(a, today)


# --------------------------------------------------------------------------- #
# Activities (Strava ingest)
# --------------------------------------------------------------------------- #
def upsert_activities(db: Session, athlete_id: str, items: list[dict]) -> dict:
    """Insert or update synced activities. Dedup by activity id.

    Returns {created, updated, skipped}.
    """
    created = updated = skipped = 0
    for item in items:
        act_id = str(item.get("id") or "").strip()
        if not act_id:
            skipped += 1
            continue

        existing = db.get(Activity, act_id)
        fields = _normalise_activity(item)
        if existing:
            for k, v in fields.items():
                setattr(existing, k, v)
            existing.synced_at = datetime.utcnow()
            updated += 1
        else:
            db.add(Activity(id=act_id, athlete_id=athlete_id, **fields))
            created += 1

    db.commit()
    return {"created": created, "updated": updated, "skipped": skipped}


def _normalise_activity(item: dict) -> dict:
    """Map an incoming (already-normalised) activity payload to ORM fields.

    The n8n workflow is responsible for mapping raw Strava fields to this shape
    (see docs/STRAVA_SYNC_CONTRACT.md). We accept the normalised contract and,
    where pace is missing but distance+time exist, derive it.
    """
    dist = float(item.get("distance_km") or 0.0)
    moving = float(item.get("moving_time_min") or 0.0)
    pace = item.get("average_pace_min_per_km")
    if pace is None and dist > 0 and moving > 0:
        pace = round(moving / dist, 3)

    start = item.get("start_date")
    if isinstance(start, str):
        start = datetime.fromisoformat(start.replace("Z", "+00:00"))

    return {
        "start_date": start,
        "activity_type": item.get("activity_type", "Run"),
        "name": item.get("name"),
        "distance_km": dist,
        "moving_time_min": moving,
        "elapsed_time_min": item.get("elapsed_time_min"),
        "total_elevation_gain_m": item.get("total_elevation_gain_m"),
        "average_pace_min_per_km": pace,
        "average_heartrate": item.get("average_heartrate"),
        "max_heartrate": item.get("max_heartrate"),
        "suffer_score": item.get("suffer_score"),
        "raw": json.dumps(item.get("raw")) if item.get("raw") is not None else None,
    }


def list_activities(db: Session, athlete_id: str, limit: int = 50) -> list[Activity]:
    stmt = (
        select(Activity)
        .where(Activity.athlete_id == athlete_id)
        .order_by(Activity.start_date.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def recompute_current_weekly_km(db: Session, athlete_id: str, weeks: int = 4) -> float:
    """Update the athlete's current_weekly_km from recent Strava activity (truth).

    Average weekly running distance over the last `weeks` weeks.
    """
    athlete = db.get(Athlete, athlete_id)
    if not athlete:
        return 0.0
    cutoff = datetime.utcnow() - timedelta(weeks=weeks)
    stmt = select(Activity).where(
        Activity.athlete_id == athlete_id,
        Activity.start_date >= cutoff,
        Activity.activity_type == "Run",
    )
    runs = list(db.scalars(stmt))
    total_km = sum(r.distance_km for r in runs)
    weekly = round(total_km / max(1, weeks), 1)
    athlete.current_weekly_km = weekly
    _recache_vdot(athlete)
    db.commit()
    return weekly


# --------------------------------------------------------------------------- #
# Adaptation
# --------------------------------------------------------------------------- #
def _runs_between(db: Session, athlete_id: str, start, end) -> list[Activity]:
    """Run activities with start_date in [start, end) (dates)."""
    from datetime import datetime as _dt
    lo = _dt.combine(start, _dt.min.time())
    hi = _dt.combine(end, _dt.min.time())
    stmt = select(Activity).where(
        Activity.athlete_id == athlete_id,
        Activity.activity_type == "Run",
        Activity.start_date >= lo,
        Activity.start_date < hi,
    )
    return list(db.scalars(stmt))


def adapt_week(db: Session, athlete_id: str, as_of=None) -> dict:
    """Evaluate the most recently completed plan week and apply the nudge.

    `as_of` (date) defaults to today. Finds the latest plan week whose end_date
    is on/before as_of, compares planned vs actual, applies the VDOT nudge to the
    athlete, and logs the decision. Returns a serialisable result.
    """
    from dataclasses import asdict
    from datetime import date as _date, timedelta

    athlete = db.get(Athlete, athlete_id)
    if not athlete:
        return {"adapted": False, "reason": "athlete not found"}

    as_of = as_of or _date.today()
    plan = build_plan(_to_input(athlete, today=as_of))

    # Most recently completed week (end_date <= as_of).
    completed = [w for w in plan.weeks if w.end_date and w.end_date <= as_of]
    if not completed:
        return {"adapted": False, "reason": "no completed week yet"}
    week = max(completed, key=lambda w: w.end_date)

    week_runs = _runs_between(db, athlete_id, week.start_date, week.end_date)
    recent_runs = _runs_between(
        db, athlete_id, as_of - timedelta(weeks=4), as_of + timedelta(days=1)
    )

    current_vdot = athlete.adapted_vdot or round(
        vdot_mod.estimate_vdot(_to_input(athlete, today=as_of)), 1
    )
    in_taper = week.phase == "5"

    decision = evaluate_week(
        planned_week=week,
        week_runs=week_runs,
        recent_runs=recent_runs,
        current_vdot=current_vdot,
        in_taper=in_taper,
    )

    # Apply + log.
    athlete.adapted_vdot = decision.vdot_after
    athlete.vdot = decision.vdot_after
    db.add(AdaptationLog(
        athlete_id=athlete_id,
        week_index=decision.week_index,
        weeks_to_race=decision.weeks_to_race,
        phase=decision.phase,
        vdot_before=decision.vdot_before,
        vdot_after=decision.vdot_after,
        decision=json.dumps(asdict(decision)),
    ))
    db.commit()

    out = asdict(decision)
    out["adapted"] = True
    out["vdot_delta"] = decision.vdot_delta
    return out


def list_adaptations(db: Session, athlete_id: str, limit: int = 20) -> list[dict]:
    stmt = (
        select(AdaptationLog)
        .where(AdaptationLog.athlete_id == athlete_id)
        .order_by(AdaptationLog.created_at.desc())
        .limit(limit)
    )
    rows = list(db.scalars(stmt))
    return [
        {
            "created_at": r.created_at.isoformat(),
            "week_index": r.week_index,
            "weeks_to_race": r.weeks_to_race,
            "phase": r.phase,
            "vdot_before": r.vdot_before,
            "vdot_after": r.vdot_after,
            "decision": json.loads(r.decision),
        }
        for r in rows
    ]
