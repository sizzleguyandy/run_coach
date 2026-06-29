"""World Marathon Trainer — FastAPI app.

Run:
    cd world_marathon_trainer
    uvicorn api.main:app --reload --port 8000

Docs at http://localhost:8000/docs

This layer is deliberately thin: validate -> call engine -> serialise. All
training logic lives in engine/. n8n and the agent call these endpoints.
"""

from __future__ import annotations

import os
import sys
from dataclasses import asdict
from datetime import date
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

# Make the engine importable whether launched from repo root or this folder.
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from engine import AthleteInput, build_plan          # noqa: E402
from engine import vdot as vdot_mod                   # noqa: E402
from engine.race_profile import (                     # noqa: E402
    available_races,
    load_race,
)
from . import store                                   # noqa: E402
from .db import init_db, get_session                  # noqa: E402
from .schemas import (                                # noqa: E402
    AthleteRequest,
    RaceResultRequest,
    RaceSummary,
    VdotResponse,
    AthleteCreate,
    AthleteUpdate,
    AthleteOut,
    SyncRequest,
    ActivityOut,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="World Marathon Trainer",
    version="0.1.0",
    description="Race-first marathon training engine. Each plan is shaped by the "
                "target race profile (Daniels methodology under the hood).",
    lifespan=lifespan,
)

# Open CORS for now — tighten when the agent/n8n origins are known.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)




# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
@app.get("/health", tags=["meta"])
def health():
    return {"status": "ok", "races_loaded": len(available_races())}


# --------------------------------------------------------------------------- #
# Races
# --------------------------------------------------------------------------- #
@app.get("/races", response_model=list[RaceSummary], tags=["races"])
def list_races():
    """List all available race profiles. Adding a JSON file adds a race here."""
    out = []
    for rid in available_races():
        race = load_race(rid)
        ident = race.doc.get("identity", {})
        out.append(RaceSummary(
            id=race.id,
            name=race.name,
            distance_km=race.distance_km,
            world_major=ident.get("world_major", False),
            race_month=ident.get("race_month"),
        ))
    return out


@app.get("/races/{race_id}", tags=["races"])
def get_race(race_id: str):
    """Full race profile document (drives training + feeds the agent's RAG)."""
    try:
        return load_race(race_id).doc
    except FileNotFoundError:
        raise HTTPException(404, f"no race profile for id '{race_id}'")


# --------------------------------------------------------------------------- #
# VDOT / paces
# --------------------------------------------------------------------------- #
@app.get("/vdot/{value}", response_model=VdotResponse, tags=["vdot"])
def vdot_paces(value: float):
    """Training paces + equivalent race times + a human-readable card for a VDOT."""
    if not (20 <= value <= 90):
        raise HTTPException(400, "vdot must be between 20 and 90")
    return VdotResponse(
        vdot=value,
        paces={k: round(v, 3) for k, v in vdot_mod.paces_from_vdot(value).items()},
        race_times=vdot_mod.race_times_from_vdot(value),
        card=vdot_mod.pace_card(value),
    )


@app.post("/vdot/from-race", response_model=VdotResponse, tags=["vdot"])
def vdot_from_race(body: RaceResultRequest):
    """Compute VDOT (and paces) from a race result."""
    v = vdot_mod.vdot_from_race(body.distance_km, body.time_min)
    return VdotResponse(
        vdot=round(v, 1),
        paces={k: round(p, 3) for k, p in vdot_mod.paces_from_vdot(v).items()},
        race_times=vdot_mod.race_times_from_vdot(v),
        card=vdot_mod.pace_card(v),
    )


@app.get("/vdot-table", tags=["vdot"])
def vdot_table():
    """The full VDOT 30-85 lookup table (paces + race-time equivalents)."""
    return vdot_mod.lookup_table()


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #
@app.post("/plan", tags=["plan"])
def make_plan(body: AthleteRequest):
    """Build a complete training plan for an athlete targeting a race.

    Returns assessment (entry phase, peak, VDOT, feasibility) plus the full
    week-by-week plan from the athlete's entry point through race day.
    """
    try:
        load_race(body.race_id)
    except FileNotFoundError:
        raise HTTPException(404, f"no race profile for id '{body.race_id}'")

    athlete = AthleteInput(**body.model_dump())
    plan = build_plan(athlete)
    return asdict(plan)


@app.post("/plan/assessment", tags=["plan"])
def make_assessment(body: AthleteRequest):
    """Just the slot-in assessment (entry phase, peak, feasibility) — no weeks.

    Cheap call for onboarding: 'here's where you'd start and why'.
    """
    from engine.assessment import assess

    try:
        load_race(body.race_id)
    except FileNotFoundError:
        raise HTTPException(404, f"no race profile for id '{body.race_id}'")

    athlete = AthleteInput(**body.model_dump())
    return asdict(assess(athlete))


# --------------------------------------------------------------------------- #
# Athletes (persistence)
# --------------------------------------------------------------------------- #
@app.post("/athlete", response_model=AthleteOut, status_code=201, tags=["athletes"])
def create_athlete(body: AthleteCreate, db: Session = Depends(get_session)):
    """Onboard an athlete. Returns the id the n8n workflow keys its sync against."""
    try:
        load_race(body.race_id)
    except FileNotFoundError:
        raise HTTPException(404, f"no race profile for id '{body.race_id}'")
    return store.create_athlete(db, body.model_dump())


@app.get("/athletes", response_model=list[AthleteOut], tags=["athletes"])
def list_athletes(db: Session = Depends(get_session)):
    return store.list_athletes(db)


@app.get("/athlete/{athlete_id}", response_model=AthleteOut, tags=["athletes"])
def get_athlete(athlete_id: str, db: Session = Depends(get_session)):
    a = store.get_athlete(db, athlete_id)
    if not a:
        raise HTTPException(404, "athlete not found")
    return a


@app.patch("/athlete/{athlete_id}", response_model=AthleteOut, tags=["athletes"])
def update_athlete(athlete_id: str, body: AthleteUpdate,
                   db: Session = Depends(get_session)):
    if body.race_id:
        try:
            load_race(body.race_id)
        except FileNotFoundError:
            raise HTTPException(404, f"no race profile for id '{body.race_id}'")
    a = store.update_athlete(db, athlete_id, body.model_dump(exclude_unset=True))
    if not a:
        raise HTTPException(404, "athlete not found")
    return a


@app.delete("/athlete/{athlete_id}", status_code=204, tags=["athletes"])
def delete_athlete(athlete_id: str, db: Session = Depends(get_session)):
    if not store.delete_athlete(db, athlete_id):
        raise HTTPException(404, "athlete not found")


@app.post("/athlete/{athlete_id}/plan", tags=["athletes"])
def plan_for_athlete(athlete_id: str, db: Session = Depends(get_session)):
    """Build a plan from the stored athlete record (no need to re-send inputs)."""
    a = store.get_athlete(db, athlete_id)
    if not a:
        raise HTTPException(404, "athlete not found")
    plan = build_plan(store.to_athlete_input(a))
    return asdict(plan)


# --------------------------------------------------------------------------- #
# Strava sync (the n8n target)
# --------------------------------------------------------------------------- #
@app.post("/athlete/{athlete_id}/sync", tags=["sync"])
def sync_activities(athlete_id: str, body: SyncRequest,
                    db: Session = Depends(get_session)):
    """Ingest Strava activities for one athlete (called by their n8n workflow).

    Dedups by Strava activity id, so re-syncing overlapping windows is safe.
    Optionally recomputes the athlete's current weekly volume from the truth.
    """
    a = store.get_athlete(db, athlete_id)
    if not a:
        raise HTTPException(404, "athlete not found")

    items = [act.model_dump() for act in body.activities]
    result = store.upsert_activities(db, athlete_id, items)

    if body.recompute_weekly_km:
        result["current_weekly_km"] = store.recompute_current_weekly_km(db, athlete_id)

    return {"ok": True, **result}


@app.get("/athlete/{athlete_id}/activities", response_model=list[ActivityOut],
         tags=["sync"])
def get_activities(athlete_id: str, limit: int = 50,
                   db: Session = Depends(get_session)):
    if not store.get_athlete(db, athlete_id):
        raise HTTPException(404, "athlete not found")
    return store.list_activities(db, athlete_id, limit=limit)


# --------------------------------------------------------------------------- #
# Adaptation
# --------------------------------------------------------------------------- #
@app.post("/athlete/{athlete_id}/adapt", tags=["adaptation"])
def adapt(athlete_id: str, as_of: Optional[date] = None,
          db: Session = Depends(get_session)):
    """Evaluate the most recently completed week and apply a bounded VDOT nudge.

    n8n calls this after the weekly sync. `as_of` (YYYY-MM-DD) overrides 'today'
    for testing / backfill. Returns the decision (metrics, VDOT change, coaching
    notes, flags) — the agent narrates `notes`/`flags` to the athlete.
    """
    if not store.get_athlete(db, athlete_id):
        raise HTTPException(404, "athlete not found")
    return store.adapt_week(db, athlete_id, as_of=as_of)


@app.get("/athlete/{athlete_id}/adaptations", tags=["adaptation"])
def adaptations(athlete_id: str, limit: int = 20,
                db: Session = Depends(get_session)):
    """Audit trail of past adaptation decisions for this athlete."""
    if not store.get_athlete(db, athlete_id):
        raise HTTPException(404, "athlete not found")
    return store.list_adaptations(db, athlete_id, limit=limit)
