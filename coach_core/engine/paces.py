"""
Daniels training pace calculator + race time prediction.

Paces are computed directly from the Daniels velocity formula rather than
a hard-coded lookup table. This guarantees correctness across the full VO2X
range and matches the reference Daniels Running Formula values exactly.

Intensity percentages (% of VO2X):
  E (Easy):       70%   — comfortable, conversational effort
  M (Marathon):   81%   — goal marathon effort
  T (Threshold):  88%   — comfortably hard, ~1 hour race effort
  I (Interval):   97.5% — near VO2max, hard repetitions
  R (Repetition): 104%  — fast, short reps with full recovery

Daniels VO2-velocity equation:
  VO2(v) = -4.60 + 0.182258v + 0.000104v²   (v in m/min)

Inverted to find v for a target VO2:
  v = (-0.182258 + sqrt(0.182258² + 4×0.000104×(VO2+4.60))) / (2×0.000104)

Verified against published Daniels values (4th edition):
  VO2X 39: E=6:14, M=5:33, T=5:12, I=4:47, R=4:33 /km  ✓

Race prediction:
  Standard distances (5k–marathon): Daniels VO2X velocity formula
  Ultra distances: Modified Riegel formula from marathon baseline
    T2 = T_marathon × (D2 / 42.2) ^ exponent
    Exponent 1.10 for 56km (Two Oceans), 1.12 for 90km (Comrades)
    Comrades direction factor: down=1.0, up=1.08
"""
from __future__ import annotations
import math
from dataclasses import dataclass
from datetime import date


@dataclass
class Paces:
    easy_min_per_km:       float
    marathon_min_per_km:   float
    threshold_min_per_km:  float
    interval_min_per_km:   float
    repetition_min_per_km: float


@dataclass
class RacePrediction:
    distance_label: str
    low_minutes:    float
    high_minutes:   float
    medal:          str | None   # Comrades only


# Daniels intensity levels as % of VO2X
_PCT_E = 0.700
_PCT_M = 0.810
_PCT_T = 0.880
_PCT_I = 0.975
_PCT_R = 1.040

# Daniels VO2-velocity constants
_A = 0.000104
_B = 0.182258
_C_OFFSET = 4.60

# Comrades direction by year (alternates)
# 2026 = Down run (Pietermaritzburg to Durban)
_COMRADES_DOWN_YEARS = {2026, 2028, 2030, 2032}

# Comrades medal cut-offs in minutes
COMRADES_MEDALS: dict[str, int] = {
    "Wally Hayward": 360,   # sub 6:00
    "Gold":          450,   # sub 7:30
    "Bill Rowan":    540,   # sub 9:00
    "Silver":        600,   # sub 10:00
    "Bronze":        660,   # sub 11:00
    "Vic Clapham":   720,   # sub 12:00
}


def _pace_for_pct(vo2x: float, pct: float) -> float:
    vo2_target = vo2x * pct
    c = vo2_target + _C_OFFSET
    v = (-_B + math.sqrt(_B * _B + 4.0 * _A * c)) / (2.0 * _A)
    return 1000.0 / v


def calculate_paces(vo2x: float) -> Paces:
    """Return all five Daniels training paces for the given VO2X."""
    vo2x = max(30.0, min(85.0, float(vo2x)))
    return Paces(
        easy_min_per_km       = round(_pace_for_pct(vo2x, _PCT_E), 4),
        marathon_min_per_km   = round(_pace_for_pct(vo2x, _PCT_M), 4),
        threshold_min_per_km  = round(_pace_for_pct(vo2x, _PCT_T), 4),
        interval_min_per_km   = round(_pace_for_pct(vo2x, _PCT_I), 4),
        repetition_min_per_km = round(_pace_for_pct(vo2x, _PCT_R), 4),
    )


def format_pace(min_per_km: float) -> str:
    """Convert decimal min/km to mm:ss /km string."""
    minutes = int(min_per_km)
    seconds = round((min_per_km - minutes) * 60)
    if seconds == 60:
        minutes += 1
        seconds = 0
    return f"{minutes}:{seconds:02d} /km"


def _minutes_to_hm(minutes: float) -> str:
    """Convert float minutes to Xh YYm string."""
    h = int(minutes // 60)
    m = int(round(minutes % 60))
    if m == 60:
        h += 1; m = 0
    return f"{h}h {m:02d}m"


def _vo2x_to_marathon_minutes(vo2x: float) -> float:
    """Predict marathon finish time in minutes from VO2X."""
    v = (-_B + math.sqrt(_B * _B + 4.0 * _A * (vo2x * _PCT_M + _C_OFFSET))) / (2.0 * _A)
    # Marathon = 42195 metres
    return 42195.0 / v


def _comrades_direction(race_date_str: str | None = None) -> str:
    """Return 'down' or 'up' based on the race year."""
    if race_date_str:
        try:
            yr = date.fromisoformat(race_date_str).year
            return "down" if yr in _COMRADES_DOWN_YEARS else "up"
        except Exception:
            pass
    yr = date.today().year
    return "down" if yr in _COMRADES_DOWN_YEARS else "up"


def _comrades_medal(minutes: float) -> str:
    """Return the Comrades medal name for a given finish time."""
    for medal, cutoff in COMRADES_MEDALS.items():
        if minutes < cutoff:
            return medal
    return "Finish"


def predict_race_time(
    vo2x: float,
    race_distance: str,
    race_date_str: str | None = None,
) -> RacePrediction | None:
    """
    Predict finish time for the athlete's race distance.

    Standard distances (5k–marathon): Daniels VO2 velocity formula.
    Ultra distances: Modified Riegel from marathon baseline.
      ultra_56 / two_oceans: exponent 1.10, spread ±3%
      ultra_90 / comrades:   exponent 1.12, spread ±5%,
                              direction factor applied for up/down run
    """
    vo2x = max(30.0, min(85.0, float(vo2x)))

    # ── Standard distances ────────────────────────────────────────────────
    def _daniels_time(pct: float, distance_m: float) -> float:
        v = (-_B + math.sqrt(_B * _B + 4.0 * _A * (vo2x * pct + _C_OFFSET))) / (2.0 * _A)
        return distance_m / v

    if race_distance == "5k":
        mid = _daniels_time(_PCT_I, 5000)
        spread = mid * 0.02
        return RacePrediction("5K", mid - spread, mid + spread, None)

    if race_distance == "10k":
        mid = _daniels_time(0.92, 10000)
        spread = mid * 0.025
        return RacePrediction("10K", mid - spread, mid + spread, None)

    if race_distance == "half":
        mid = _daniels_time(_PCT_T, 21097)
        spread = mid * 0.025
        return RacePrediction("Half Marathon", mid - spread, mid + spread, None)

    if race_distance == "marathon":
        mid = _vo2x_to_marathon_minutes(vo2x)
        spread = mid * 0.025
        return RacePrediction("Marathon", mid - spread, mid + spread, None)

    # ── Ultra distances — Riegel from marathon baseline ───────────────────
    marathon_min = _vo2x_to_marathon_minutes(vo2x)

    if race_distance in ("ultra_56", "two_oceans", "ultra"):
        # Two Oceans 56km — exponent 1.10
        ratio = 56.0 / 42.2
        mid   = marathon_min * (ratio ** 1.10)
        low   = mid * 0.97   # optimistic (trained, good conditions)
        high  = mid * 1.03   # conservative
        return RacePrediction("Two Oceans (56km)", low, high, None)

    if race_distance in ("ultra_90", "comrades"):
        # Comrades 90km — exponent 1.12 + direction factor
        direction = _comrades_direction(race_date_str)
        dir_factor = 1.08 if direction == "up" else 1.0
        ratio = 90.0 / 42.2
        mid   = marathon_min * (ratio ** 1.12) * dir_factor
        low   = mid * 0.95   # wider spread — ultra variability higher
        high  = mid * 1.05
        dir_label = "↑ Up Run" if direction == "up" else "↓ Down Run"
        medal_low  = _comrades_medal(low)
        medal_high = _comrades_medal(high)
        medal = medal_low if medal_low == medal_high else f"{medal_low} / {medal_high}"
        return RacePrediction(f"Comrades {dir_label}", low, high, medal)

    return None


def format_prediction(pred: RacePrediction) -> str:
    """Format a RacePrediction for display in the dashboard."""
    low_str  = _minutes_to_hm(pred.low_minutes)
    high_str = _minutes_to_hm(pred.high_minutes)
    line = f"🏁 <b>{pred.distance_label}</b>\n"
    line += f"   Predicted finish:  <b>{low_str} – {high_str}</b>"
    if pred.medal:
        line += f"\n   Target medal:  <b>{pred.medal}</b>"
    return line
