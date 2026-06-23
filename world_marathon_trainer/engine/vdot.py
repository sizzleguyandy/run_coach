"""VDOT / pace engine (Jack Daniels).

VDOT is a fitness index derived from a race performance. From it we compute the
five Daniels training paces (E, M, T, I, R) in minutes per kilometre.

Two Daniels equations:
    VO2 demand of running at velocity v (m/min):
        VO2 = -4.60 + 0.182258 * v + 0.000104 * v^2
    Fraction of VO2max sustainable for t minutes:
        %max = 0.8 + 0.1894393 * e^(-0.012778 t) + 0.2989558 * e^(-0.1932605 t)

VDOT from a race = VO2_demand(race velocity) / %max(race time).

Training paces: each zone is run at a fixed fraction of VDOT (as VO2). Invert the
VO2 equation (a quadratic in v) to get the velocity, then pace = 1000 / v.
"""

from __future__ import annotations

import math

# Intensity of each training zone as a fraction of VDOT (VO2 terms).
# These approximate Daniels' published pace tables.
ZONE_INTENSITY = {
    "E": 0.72,   # easy / long-run aerobic
    "M": 0.84,   # marathon pace
    "T": 0.88,   # threshold ("comfortably hard")
    "I": 0.98,   # interval (~VO2max)
    "R": 1.06,   # repetition (speed/economy)
}


def _vo2_demand(v_m_per_min: float) -> float:
    return -4.60 + 0.182258 * v_m_per_min + 0.000104 * v_m_per_min ** 2


def _pct_max(t_min: float) -> float:
    return (
        0.8
        + 0.1894393 * math.exp(-0.012778 * t_min)
        + 0.2989558 * math.exp(-0.1932605 * t_min)
    )


def _velocity_for_vo2(vo2: float) -> float:
    """Invert the VO2 demand quadratic for velocity (m/min), positive root."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    disc = b * b - 4 * a * c
    return (-b + math.sqrt(disc)) / (2 * a)


def vdot_from_race(distance_km: float, time_min: float) -> float:
    """Compute VDOT from a race result."""
    if distance_km <= 0 or time_min <= 0:
        raise ValueError("distance and time must be positive")
    v = (distance_km * 1000.0) / time_min
    return _vo2_demand(v) / _pct_max(time_min)


def paces_from_vdot(vdot: float) -> dict[str, float]:
    """Return {zone: pace_min_per_km} for all five zones."""
    out: dict[str, float] = {}
    for zone, intensity in ZONE_INTENSITY.items():
        vo2 = vdot * intensity
        v = _velocity_for_vo2(vo2)         # m/min
        out[zone] = 1000.0 / v             # min/km
    return out


def estimate_vdot(athlete) -> float:
    """Best-effort VDOT for an athlete from available inputs.

    Priority: recent race > goal marathon time > conservative default from
    current weekly volume.
    """
    if athlete.recent_race_distance_km and athlete.recent_race_time_min:
        return vdot_from_race(
            athlete.recent_race_distance_km, athlete.recent_race_time_min
        )
    if athlete.goal_marathon_time_min:
        return vdot_from_race(42.195, athlete.goal_marathon_time_min)
    # Fallback: crude map from current weekly volume. Deliberately conservative.
    km = athlete.current_weekly_km
    if km < 30:
        return 35.0
    if km < 50:
        return 42.0
    if km < 80:
        return 48.0
    return 54.0


def fmt_pace(pace_min_per_km: float) -> str:
    """Format a min/km pace as M:SS."""
    m = int(pace_min_per_km)
    s = round((pace_min_per_km - m) * 60)
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}/km"
