"""Data models for the training engine. Plain dataclasses, no behaviour."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #
@dataclass
class AthleteInput:
    """Everything the engine needs to build a plan for one athlete.

    Fitness inputs come from Strava (ground truth) where possible.
    """

    today: date
    race_id: str
    race_date: date

    # Current fitness (Strava truth)
    current_weekly_km: float
    training_days_per_week: int

    # Ability markers used for slot-in. Provide whatever is known.
    can_run_10k_continuous: bool = False
    longest_continuous_run_min: float = 0.0

    # Recent race result for VDOT (optional but strongly preferred).
    # distance in km, time in minutes.
    recent_race_distance_km: Optional[float] = None
    recent_race_time_min: Optional[float] = None

    # Goal marathon time in minutes (optional; used if no recent race).
    goal_marathon_time_min: Optional[float] = None

    # Effective VDOT set by the adaptation loop. When present it wins over the
    # race-derived estimate (the loop has the freshest, Strava-backed read).
    vdot_override: Optional[float] = None

    name: str = "Athlete"


# --------------------------------------------------------------------------- #
# Outputs
# --------------------------------------------------------------------------- #
@dataclass
class Session:
    """A single day's training."""

    day: str                       # "Mon".."Sun"
    kind: str                      # "rest","easy","long","quality","walk_run","race"
    description: str               # human-readable session
    distance_km: Optional[float] = None
    duration_min: Optional[float] = None
    pace_min_per_km: Optional[float] = None   # target pace where applicable
    tags: list[str] = field(default_factory=list)  # e.g. ["course:late_hill"]


@dataclass
class Week:
    """One training week."""

    index: int                     # absolute week number in the plan (1-based)
    phase: str                     # "1a","1b","2","3","4","5"
    phase_label: str               # human-readable
    weeks_to_race: Optional[int]   # None until the 18-week block begins
    target_volume_km: float
    days: list[Session] = field(default_factory=list)
    note: str = ""
    # Calendar anchor (computed in the builder from race_date) so synced
    # activities can be matched to the week they belong to.
    start_date: Optional[date] = None
    end_date: Optional[date] = None


@dataclass
class Assessment:
    """Result of the slot-in / peak-selection step."""

    entry_phase: str               # "1a","1b","2"
    target_peak_km: float
    peak_binding_constraint: str   # "runway","days","gate"
    daniels_category: str          # e.g. "up_to_64"
    vdot: float
    runway_weeks: int              # weeks of base-building available before the block
    feasible: bool                 # can they reach the 48km gate safely in time?
    flags: list[str] = field(default_factory=list)
    rationale: list[str] = field(default_factory=list)


@dataclass
class DerivedProfile:
    """Athlete starting profile derived from recent Strava history."""

    weeks_of_data: int
    n_runs: int
    weekly_km: float
    longest_run_km: float
    longest_run_min: float
    can_run_10k: bool
    observed_training_days: int
    implied_vdot: Optional[float]
    confidence: str                  # "none","low","medium","high"
    notes: list[str] = field(default_factory=list)


@dataclass
class AdaptationDecision:
    """Result of evaluating one completed week against its plan."""

    week_index: int
    weeks_to_race: Optional[int]
    phase: str

    planned_km: float
    actual_km: float
    volume_compliance: float          # actual / planned (clamped)

    planned_sessions: int
    completed_sessions: int
    session_compliance: float

    planned_long_km: float
    actual_long_km: float
    long_run_done: bool

    avg_heartrate: Optional[float]
    avg_suffer: Optional[float]
    best_recent_vdot: float

    vdot_before: float
    vdot_after: float

    notes: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def vdot_delta(self) -> float:
        return round(self.vdot_after - self.vdot_before, 2)


@dataclass
class Plan:
    """A complete athlete plan."""

    athlete: str
    race_id: str
    race_date: date
    assessment: Assessment
    weeks: list[Week] = field(default_factory=list)

    @property
    def total_weeks(self) -> int:
        return len(self.weeks)
