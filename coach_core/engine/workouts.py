import math
from coach_core.engine.paces import Paces, format_pace
from coach_core.engine.workout_templates import get_template_session
from coach_core.engine.phases import PhaseAllocation
from coach_core.engine.hills import (
    should_replace_with_hills,
    week_number_in_phase,
    get_hill_quality_session,
    get_downhill_session,
    get_hilly_long_run_note,
)

# ── Volume thresholds — number of running days ─────────────────────────────
VOLUME_THRESHOLD_LOW    = 30    # < 30 km/wk  → 3 days (Tue/Thu/Sat)
VOLUME_THRESHOLD_MEDIUM = 50    # 30–50 km/wk → 4 days (Tue/Wed/Thu/Sat)
                                # > 50 km/wk  → 5 days (original full week)

# ── Minimum session distances ──────────────────────────────────────────────
MIN_RECOVERY_KM     = 4.0   # ~20–25 min easy
MIN_MEDIUM_LONG_KM  = 5.0
MIN_LONG_KM         = 7.0

# distance_factor: 0=5k, 0=10k, 1=half, 2=marathon, 3=ultra
DISTANCE_FACTOR: dict[str, int] = {
    "5k": 0, "10k": 0, "half": 1, "marathon": 2, "ultra": 3,
}

# Ordered days for arithmetic
DAYS_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _day_idx(day: str) -> int:
    return DAYS_ORDER.index(day)


def _day_at(offset: int, base: str) -> str:
    """Return day name at `offset` positions forward from `base` (wraps)."""
    return DAYS_ORDER[(_day_idx(base) + offset) % 7]


def _gap_days(quality_day: str, long_run_day: str) -> list[str]:
    """
    Days strictly between quality_day and long_run_day going forward.
    e.g. quality=Tue, long_run=Sat → [Wed, Thu, Fri]
    """
    q = _day_idx(quality_day)
    lr = _day_idx(long_run_day)
    days = []
    idx = (q + 1) % 7
    while idx != lr:
        days.append(DAYS_ORDER[idx])
        idx = (idx + 1) % 7
    return days


def _assign_session_days(
    long_run_day: str,
    quality_day: str,
    extra_days: list,
) -> dict[str, str]:
    """
    Map each day of the week to a session type using explicit athlete-chosen days.

    Session types: 'quality', 'recovery', 'medium_long', 'long', 'rest'

    Rules:
      - long_run_day → always 'long'
      - quality_day  → always 'quality'
      - extra_days   → assigned recovery / medium_long by position in week
          * first extra after quality_day  → recovery (legs still tired from quality)
          * middle extras                  → medium_long
          * extra day after long_run_day   → recovery (post long-run flush)
      - all other days → rest
    """
    assignment = {d: "rest" for d in DAYS_ORDER}
    assignment[long_run_day] = "long"
    assignment[quality_day]  = "quality"

    if not extra_days:
        return assignment

    # Sort extra days in week order, grouped into pre-long-run and post-long-run
    lr_idx = _day_idx(long_run_day)
    q_idx  = _day_idx(quality_day)

    def week_distance(day: str) -> int:
        """Positions forward from quality_day (0=quality, 1=next day, ...)."""
        return (_day_idx(day) - q_idx) % 7

    sorted_extras = sorted(extra_days, key=week_distance)

    n = len(sorted_extras)

    if n == 1:
        # Single extra: medium_long is best (one key aerobic session)
        assignment[sorted_extras[0]] = "medium_long"

    elif n == 2:
        # First extra (closer to quality) → recovery
        # Second extra (closer to long run) → medium_long
        assignment[sorted_extras[0]] = "recovery"
        assignment[sorted_extras[1]] = "medium_long"

    elif n == 3:
        # recovery → medium_long → recovery
        assignment[sorted_extras[0]] = "recovery"
        assignment[sorted_extras[1]] = "medium_long"
        assignment[sorted_extras[2]] = "recovery"

    else:
        # 4+ extras: recovery, medium_long, recovery, then remaining = recovery
        assignment[sorted_extras[0]] = "recovery"
        assignment[sorted_extras[1]] = "medium_long"
        assignment[sorted_extras[2]] = "recovery"
        for d in sorted_extras[3:]:
            assignment[d] = "recovery"

    return assignment


def get_long_run_km(weekly_volume: float, race_distance: str) -> float:
    """Long run distance — standard percentages, marathon capped at 32 km."""
    df = DISTANCE_FACTOR.get(race_distance, 1)
    pct = min(0.25 + 0.025 * df, 0.35)
    if race_distance in ("ultra", "ultra_56", "ultra_90"):
        pct = 0.30
    long_run = weekly_volume * pct
    if race_distance == "marathon":
        long_run = min(long_run, 32.0)
    return round(long_run, 1)


def get_quality_session(
    phase: int,
    weekly_volume: float,
    paces: Paces,
    hilliness: str = "low",
    week_in_phase: int = 1,
) -> dict:
    """
    Tuesday quality session — selects from Daniels template library with rotation.

    If hilliness warrants it, delegates to the hill workout engine instead.
    Otherwise routes to workout_templates.get_template_session() which:
      - Selects the correct Daniels mileage category (A–G) for the athlete
      - Rotates through all sessions in that category using week_in_phase
      - Returns a different workout each week within the same phase
    """
    if should_replace_with_hills(phase, hilliness, week_in_phase):
        return get_hill_quality_session(phase, hilliness, weekly_volume)

    return get_template_session(
        phase=phase,
        weekly_volume_km=weekly_volume,
        paces=paces,
        week_in_phase=week_in_phase,
    )


def _scale_sessions(targets: dict[str, float], weekly_volume: float) -> dict[str, float]:
    """
    If the sum of planned distances exceeds 110% of weekly_volume,
    scale all sessions down proportionally to 105% of weekly_volume.
    Returns rounded values.
    """
    total = sum(targets.values())
    if total > weekly_volume * 1.10:
        scale = (weekly_volume * 1.05) / total
        return {k: round(v * scale, 1) for k, v in targets.items()}
    return {k: round(v, 1) for k, v in targets.items()}


def _long_run_notes(
    long_run_km: float,
    paces: Paces,
    race_distance: str,
    phase: int,
    hilliness: str,
) -> str:
    """Build the long run description string."""
    base = f"{long_run_km} km easy @ E pace ({format_pace(paces.easy_min_per_km)})"

    # Ultra athletes — walk-break + nutrition prescription for runs >= 20 km in Phase II+
    if race_distance in ("ultra_56", "ultra_90") and long_run_km >= 20 and phase >= 2:
        from coach_core.engine.workout_templates import get_ultra_long_run_notes
        return get_ultra_long_run_notes(
            long_run_km,
            format_pace(paces.easy_min_per_km),
            race_distance,
            phase,
        )

    if hilliness == "high" and phase in (3, 4):
        return get_hilly_long_run_note(long_run_km, format_pace(paces.marathon_min_per_km))
    if race_distance in ("marathon", "ultra") and phase == 3:
        steady_km = min(16, round(long_run_km * 0.35, 1))
        return (
            f"{long_run_km} km — first {round(long_run_km - steady_km, 1)} km easy, "
            f"last {steady_km} km @ M pace ({format_pace(paces.marathon_min_per_km)})"
        )
    if race_distance in ("half", "marathon") and phase in (3, 4):
        finish_km = 5 if race_distance == "half" else 8
        return (
            f"{long_run_km} km — last {finish_km} km @ goal pace "
            f"({format_pace(paces.marathon_min_per_km)})"
        )
    return base


def _medium_long_session(
    km: float,
    paces: Paces,
    race_distance: str,
    phase: int,
    day: str,
    long_run_day: str,
) -> dict:
    """
    Build a medium-long session dict.
    For ultra athletes in Phase II/III on the day immediately after long_run_day,
    returns back-to-back guidance instead of a plain easy run note.
    """
    # Back-to-back: ultra athletes, Phase II or III, day after long run day
    is_back_to_back = (
        race_distance in ("ultra_56", "ultra_90")
        and phase in (2, 3)
        and day == _day_at(1, long_run_day)
    )
    if is_back_to_back:
        from coach_core.engine.workout_templates import get_ultra_back_to_back_notes
        return {
            "session": "Back-to-Back Run",
            "km": km,
            "notes": get_ultra_back_to_back_notes(
                km, format_pace(paces.easy_min_per_km), race_distance, phase
            ),
        }
    return {"session": "Medium-Long Run", "km": km, "notes": f"{km} km easy"}


def build_week_days(
    week_number: int,
    phase: int,
    weekly_volume: float,
    paces: Paces,
    race_distance: str,
    phases: PhaseAllocation,
    hilliness: str = "low",
    long_run_day: str = "Sat",
    quality_day: str = "Tue",
    extra_training_days: str = "Thu",
) -> dict:
    """
    Build the 7-day session dict for a training week.

    Session placement uses the athlete's explicitly chosen training days:
      long_run_day        → long run
      quality_day         → hard session (strides / reps / intervals / threshold)
      extra_training_days → recovery and medium-long runs, assigned by position
                            (comma-separated string e.g. "Wed,Thu")

    Number of running days = 2 + len(extra_days), clamped 3–5 for
    session distance calculations. Athletes who choose more days than
    recommended are supported but distances are redistributed accordingly.

    Minimum distances enforced:
      Recovery    >= 4.0 km
      Medium-long >= 5.0 km
      Long        >= 7.0 km
    """
    is_race_week = week_number == phases.total_weeks

    # ── Race week (fixed regardless of volume) ─────────────────────────────
    if is_race_week:
        return {
            "Mon": {"session": "Rest",          "km": 0,   "notes": "Full rest — race week"},
            "Tue": {"session": "Easy + Strides", "km": 3.0, "notes": "3 km easy + 4 × 100m strides to sharpen legs"},
            "Wed": {"session": "Recovery",       "km": 4.0, "notes": "4 km very easy, conversational pace"},
            "Thu": {"session": "Easy",           "km": 3.0, "notes": "3 km easy + 2 × 100m strides"},
            "Fri": {"session": "Rest",           "km": 0,   "notes": "Rest or 15 min walk"},
            "Sat": {"session": "🏁 RACE DAY",   "km": 0,   "notes": "Race day — execute your plan!"},
            "Sun": {"session": "Rest",           "km": 0,   "notes": "Recovery — celebrate!"},
        }

    # ── Parse extra training days chosen by athlete ───────────────────────
    extra_days = [d.strip() for d in extra_training_days.split(",")
                  if d.strip() and d.strip() in DAYS_ORDER
                  and d.strip() != long_run_day
                  and d.strip() != quality_day]
    num_days = max(3, min(5, 2 + len(extra_days)))

    # ── Quality session (unchanged across all day counts) ──────────────────
    wip     = week_number_in_phase(week_number, phases)
    quality = get_quality_session(phase, weekly_volume, paces, hilliness, wip)

    # ── 3-DAY WEEK (< 30 km) ──────────────────────────────────────────────
    if num_days == 3:
        # Spec formula: quality = 20% of volume + 3 km WU/CD overhead
        # long = 40% of volume (higher pct compensates for fewer days)
        # Minimums re-enforced after scaling (at very low volume, totals
        # may slightly exceed ceiling — meaningful sessions take priority)
        q_distance  = round(weekly_volume * 0.20 + 3.0, 1)
        long_run_km = max(round(weekly_volume * 0.40, 1), MIN_LONG_KM)
        medium_km   = max(round(weekly_volume * 0.30, 1), MIN_MEDIUM_LONG_KM)

        dist = _scale_sessions(
            {"quality": q_distance, "medium": medium_km, "long": long_run_km},
            weekly_volume,
        )
        # Hard floor — never scale a session below its minimum
        dist["long"]   = round(max(dist["long"],   MIN_LONG_KM),         1)
        dist["medium"] = round(max(dist["medium"], MIN_MEDIUM_LONG_KM),  1)
        long_run_km = dist["long"]

        day_map = _assign_session_days(long_run_day, quality_day, extra_days)
        result = {}
        for d in DAYS_ORDER:
            stype = day_map[d]
            if stype == "quality":
                result[d] = {
                    "session": quality["type"],
                    "km": dist["quality"],
                    "notes": (
                        f"WU {quality['warmup_km']} km easy | "
                        f"{quality['detail']} | "
                        f"CD {quality['cooldown_km']} km easy"
                    ),
                }
            elif stype == "medium_long":
                result[d] = _medium_long_session(
                    dist["medium"], paces, race_distance, phase, d, long_run_day
                )
            elif stype == "long":
                result[d] = {
                    "session": "Long Run",
                    "km": long_run_km,
                    "notes": _long_run_notes(long_run_km, paces, race_distance, phase, hilliness),
                }
            else:
                result[d] = {"session": "Rest", "km": 0, "notes": "Full rest"}
        return result

    # ── 4-DAY WEEK (30–50 km) ─────────────────────────────────────────────
    if num_days == 4:
        long_run_km  = max(get_long_run_km(weekly_volume, race_distance), MIN_LONG_KM)
        medium_km    = max(weekly_volume * 0.25, MIN_MEDIUM_LONG_KM)
        recovery_km  = max(weekly_volume * 0.15, MIN_RECOVERY_KM)
        quality_km   = quality["total_km"]

        dist = _scale_sessions(
            {
                "quality":  quality_km,
                "recovery": recovery_km,
                "medium":   medium_km,
                "long":     long_run_km,
            },
            weekly_volume,
        )
        long_run_km = dist["long"]

        day_map = _assign_session_days(long_run_day, quality_day, extra_days)
        result = {}
        for d in DAYS_ORDER:
            stype = day_map[d]
            if stype == "quality":
                result[d] = {
                    "session": quality["type"],
                    "km": dist["quality"],
                    "notes": (
                        f"WU {quality['warmup_km']} km easy | "
                        f"{quality['detail']} | "
                        f"CD {quality['cooldown_km']} km easy"
                    ),
                }
            elif stype == "recovery":
                result[d] = {
                    "session": "Recovery Run",
                    "km": dist["recovery"],
                    "notes": f"{dist['recovery']} km very easy",
                }
            elif stype == "medium_long":
                result[d] = _medium_long_session(
                    dist["medium"], paces, race_distance, phase, d, long_run_day
                )
            elif stype == "long":
                result[d] = {
                    "session": "Long Run",
                    "km": long_run_km,
                    "notes": _long_run_notes(long_run_km, paces, race_distance, phase, hilliness),
                }
            else:
                result[d] = {"session": "Rest", "km": 0, "notes": "Rest or easy walk"}
        return result

    # ── 5-DAY WEEK (> 50 km) — original full-week logic ───────────────────
    long_run_km  = get_long_run_km(weekly_volume, race_distance)
    medium_km    = max(round(weekly_volume * 0.18, 1), MIN_MEDIUM_LONG_KM)
    recovery_km  = max(round(weekly_volume * 0.09, 1), MIN_RECOVERY_KM)
    quality_km   = quality["total_km"]

    dist = _scale_sessions(
        {
            "quality":   quality_km,
            "recovery1": recovery_km,
            "medium":    medium_km,
            "recovery2": recovery_km,
            "long":      long_run_km,
        },
        weekly_volume,
    )
    long_run_km = dist["long"]

    day_map = _assign_session_days(long_run_day, quality_day, extra_days)
    # track which recovery slot we're on (r1, r2)
    recovery_slots = ["recovery1", "recovery2"]
    recovery_cursor = 0
    days = {}
    for d in DAYS_ORDER:
        stype = day_map[d]
        if stype == "quality":
            days[d] = {
                "session": quality["type"],
                "km": dist["quality"],
                "notes": (
                    f"WU {quality['warmup_km']} km easy | "
                    f"{quality['detail']} | "
                    f"CD {quality['cooldown_km']} km easy"
                ),
            }
        elif stype == "recovery":
            rkey = recovery_slots[min(recovery_cursor, 1)]
            recovery_cursor += 1
            days[d] = {
                "session": "Recovery Run",
                "km": dist[rkey],
                "notes": f"{dist[rkey]} km very easy",
            }
        elif stype == "medium_long":
            days[d] = {
                "session": "Medium-Long Run",
                "km": dist["medium"],
                "notes": f"{dist['medium']} km easy",
            }
        elif stype == "long":
            days[d] = {
                "session": "Long Run",
                "km": long_run_km,
                "notes": _long_run_notes(long_run_km, paces, race_distance, phase, hilliness),
            }
        else:
            days[d] = {
                "session": "Rest",
                "km": 0,
                "notes": "Full rest",
            }

    # Ultra: back-to-back Saturday/Sunday long runs (5-day only, Phases II/III)
    if race_distance in ("ultra", "ultra_56", "ultra_90") and phase in (2, 3):
        from coach_core.engine.workout_templates import (
            get_ultra_long_run_notes, get_ultra_back_to_back_notes
        )
        ULTRA_CAP = 40.0
        sun_km = min(round(weekly_volume * 0.20, 1), ULTRA_CAP)
        sat_km = days["Sat"]["km"]
        if sat_km > ULTRA_CAP:
            sat_km = ULTRA_CAP
        # Update Saturday with walk-break + nutrition notes (if ultra_56/90)
        if race_distance in ("ultra_56", "ultra_90") and sat_km >= 20:
            days["Sat"] = {
                "session": "Long Run",
                "km": sat_km,
                "notes": get_ultra_long_run_notes(
                    sat_km, format_pace(paces.easy_min_per_km), race_distance, phase
                ),
            }
        elif sat_km != days["Sat"]["km"]:
            days["Sat"] = {
                "session": "Long Run",
                "km": sat_km,
                "notes": f"{sat_km} km easy @ E pace (capped at {ULTRA_CAP} km)",
            }
        # Sunday back-to-back with full ultra guidance
        days["Sun"] = {
            "session": "Back-to-Back Run",
            "km": sun_km,
            "notes": get_ultra_back_to_back_notes(
                sun_km, format_pace(paces.easy_min_per_km), race_distance, phase
            ) if race_distance in ("ultra_56", "ultra_90") else (
                f"{sun_km} km easy @ E pace — "
                "back-to-back with Saturday to build ultra fatigue resistance"
            ),
        }

    # High hilliness: downhill repeats on alternating Fridays (Phases III/IV)
    if hilliness == "high" and phase in (3, 4):
        if wip % 2 == 0:
            dl = get_downhill_session(weekly_volume, is_taper=(phase == 4))
            days["Fri"] = dl

    return days
