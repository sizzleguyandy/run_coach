"""
Prediction API router — V2 onboarding.

GET  /predict/races   → list all pre-loaded SA race presets
POST /predict/        → return time range prediction for runner + race
"""
from fastapi import APIRouter, Query
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
async def list_races(
    country: Optional[str] = Query(
        None,
        description="Filter by country code: ZA (South Africa) or GB (United Kingdom). Omit for all races.",
    ),
):
    """Return race presets for the onboarding race picker. Optionally filter by country."""
    from coach_core.engine.race_presets_sa import RACE_PRESETS_SA as SA_PRESETS
    from coach_core.engine.race_presets_uk import RACE_PRESETS_UK as UK_PRESETS

    # Build a combined map with country tag
    all_presets: dict = {}
    for pid, p in SA_PRESETS.items():
        all_presets[pid] = {**p, "_country": "ZA"}
    for pid, p in UK_PRESETS.items():
        all_presets[pid] = {**p, "_country": "GB"}
    # Fall back to the main presets dict for anything not in SA/UK files
    for pid, p in RACE_PRESETS.items():
        if pid not in all_presets:
            all_presets[pid] = {**p, "_country": "ZA"}  # default

    country_filter = country.upper() if country else None

    races = []
    for pid, p in all_presets.items():
        if country_filter and p.get("_country") != country_filter:
            continue
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
            "country":          p.get("_country", "ZA"),
        })

    races.append({
        "id":          "custom",
        "name":        "Other race (custom)",
        "emoji":       "🏁",
        "distance_km": None,
        "country":     country_filter or "ZA",
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
        "vo2x":         result.vo2x,
        "training_focus": result.training_focus,
        "warnings":     result.warnings,
        "weeks_to_race": result.weeks_to_race,
    }
