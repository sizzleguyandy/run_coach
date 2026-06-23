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

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

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
from .schemas import (                                # noqa: E402
    AthleteRequest,
    RaceResultRequest,
    RaceSummary,
    VdotResponse,
)

app = FastAPI(
    title="World Marathon Trainer",
    version="0.1.0",
    description="Race-first marathon training engine. Each plan is shaped by the "
                "target race profile (Daniels methodology under the hood).",
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
