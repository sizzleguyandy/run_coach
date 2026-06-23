"""Pydantic request/response schemas for the API.

These mirror the engine dataclasses but live in the API layer so the engine
stays free of any web-framework dependency.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Requests
# --------------------------------------------------------------------------- #
class AthleteRequest(BaseModel):
    """Input for building a plan. Fitness fields come from Strava in production."""

    today: date = Field(..., description="Reference 'today' for the plan")
    race_id: str = Field(..., description="Race profile id, e.g. 'cape_town'")
    race_date: date

    current_weekly_km: float = Field(..., ge=0)
    training_days_per_week: int = Field(..., ge=3, le=7)

    can_run_10k_continuous: bool = False
    longest_continuous_run_min: float = 0.0

    recent_race_distance_km: Optional[float] = Field(None, gt=0)
    recent_race_time_min: Optional[float] = Field(None, gt=0)
    goal_marathon_time_min: Optional[float] = Field(None, gt=0)

    name: str = "Athlete"

    model_config = {
        "json_schema_extra": {
            "example": {
                "today": "2026-06-23",
                "race_id": "cape_town",
                "race_date": "2027-05-23",
                "current_weekly_km": 35.0,
                "training_days_per_week": 5,
                "can_run_10k_continuous": True,
                "recent_race_distance_km": 10.0,
                "recent_race_time_min": 52.0,
                "name": "Test Athlete",
            }
        }
    }


class RaceResultRequest(BaseModel):
    distance_km: float = Field(..., gt=0)
    time_min: float = Field(..., gt=0)


# --------------------------------------------------------------------------- #
# Responses (lightweight — the engine output is serialised via dataclasses)
# --------------------------------------------------------------------------- #
class RaceSummary(BaseModel):
    id: str
    name: str
    distance_km: float
    world_major: bool = False
    race_month: Optional[int] = None


class VdotResponse(BaseModel):
    vdot: float
    paces: dict[str, float]
    race_times: dict[str, float]
    card: str
