"""
Prediction API router — V2 onboarding.

GET  /predict/races   → list all pre-loaded SA race presets
POST /predict/        → return time range prediction for runner + race
"""
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional
from datetime import date

from coach_core.engine.predictor import (
    predict, PredictionInput,
    PRESET_HILL_FACTORS, HILL_PROFILES,
)
from coach_core.engine.race_presets import RACE_PRESETS

router = APIRouter(prefix="/predict", tags=["predict"])


# ── Hill factor helpers ─────────────────────────────────────────────────────

def _hilliness_to_factor(hilliness: str) -> float:
    """Map existing hilliness string to hill factor."""
    mapping = {"low": 0.045, "medium": 0.08, "high": 0.115}
    return mapping.get(hilliness, 0.045)


# ── GET /predict/races ──────────────────────────────────────────────────────

@router.get("/races")
async def list_races():
    """Return all pre-loaded SA race presets for V2 onboarding race picker."""
    races = []
    for pid, p in RACE_PRESETS.items():
        hill_factor = PRESET_HILL_FACTORS.get(pid, _hilliness_to_factor(p.get("hilliness", "low")))
        races.append({
            "id":               pid,
            "name":             p["display_name"],
            "emoji":            p["emoji"],
            "distance_km":      p["exact_distance_km"],
            "race_distance":    p["race_distance"],
            "hilliness":        p.get("hilliness", "low"),
            "hill_factor":      hill_factor,
            "elevation_gain_m": p.get("elevation_gain_m", 0),
            "description":      p.get("description", ""),
        })

    races.append({
        "id":          "custom",
        "name":        "Other race (custom)",
        "emoji":       "🏁",
        "distance_km": None,
        "is_custom":   True,
    })
    return races


# ── POST /predict/ ──────────────────────────────────────────────────────────

class PredictRequest(BaseModel):
    # Race
    race_name:          str
    race_distance_km:   float
    hill_factor:        float
    race_date:          date
    requires_qualifier: bool = False
    qualifier_standard: Optional[str] = None

    # Runner
    has_recent_race:            bool
    recent_race_distance_km:    Optional[float] = None
    recent_race_time_minutes:   Optional[float] = None
    beginner_ability:           Optional[str] = None

    # Fitness
    weekly_mileage_km: float
    longest_run_km:    float
    plan_type:         str = "balanced"   # "balanced" | "conservative" | "injury_prone"


@router.post("/")
async def get_prediction(req: PredictRequest):
    inp = PredictionInput(
        race_name=req.race_name,
        race_distance_km=req.race_distance_km,
        hill_factor=req.hill_factor,
        race_date=req.race_date,
        requires_qualifier=req.requires_qualifier,
        qualifier_standard=req.qualifier_standard,
        has_recent_race=req.has_recent_race,
        recent_race_distance_km=req.recent_race_distance_km,
        recent_race_time_minutes=req.recent_race_time_minutes,
        beginner_ability=req.beginner_ability,
        weekly_mileage_km=req.weekly_mileage_km,
        longest_run_km=req.longest_run_km,
        plan_type=req.plan_type,
    )
    result = predict(inp)
    return {
        "finish_range": {
            "low":         result.low_fmt(),
            "high":        result.high_fmt(),
            "low_minutes": round(result.low_minutes),
            "high_minutes": round(result.high_minutes),
        },
        "goal_time":    result.mid_fmt(),
        "vdot":         result.vdot,
        "training_focus": result.training_focus,
        "warnings":     result.warnings,
        "weeks_to_race": result.weeks_to_race,
    }
