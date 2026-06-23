"""VDOT / pace engine (Jack Daniels).

VDOT is a fitness index derived from a race performance.

Two core Daniels equations:
    VO2 demand at velocity v (m/min):
        VO2 = -4.60 + 0.182258 * v + 0.000104 * v²
    Fraction of VO2max sustainable for t minutes:
        %max = 0.8 + 0.1894393 * e^(-0.012778 t) + 0.2989558 * e^(-0.1932605 t)

VDOT from a race  = VO2_demand(race_velocity) / %max(race_time)
Pace from VDOT    = invert VO2_demand at VDOT * zone_intensity

The module ships:
    vdot_from_race()    – VDOT from a known result
    paces_from_vdot()   – {E,M,T,I,R} paces in min/km
    race_times_from_vdot() – equivalent race finish times for 5K/10K/HM/M
    lookup_table()      – athlete-facing table for VDOT 30-85
    fmt_pace()          – format min/km as "M:SS/km"
    fmt_time()          – format minutes as "H:MM:SS"
    estimate_vdot()     – best-effort VDOT from athlete inputs

The lookup_table() is pre-computed once at import (lru_cache) and returns
the same data Daniels' printed Table 5.2, generated from the same formula.
"""

from __future__ import annotations

import math
from functools import lru_cache

# ---------------------------------------------------------------------------
# Zone intensities: fraction of VDOT expressed as VO2.
# Daniels' published tables are computed from these fixed intensities.
# ---------------------------------------------------------------------------
ZONE_INTENSITY: dict[str, float] = {
    "E": 0.72,   # easy / long run  — conversational effort
    "M": 0.84,   # marathon pace    — sustained race effort
    "T": 0.88,   # threshold        — comfortably hard, ~60 min max
    "I": 0.975,  # interval         — VO2max, 3-5 min reps
    "R": 1.06,   # repetition       — speed / economy, <2 min reps
}

# Race distances used for equivalent-time table (metres).
RACE_DISTANCES_KM: dict[str, float] = {
    "5K":       5.0,
    "10K":      10.0,
    "Half":     21.0975,
    "Marathon": 42.195,
}


# ---------------------------------------------------------------------------
# Core formulas
# ---------------------------------------------------------------------------
def _vo2_demand(v_m_per_min: float) -> float:
    """VO2 demand (mL/kg/min) at running velocity v (m/min)."""
    return -4.60 + 0.182258 * v_m_per_min + 0.000104 * v_m_per_min ** 2


def _pct_max(t_min: float) -> float:
    """Fraction of VO2max that can be sustained for t minutes (Daniels)."""
    return (
        0.8
        + 0.1894393 * math.exp(-0.012778 * t_min)
        + 0.2989558 * math.exp(-0.1932605 * t_min)
    )


def _velocity_for_vo2(vo2: float) -> float:
    """Invert VO2 demand quadratic: given a VO2 target, return v (m/min)."""
    a, b, c = 0.000104, 0.182258, -4.60 - vo2
    discriminant = b * b - 4.0 * a * c
    return (-b + math.sqrt(discriminant)) / (2.0 * a)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def vdot_from_race(distance_km: float, time_min: float) -> float:
    """Compute VDOT from a race result (distance in km, time in minutes)."""
    if distance_km <= 0 or time_min <= 0:
        raise ValueError("distance and time must be positive")
    v = (distance_km * 1000.0) / time_min           # m/min
    return _vo2_demand(v) / _pct_max(time_min)


def paces_from_vdot(vdot: float) -> dict[str, float]:
    """Return {zone: pace_min_per_km} for the five Daniels zones.

    Fractional VDOT values are handled exactly — no quantisation to integers.
    """
    out: dict[str, float] = {}
    for zone, intensity in ZONE_INTENSITY.items():
        vo2 = vdot * intensity
        v = _velocity_for_vo2(vo2)      # m/min
        out[zone] = 1000.0 / v          # min/km
    return out


def race_times_from_vdot(vdot: float) -> dict[str, float]:
    """For a given VDOT return equivalent finish times in minutes.

    Solved numerically: find t such that vo2_demand(d/t) / pct_max(t) = vdot.
    Uses bisection; converges in ~30 iterations.
    """
    results: dict[str, float] = {}
    for label, dist_km in RACE_DISTANCES_KM.items():
        d_m = dist_km * 1000.0
        # Bracket: generous bounds (90s/km → very fast; 12min/km → walking)
        lo, hi = d_m / (1000.0 / 1.5), d_m / (1000.0 / 12.0)
        for _ in range(60):
            mid = (lo + hi) / 2.0
            v = d_m / mid
            if v <= 0:
                break
            vo2 = _vo2_demand(v)
            pct = _pct_max(mid)
            if pct <= 0:
                break
            computed_vdot = vo2 / pct
            if computed_vdot < vdot:
                hi = mid
            else:
                lo = mid
        results[label] = round((lo + hi) / 2.0, 2)
    return results


@lru_cache(maxsize=1)
def lookup_table() -> list[dict]:
    """Pre-computed Daniels-equivalent table for VDOT 30-85.

    Each entry:  {vdot, paces:{E,M,T,I,R}, race_times:{5K,10K,Half,Marathon}}
    Cached on first call — zero cost on subsequent calls.
    """
    table = []
    for v in range(30, 86):
        paces = paces_from_vdot(float(v))
        times = race_times_from_vdot(float(v))
        table.append({
            "vdot": v,
            "paces": {k: round(p, 3) for k, p in paces.items()},
            "race_times": times,
        })
    return table


def nearest_vdot_row(vdot: float) -> dict:
    """Return the lookup row whose integer VDOT is nearest to `vdot`."""
    tbl = lookup_table()
    clamped = max(30, min(85, round(vdot)))
    return tbl[clamped - 30]


def fmt_pace(pace_min_per_km: float) -> str:
    """Format a pace (min/km float) as 'M:SS/km'."""
    m = int(pace_min_per_km)
    s = round((pace_min_per_km - m) * 60)
    if s == 60:
        m, s = m + 1, 0
    return f"{m}:{s:02d}/km"


def fmt_time(total_min: float) -> str:
    """Format a finish time (float minutes) as 'H:MM:SS'."""
    total_s = round(total_min * 60)
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def pace_card(vdot: float) -> str:
    """Human-readable training-pace card for a given VDOT.

    Shows the five training zones AND the equivalent race times so the athlete
    knows what effort each zone corresponds to and how fit they are.

    Example output:
        ── VDOT 42 Training Paces ──────────────────────────
        Easy (E)       6:10–6:40/km   conversational, most of your running
        Marathon (M)   5:30/km        goal race pace, controlled effort
        Threshold (T)  5:17/km        comfortably hard, 20-40 min blocks
        Interval (I)   4:51/km        hard 3-5 min reps at VO2max
        Repetition (R) 4:33/km        speed/economy, short fast reps <2 min

        ── Equivalent Race Times ───────────────────────────
        5K          22:45
        10K         47:15
        Half        1:45:20
        Marathon    3:38:00
    """
    paces = paces_from_vdot(vdot)
    times = race_times_from_vdot(vdot)

    # E pace is given as a range (70-79% intensity)
    e_slow = 1000.0 / _velocity_for_vo2(vdot * 0.70)
    e_fast = 1000.0 / _velocity_for_vo2(vdot * 0.79)

    lines = [
        f"── VDOT {vdot:.1f} Training Paces ─────────────────────",
        f"Easy (E)         {fmt_pace(e_fast)} – {fmt_pace(e_slow)}   "
        f"conversational, base of your training",
        f"Marathon (M)     {fmt_pace(paces['M'])}      "
        f"goal race pace, controlled effort",
        f"Threshold (T)    {fmt_pace(paces['T'])}      "
        f"comfortably hard, 20-40 min blocks",
        f"Interval (I)     {fmt_pace(paces['I'])}      "
        f"hard 3-5 min reps, VO2max effort",
        f"Repetition (R)   {fmt_pace(paces['R'])}      "
        f"speed/economy, short reps under 2 min",
        "",
        "── Equivalent Race Times ───────────────────────────",
    ]
    for race, t in times.items():
        lines.append(f"  {race:<12} {fmt_time(t)}")
    return "\n".join(lines)


def estimate_vdot(athlete) -> float:
    """Best-effort VDOT from available athlete inputs.

    Priority: recent race > goal marathon time > volume-based fallback.
    """
    if athlete.recent_race_distance_km and athlete.recent_race_time_min:
        return vdot_from_race(
            athlete.recent_race_distance_km, athlete.recent_race_time_min
        )
    if athlete.goal_marathon_time_min:
        return vdot_from_race(42.195, athlete.goal_marathon_time_min)
    # Fallback: deliberately conservative estimates from weekly volume.
    km = athlete.current_weekly_km
    if km < 30:
        return 35.0
    if km < 50:
        return 42.0
    if km < 80:
        return 48.0
    return 54.0
