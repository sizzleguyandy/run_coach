"""Pydantic request/response schemas for the API.

These mirror the engine dataclasses but live in the API layer so the engine
stays free of any web-framework dependency.
"""

from __future__ import annotations

from datetime import date, datetime
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


# --------------------------------------------------------------------------- #
# Athlete persistence
# --------------------------------------------------------------------------- #
class AthleteCreate(BaseModel):
    """Create/onboard an athlete. `id` optional — generated if omitted."""

    id: Optional[str] = None
    name: str = "Athlete"
    strava_athlete_id: Optional[str] = None

    race_id: str
    race_date: date

    current_weekly_km: float = Field(0.0, ge=0)
    training_days_per_week: int = Field(4, ge=3, le=7)
    can_run_10k_continuous: bool = False
    longest_continuous_run_min: float = 0.0
    recent_race_distance_km: Optional[float] = Field(None, gt=0)
    recent_race_time_min: Optional[float] = Field(None, gt=0)
    goal_marathon_time_min: Optional[float] = Field(None, gt=0)


class AthleteUpdate(BaseModel):
    """Partial update — every field optional."""

    name: Optional[str] = None
    strava_athlete_id: Optional[str] = None
    race_id: Optional[str] = None
    race_date: Optional[date] = None
    current_weekly_km: Optional[float] = Field(None, ge=0)
    training_days_per_week: Optional[int] = Field(None, ge=3, le=7)
    can_run_10k_continuous: Optional[bool] = None
    longest_continuous_run_min: Optional[float] = None
    recent_race_distance_km: Optional[float] = Field(None, gt=0)
    recent_race_time_min: Optional[float] = Field(None, gt=0)
    goal_marathon_time_min: Optional[float] = Field(None, gt=0)


class AthleteOut(BaseModel):
    id: str
    name: str
    strava_athlete_id: Optional[str] = None
    race_id: str
    race_date: date
    current_weekly_km: float
    training_days_per_week: int
    can_run_10k_continuous: bool
    longest_continuous_run_min: float
    recent_race_distance_km: Optional[float] = None
    recent_race_time_min: Optional[float] = None
    goal_marathon_time_min: Optional[float] = None
    vdot: Optional[float] = None

    model_config = {"from_attributes": True}


# --------------------------------------------------------------------------- #
# Strava sync
# --------------------------------------------------------------------------- #
class SyncActivity(BaseModel):
    """One activity as sent by an athlete's n8n workflow.

    n8n maps raw Strava fields to this shape (see STRAVA_SYNC_CONTRACT.md).
    `id` is the Strava activity id — used for dedup on re-sync.
    """

    id: str
    start_date: datetime
    activity_type: str = "Run"
    name: Optional[str] = None
    distance_km: float = Field(..., ge=0)
    moving_time_min: float = Field(..., ge=0)
    elapsed_time_min: Optional[float] = None
    total_elevation_gain_m: Optional[float] = None
    average_pace_min_per_km: Optional[float] = None
    average_heartrate: Optional[float] = None
    max_heartrate: Optional[float] = None
    suffer_score: Optional[float] = None
    raw: Optional[dict] = None


class SyncRequest(BaseModel):
    activities: list[SyncActivity] = Field(default_factory=list)
    recompute_weekly_km: bool = True

    model_config = {
        "json_schema_extra": {
            "example": {
                "activities": [
                    {
                        "id": "13371337",
                        "start_date": "2026-06-22T05:30:00Z",
                        "activity_type": "Run",
                        "name": "Morning Run",
                        "distance_km": 12.4,
                        "moving_time_min": 64.2,
                        "total_elevation_gain_m": 88,
                        "average_heartrate": 148,
                        "suffer_score": 73,
                    }
                ],
                "recompute_weekly_km": True,
            }
        }
    }


class ConditionRequest(BaseModel):
    """A classified athlete condition. The agent maps free text to `condition`."""

    condition: str = Field(
        ..., description="one of: tired, niggle, pain, illness_mild, "
                         "illness_systemic, missed, great, stress")
    severity: Optional[str] = Field(None, description="mild | moderate | severe")
    body_area: Optional[str] = Field(None, description="e.g. 'left knee'")
    note: Optional[str] = None

    model_config = {
        "json_schema_extra": {
            "example": {"condition": "niggle", "severity": "mild",
                        "body_area": "left knee"}
        }
    }


class OnboardRequest(BaseModel):
    """Onboard via Strava history. The athlete's recent runs are read to derive
    starting fitness; race + committed days are supplied by the athlete.
    """

    id: Optional[str] = None
    name: str = "Athlete"
    strava_athlete_id: Optional[str] = None

    race_id: str
    race_date: date
    # Days the athlete can COMMIT to going forward (drives the peak cap). If
    # omitted, the observed training pattern is used.
    training_days_committed: Optional[int] = Field(None, ge=3, le=7)
    today: Optional[date] = None
    preview: bool = False

    activities: list[SyncActivity] = Field(default_factory=list)

    model_config = {
        "json_schema_extra": {
            "example": {
                "name": "Andrew",
                "race_id": "cape_town",
                "race_date": "2027-05-23",
                "training_days_committed": 5,
                "preview": True,
                "activities": [
                    {"id": "a1", "start_date": "2026-05-01T05:30:00Z",
                     "distance_km": 10.0, "moving_time_min": 55.0},
                    {"id": "a2", "start_date": "2026-05-04T05:30:00Z",
                     "distance_km": 16.0, "moving_time_min": 92.0},
                ],
            }
        }
    }


class ActivityOut(BaseModel):
    id: str
    start_date: datetime
    activity_type: str
    name: Optional[str] = None
    distance_km: float
    moving_time_min: float
    total_elevation_gain_m: Optional[float] = None
    average_pace_min_per_km: Optional[float] = None
    average_heartrate: Optional[float] = None
    suffer_score: Optional[float] = None

    model_config = {"from_attributes": True}
