"""
Race Time Predictor — V2 onboarding engine.

Implements the Race Time Predictor & Training Recommender V1.0 spec.
Works alongside the existing Daniels VDOT engine (paces.py, adaptation.py).

Two paths:
  Experienced: VDOT from recent race result -> scale to target race
  Beginner:    5K estimate from ability level -> scale to target race

Both paths apply fitness modifier, improvement factor, and final range.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import math as _math

from coach_core.engine.adaptation import calculate_vdot_from_race, vdot_to_5k_minutes

# Daniels velocity-formula constants (mirrors paces.py — kept local to avoid circular import)
_DA = 0.000104
_DB = 0.182258
_DC = 4.60


def _daniels_time_minutes(vdot: float, distance_km: float) -> float:
    """
    Predict finish time using the Daniels velocity formula directly.
    Uses the same engine as paces.py so predictions are internally consistent.

    Intensity by distance:
      ≤5.5km   → I pace (97.5% VDOT) — 5K race effort
      ≤11km    → 92% VDOT — 10K effort
      ≤22km    → T pace (88% VDOT) — half marathon threshold effort
      ≤43km    → M pace (81% VDOT) — marathon effort
      >43km    → Riegel from marathon baseline (ultra distances)
    """
    vdot = max(30.0, min(85.0, float(vdot)))
    dist_m = distance_km * 1000.0

    if distance_km <= 5.5:
        pct = 0.975
    elif distance_km <= 11.0:
        pct = 0.92
    elif distance_km <= 22.0:
        pct = 0.88
    elif distance_km <= 43.0:
        pct = 0.81
    else:
        # Ultra: scale from marathon using Riegel exponents
        marathon_v = (-_DB + _math.sqrt(_DB*_DB + 4.0*_DA*(vdot*0.81 + _DC))) / (2.0*_DA)
        marathon_min = 42195.0 / marathon_v
        exp = 1.10 if distance_km <= 60.0 else 1.12
        return marathon_min * (distance_km / 42.2) ** exp

    v = (-_DB + _math.sqrt(_DB*_DB + 4.0*_DA*(vdot*pct + _DC))) / (2.0*_DA)
    return dist_m / v


# ── Beginner ability -> predicted 5K time (minutes) ───────────────────────

BEGINNER_5K_TIMES: dict[str, float] = {
    "couch":        42.0,   # run/walk
    "run5k_slow":   35.0,   # can run 5km but slowly
    "finished_c25k": 32.0,  # just completed C25K
    "run10k":       29.0,   # can run 10km comfortably
}


# ── Hill factor per named preset ───────────────────────────────────────────
# Derived from spec Section 2.1 + existing race_presets.py data.

PRESET_HILL_FACTORS: dict[str, float] = {
    # SA races
    "comrades_marathon":             0.18,   # average of up (0.22) and down (0.14)
    "two_oceans_marathon":           0.10,   # 747m gain — medium (revised down from 1100m)
    "cape_town_marathon":            0.05,
    "soweto_marathon":               0.07,
    "durban_international_marathon": 0.02,   # 30m gain — very flat
    "knysna_forest_marathon":        0.10,   # 700m trail gain — trail penalty applied elsewhere
    # UK races
    "london_marathon":               0.02,   # 127m — essentially flat
    "manchester_marathon":           0.01,   # 54m — flattest in UK
    "brighton_marathon":             0.03,
    "edinburgh_marathon":            0.01,   # 38m net downhill
    "yorkshire_marathon":            0.03,
    "loch_ness_marathon":            0.06,   # 349m rolling Highland terrain
}


# ── Custom hill profile -> (hill_factor, race_hilliness label) ─────────────

HILL_PROFILES: dict[str, dict] = {
    "flat":     {"hill_factor": 0.01,  "race_hilliness": "low"},
    "rolling":  {"hill_factor": 0.045, "race_hilliness": "low"},
    "hilly":    {"hill_factor": 0.115, "race_hilliness": "high"},
    "mountain": {"hill_factor": 0.215, "race_hilliness": "high"},
}


# ── V2 plan type -> existing training_profile ──────────────────────────────

PLAN_TYPE_TO_PROFILE: dict[str, str] = {
    "balanced":     "aggressive",
    "conservative": "conservative",
    "injury_prone": "conservative",
}


# ── Utility ────────────────────────────────────────────────────────────────

def fmt_time(minutes: float) -> str:
    """Format minutes as h:mm."""
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h}:{m:02d}"


def km_to_race_distance(km: float) -> str:
    """Map a distance in km to the existing race_distance string."""
    if km <= 5.5:
        return "5k"
    if km <= 11.0:
        return "10k"
    if km <= 22.0:
        return "half"
    if km <= 43.0:
        return "marathon"
    if km <= 60.0:
        return "ultra_56"
    return "ultra_90"


# ── Data classes ───────────────────────────────────────────────────────────

@dataclass
class PredictionInput:
    # Race info
    race_name:          str
    race_distance_km:   float
    hill_factor:        float
    race_date:          date
    requires_qualifier: bool = False
    qualifier_standard: Optional[str] = None

    # Runner — one of the two paths must be filled
    has_recent_race:            bool = True
    recent_race_distance_km:    Optional[float] = None
    recent_race_time_minutes:   Optional[float] = None
    beginner_ability:           Optional[str] = None  # key from BEGINNER_5K_TIMES

    # Current fitness
    weekly_mileage_km: float = 30.0
    longest_run_km:    float = 15.0

    # Plan type
    plan_type: str = "balanced"   # "balanced" | "conservative" | "injury_prone"

    # Direct VDOT input (overrides both has_recent_race paths)
    direct_vdot: Optional[float] = None


@dataclass
class PredictionResult:
    low_minutes:       float
    high_minutes:      float
    goal_mid_minutes:  float
    vdot:              Optional[float]
    training_focus:    list = field(default_factory=list)
    warnings:          list = field(default_factory=list)
    weeks_to_race:     int = 0

    def low_fmt(self) -> str:
        return fmt_time(self.low_minutes)

    def high_fmt(self) -> str:
        return fmt_time(self.high_minutes)

    def mid_fmt(self) -> str:
        return fmt_time(self.goal_mid_minutes)


# ── Internal calculation steps ─────────────────────────────────────────────

def _weeks_to_race(race_date: date) -> int:
    days = (race_date - date.today()).days
    return max(1, min(52, days // 7))


def _base_time(inp: PredictionInput) -> tuple[float, Optional[float]]:
    """Compute base time (minutes) and VDOT. Returns (base_minutes, vdot)."""
    if inp.direct_vdot is not None:
        # VDOT-direct path: use the Daniels velocity formula directly.
        # This keeps predictions consistent with the training paces the athlete
        # sees in their plan (paces.py uses the same formula).
        # Hill factor is NOT applied here — VDOT is derived from a marathon
        # performance that already reflects the runner's terrain fitness.
        vdot = float(inp.direct_vdot)
        base = _daniels_time_minutes(vdot, inp.race_distance_km)
        return base, vdot

    if inp.has_recent_race:
        # Experienced path
        dist = inp.recent_race_distance_km or 42.195
        time = inp.recent_race_time_minutes or 240.0
        vdot = calculate_vdot_from_race(dist, time)

        # Marathon equivalent using Daniels power law
        marathon_eq = time * (42.195 / dist) ** 1.06

        if abs(dist - inp.race_distance_km) < 0.01:
            # Same distance — no hill or scaling factor needed.
            # VDOT already reflects performance at this distance/terrain.
            base = marathon_eq
        else:
            # Different distance — scale and apply hill penalty for terrain delta
            base = marathon_eq * (inp.race_distance_km / 42.195) * (1.0 + inp.hill_factor)
        return base, vdot

    else:
        # Beginner path
        ability = inp.beginner_ability or "run5k_slow"
        five_k = BEGINNER_5K_TIMES.get(ability, 35.0)
        vdot = calculate_vdot_from_race(5.0, five_k)

        dist = inp.race_distance_km
        if dist <= 10.0:
            multiplier = (dist / 5.0) * 1.05
        elif dist <= 21.1:
            multiplier = (dist / 5.0) * 1.10
        elif dist <= 42.2:
            multiplier = (dist / 5.0) * 1.20
        else:
            multiplier = (dist / 5.0) * 1.30

        base = five_k * multiplier * (1.0 + inp.hill_factor)
        return base, vdot


def _fitness_modifier(inp: PredictionInput, is_beginner: bool) -> float:
    weekly  = min(inp.weekly_mileage_km, 300.0)
    longest = min(inp.longest_run_km, weekly)
    target  = inp.race_distance_km

    if weekly < 40 or weekly < 0.6 * target or longest < 20 or longest < 0.5 * target:
        modifier = 1.25
    elif weekly < 60 or weekly < 0.9 * target or longest < 30 or longest < 0.7 * target:
        modifier = 1.12
    elif weekly < 80 or weekly < 1.2 * target or longest < 40 or longest < 0.9 * target:
        modifier = 1.03
    else:
        modifier = 0.97

    # Beginners get no fitness benefit (floor at 1.03)
    if is_beginner:
        modifier = max(modifier, 1.03)

    return modifier


def _improvement_factor(weeks: int, plan_type: str, has_known_vdot: bool = False) -> float:
    """
    Estimate how much the athlete can improve their finish time through training.

    Beginners (no VDOT): high improvement rates — aerobic base grows quickly.
    Trained athletes (known VDOT): low rates — they are already fit, gains are
    incremental (roughly 0.5–1 VDOT point per month of structured training).
    """
    if has_known_vdot:
        # Trained runner improvement: realistic 2–4% over a full plan
        if weeks < 4:
            base = 0.0
        elif weeks <= 8:
            base = 0.02
        elif weeks <= 16:
            base = 0.03
        else:
            base = 0.04
    else:
        # Beginner improvement: significant aerobic gains possible
        if weeks < 4:
            base = 0.0
        elif weeks <= 12:
            base = 0.06
        else:
            base = 0.10

    if plan_type == "conservative":
        base *= 0.7
    elif plan_type == "injury_prone":
        base *= 0.5
    # "aggressive" athletes follow the base improvement rate (no adjustment needed)

    return base


def _final_range(
    goal_mid: float,
    plan_type: str,
    is_beginner: bool,
    has_known_vdot: bool = False,
) -> tuple[float, float]:
    if has_known_vdot:
        # VDOT is empirical — athlete has demonstrated their fitness in a race.
        # Use a tight symmetric band: ±5% from goal mid.  Plan conservatism
        # affects improvement rate (already applied above), not the confidence
        # interval around a known fitness level.
        low_pct, high_pct = 0.95, 1.05
    elif plan_type == "balanced":
        low_pct, high_pct = 0.95, 1.05
    elif plan_type == "conservative":
        low_pct, high_pct = 0.95, 1.12
    elif plan_type == "aggressive":
        # Narrower range — athlete is fit and on a structured plan
        low_pct, high_pct = 0.96, 1.04
    else:  # injury_prone
        shift = 1.07
        low_pct  = 0.94 * shift
        high_pct = 1.08 * shift

    if is_beginner:
        low_pct  *= 0.94
        high_pct *= 1.06

    return goal_mid * low_pct, goal_mid * high_pct


def _training_focus(inp: PredictionInput, is_beginner: bool) -> list:
    focus = []

    ideal_weekly = min(inp.race_distance_km, 120)
    if inp.weekly_mileage_km < ideal_weekly * 0.8:
        focus.append(f"Build weekly mileage to {int(ideal_weekly)} km before race day")

    ideal_long = min(inp.race_distance_km * 0.7, 50)
    if inp.longest_run_km < ideal_long:
        focus.append(f"Extend your longest run to {int(ideal_long)} km")

    if inp.hill_factor > 0.08:
        focus.append("Add hill repeats once per week to prepare for the course")
    if inp.hill_factor > 0.15:
        focus.append("Include downhill running to strengthen your quads")

    if inp.plan_type == "conservative":
        focus.append("Practise a run/walk strategy from your first long runs")
        focus.append("Focus on time on feet, not pace")
    elif inp.plan_type == "injury_prone":
        focus.append("Add strength training (glutes + core) twice a week")
        focus.append("Never increase weekly mileage by more than 10% per week")

    if is_beginner:
        focus.append("Follow a structured run/walk plan to build your aerobic base safely")

    return focus[:5]


def _warnings(inp: PredictionInput, is_beginner: bool, weeks: int, vdot: Optional[float]) -> list:
    warns = []

    if inp.weekly_mileage_km < inp.race_distance_km * 0.5:
        warns.append(
            f"Your current mileage ({inp.weekly_mileage_km:.0f} km/week) is low for a "
            f"{inp.race_distance_km:.0f} km race. Build gradually to reduce injury risk."
        )

    if is_beginner and inp.race_distance_km > 30:
        warns.append(
            "Taking on a long race as a beginner is ambitious. Consider a 10K or half "
            "marathon first to gain race experience."
        )

    if weeks < 2:
        warns.append(
            "Less than 2 weeks to race — focus on tapering and rest. No meaningful "
            "fitness gains are possible now."
        )
    elif weeks <= 6:
        warns.append(
            f"You have {weeks} week{'s' if weeks != 1 else ''} to race day — "
            "your plan will focus entirely on aerobic base and a short taper. "
            "No speed or quality phases will be introduced at this window. "
            "Run your best-trained race and plan the next one with more lead time."
        )

    if inp.requires_qualifier and inp.qualifier_standard:
        warns.append(
            f"This race requires a qualifying time ({inp.qualifier_standard}). "
            "Confirm you have a valid qualifier before entering."
        )

    return warns


# ── Public API ─────────────────────────────────────────────────────────────

def predict(inp: PredictionInput) -> PredictionResult:
    """Full prediction pipeline. Returns PredictionResult."""
    # An athlete is only treated as a beginner if they have no race result AND
    # no direct VDOT. Someone who enters their VDOT knows their fitness level —
    # treating them as a beginner widens the range incorrectly.
    has_known_vdot = inp.direct_vdot is not None or inp.has_recent_race
    is_beginner = not has_known_vdot
    weeks = _weeks_to_race(inp.race_date)

    base, vdot       = _base_time(inp)

    if has_known_vdot and inp.race_distance_km <= 42.195:
        # VDOT or recent race result already captures current fitness exactly
        # for distances up to and including marathon.
        # Applying a volume-based fitness modifier on top would double-count
        # and predict a time slower than the athlete can already run.
        # We skip the modifier entirely — base IS the current realistic time.
        modifier = 1.0
    else:
        # For ultra distances (> 42.195km), VDOT alone under-predicts the
        # impact of training volume. A runner on 22km/week will suffer far
        # more than VDOT predicts at 56km+. Apply the volume modifier even
        # for athletes with a known VDOT to capture this real-world effect.
        modifier = _fitness_modifier(inp, is_beginner)

    improvement      = _improvement_factor(weeks, inp.plan_type, has_known_vdot)

    current_mid      = base * modifier
    goal_mid         = current_mid * (1.0 - improvement)

    low, high        = _final_range(goal_mid, inp.plan_type, is_beginner, has_known_vdot)
    focus            = _training_focus(inp, is_beginner)
    warns            = _warnings(inp, is_beginner, weeks, vdot)

    return PredictionResult(
        low_minutes      = low,
        high_minutes     = high,
        goal_mid_minutes = goal_mid,
        vdot             = vdot,
        training_focus   = focus,
        warnings         = warns,
        weeks_to_race    = weeks,
    )
