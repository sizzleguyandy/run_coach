"""
Daniels workout templates — variety rotation engine.

Maps weekly mileage to the correct Daniels mileage category (A–G),
then rotates through the available sessions for that category using
week_in_phase as the rotation index. This gives athletes a different
workout every week while staying in the correct prescribed volume band.

Phase → Template type:
  Phase 1 (Base):         Repetition  — strides and short R-pace reps (economy)
  Phase 2 (Early Qual):   Repetition  — full R-pace menu
  Phase 3 (Peak Qual):    Interval    — I-pace hard efforts
  Phase 4 (Race Prep):    Threshold   — T-pace cruise intervals

Session descriptions use placeholders:
  {T}  → threshold pace string   e.g. "5:12 /km"
  {I}  → interval pace string    e.g. "4:47 /km"
  {R}  → repetition pace string  e.g. "4:33 /km"
  {M}  → marathon pace string    e.g. "5:33 /km"
"""
from __future__ import annotations
from typing import Optional


# ── Threshold templates (Phase IV) ────────────────────────────────────────
# Category by weekly km. Total T-time increases with fitness/volume.

THRESHOLD_CATEGORIES: list[dict] = [
    {
        "label": "A",
        "max_km": 64,
        "sessions": [
            "20 min continuous @ T pace ({T})",
            "4 × 5 min @ T pace ({T}) — 1 min rest between",
            "4 × 6 min @ T pace ({T}) — 1 min rest between",
        ],
        "wu_cd_km": 3.0,
        "approx_quality_km_factor": 0.175,  # ~T time × T pace
    },
    {
        "label": "B",
        "max_km": 113,
        "sessions": [
            "6 × 5 min @ T pace ({T}) — 1 min rest",
            "2 × 12 min @ T pace ({T}) — 2 min rest — then 2 × 5 min @ T — 1 min rest",
            "3 × 12 min @ T pace ({T}) — 2 min rest",
            "2 × 18 min @ T pace ({T}) — 3 min rest",
            "20 min @ T ({T}) — 3 min rest — 12 min @ T — 2 min rest — 5 min @ T",
        ],
        "wu_cd_km": 3.0,
        "approx_quality_km_factor": 0.20,
    },
    {
        "label": "C",
        "max_km": 137,
        "sessions": [
            "8 × 5 min @ T pace ({T}) — 1 min rest",
            "5 × 8 min @ T pace ({T}) — 1 min rest",
            "4 × 12 min @ T pace ({T}) — 2 min rest",
            "20 min @ T ({T}) — 3 min rest — 2 × 12 min @ T — 2 min rest — 5 min @ T",
        ],
        "wu_cd_km": 3.0,
        "approx_quality_km_factor": 0.21,
    },
    {
        "label": "D",
        "max_km": 160,
        "sessions": [
            "10 × 5 min @ T pace ({T}) — 1 min rest",
            "5 × 12 min @ T pace ({T}) — 2 min rest",
            "2 × 15 min @ T ({T}) — 3 min rest — 2 × 12 min @ T — 2 min rest",
            "20 min @ T ({T}) — 3 min — 15 min @ T — 2 min — 10 min @ T — 1 min — 5 min @ T",
        ],
        "wu_cd_km": 3.0,
        "approx_quality_km_factor": 0.22,
    },
    {
        "label": "E",
        "max_km": 194,
        "sessions": [
            "6 × 12 min @ T pace ({T}) — 2 min rest",
            "4 × 15 min @ T pace ({T}) — 3 min rest",
            "2 × 15 min @ T ({T}) — 3 min — 2 × 12 min @ T — 2 min — 2 × 5 min @ T — 1 min",
        ],
        "wu_cd_km": 3.0,
        "approx_quality_km_factor": 0.23,
    },
    {
        "label": "F",
        "max_km": float("inf"),
        "sessions": [
            "25 min @ T ({T}) — 5 min rest — 20 min @ T — 4 min rest — 15 min @ T",
            "20 min @ T ({T}) — 4 min rest — 15 min @ T — 3 min rest — 10 min @ T",
            "15 min @ T ({T}) — 3 min rest — 10 min @ T — 2 min rest — 5 min @ T (×2)",
        ],
        "wu_cd_km": 3.0,
        "approx_quality_km_factor": 0.23,
    },
]


# ── Interval templates (Phase III) ────────────────────────────────────────

INTERVAL_CATEGORIES: list[dict] = [
    {
        "label": "A",
        "max_km": 48,
        "sessions": [
            "5 × 2 min hard @ I pace ({I}) — 1 min jog recovery",
            "4 × 3 min hard @ I pace ({I}) — 2 min jog",
            "3 × 4 min hard @ I pace ({I}) — 3 min jog",
            "4 × 800m @ I pace ({I}) — 2 min jog",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "B",
        "max_km": 64,
        "sessions": [
            "7 × 2 min hard @ I pace ({I}) — 1 min jog",
            "5 × 3 min hard @ I pace ({I}) — 2 min jog",
            "4 × 4 min hard @ I pace ({I}) — 3 min jog",
            "5 × 800m @ I pace ({I}) — 2 min jog",
            "4 × 1000m @ I pace ({I}) — 3 min jog",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "C",
        "max_km": 72,
        "sessions": [
            "6 × 800m @ I pace ({I}) — 2 min jog",
            "6 × 3 min hard @ I pace ({I}) — 2 min jog",
            "5 × 1000m @ I pace ({I}) — 3 min jog",
            "4 × 1200m @ I pace ({I}) — 3 min jog",
            "3 × 5 min hard @ I pace ({I}) — 4 min jog",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "D",
        "max_km": 88,
        "sessions": [
            "5 × 1000m @ I pace ({I}) — 3 min jog",
            "5 × 1200m @ I pace ({I}) — 3 min jog",
            "4 × 1 mile @ I pace ({I}) — 4 min jog",
            "5 × 4 min hard @ I pace ({I}) — 3 min jog",
            "7 × 3 min hard @ I pace ({I}) — 2 min jog",
            "10 × 2 min hard @ I pace ({I}) — 1 min jog",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "E",
        "max_km": 113,
        "sessions": [
            "6 × 1000m @ I pace ({I}) — 3 min jog (max 25 min work)",
            "5 × 1200m @ I pace ({I}) — 3 min jog (max 25 min work)",
            "5 × 1 mile @ I pace ({I}) — 4 min jog",
            "4 × 3 min hard / 2 × 2 min hard @ I pace ({I}) — short jog recovery",
            "8 × 2 min hard @ I pace ({I}) — 1 min jog",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "F",
        "max_km": float("inf"),
        "sessions": [
            "7 × 1000m @ I pace ({I}) — 3 min jog",
            "6 × 1200m @ I pace ({I}) — 3 min jog",
            "5 × 1 mile @ I pace ({I}) — 4 min jog",
            "3 × 5 min / 4 × 1000m @ I pace ({I}) — mixed recovery",
            "6 × 4 min hard @ I pace ({I}) — 3 min jog",
        ],
        "wu_cd_km": 3.0,
    },
]


# ── Repetition templates (Phase I + II) ───────────────────────────────────

REPETITION_CATEGORIES: list[dict] = [
    {
        "label": "A",
        "max_km": 48,
        "sessions": [
            "8 × 200m @ R pace ({R}) — 200m jog recovery",
            "4 × 400m @ R pace ({R}) — 400m jog recovery",
            "2 × (200m R + 400m R) @ R pace ({R}) — equal jog recovery",
            "4 × 300m @ R pace ({R}) — 300m jog — then 1 × 400m @ R",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "B",
        "max_km": 64,
        "sessions": [
            "2 sets of 6 × 200m @ R pace ({R}) — 200m jog, 400m jog between sets",
            "6 × 400m @ R pace ({R}) — 400m jog recovery",
            "4 × 200m R + 2 × 400m R + 4 × 200m R @ R pace ({R})",
            "2 × 200m R + 2 × 600m R + 2 × 400m R @ R pace ({R})",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "C",
        "max_km": 80,
        "sessions": [
            "2 sets of 8 × 200m @ R pace ({R}) — 200m jog, 800m jog between sets",
            "8 × 400m @ R pace ({R}) — 400m jog recovery",
            "4 × 200m R + 4 × 400m R + 4 × 200m R @ R pace ({R})",
            "4 × 400m R + 8 × 200m R @ R pace ({R})",
            "2 × 200m R + 2 × 600m R + 4 × 400m R @ R pace ({R})",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "D",
        "max_km": 96,
        "sessions": [
            "2 sets of 10 × 200m @ R pace ({R}) — 200m jog, 800m jog between sets",
            "10 × 400m @ R pace ({R}) — 400m jog",
            "6 × 200m R + 6 × 400m R + 2 × 200m R @ R pace ({R})",
            "6 × 400m R + 8 × 200m R @ R pace ({R})",
            "2 × 200m R + 4 × 600m R + 3 × 400m R @ R pace ({R})",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "E",
        "max_km": 120,
        "sessions": [
            "4 × 200m R + 8 × 400m R + 4 × 200m R @ R pace ({R})",
            "8 × 400m R + 8 × 200m R @ R pace ({R})",
            "4 × 600m R + 4 × 400m R + 4 × 200m R @ R pace ({R})",
            "3 × 600m R + 3 × 800m R + 3 × 200m R @ R pace ({R})",
            "2 × 800m R + 4 × 400m R + 8 × 200m R @ R pace ({R})",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "F",
        "max_km": 129,
        "sessions": [
            "4 × 200m R + 4 × 400m R + 4 × 800m R + 4 × 200m R @ R pace ({R})",
            "2 × 200m R + 3 × 800m R + 4 × 600m R + 2 × 400m R @ R pace ({R})",
            "2 × 800m R + 3 × 600m R + 4 × 400m R + 5 × 200m R @ R pace ({R})",
            "4 sets of 4 × 400m @ R pace ({R}) — 400m jog, 800m jog between sets",
        ],
        "wu_cd_km": 3.0,
    },
    {
        "label": "G",
        "max_km": float("inf"),
        "sessions": [
            "5 sets of 8 × 200m @ R pace ({R}) — 200m jog, 400m jog between sets",
            "20 × 400m @ R pace ({R}) — 400m jog recovery",
            "16 × 400m R + 8 × 200m R @ R pace ({R})",
            "3 sets: 5 × 200m R + 2 × 400m R + 1 × 800m R — 5 min between sets",
        ],
        "wu_cd_km": 3.0,
    },
]


# ── Phase 1 strides (Base phase — kept simpler, foot-speed focus) ──────────

STRIDE_SESSIONS: list[str] = [
    "8 × 100m strides @ R pace ({R}) — full walk recovery",
    "10 × 100m strides @ R pace ({R}) — walk back recovery",
    "6 × 150m accelerations to R pace ({R}) — walk recovery",
    "4 × 200m controlled @ R pace ({R}) — 200m jog — then 6 × 100m strides",
    "12 × 100m strides @ R pace ({R}) — alternate with walk back",
    "2 × (6 × 100m strides @ R pace ({R})) — 2 min rest between sets",
]


# ── Selector functions ─────────────────────────────────────────────────────

def _select_category(categories: list[dict], weekly_volume_km: float) -> dict:
    """Return the appropriate category for the given weekly volume."""
    for cat in categories:
        if weekly_volume_km <= cat["max_km"]:
            return cat
    return categories[-1]


def _format_session(session: str, paces_dict: dict) -> str:
    """Substitute pace placeholders with actual formatted values."""
    return (session
            .replace("{T}", paces_dict.get("T", "—"))
            .replace("{I}", paces_dict.get("I", "—"))
            .replace("{R}", paces_dict.get("R", "—"))
            .replace("{M}", paces_dict.get("M", "—")))


def get_template_session(
    phase: int,
    weekly_volume_km: float,
    paces,                    # Paces dataclass from paces.py
    week_in_phase: int = 1,
) -> dict:
    """
    Select the appropriate Daniels template session for the given phase,
    weekly volume, and week-in-phase (for rotation).

    Returns a dict matching the existing quality session format:
      { type, detail, warmup_km, cooldown_km, quality_km, total_km }
    """
    from coach_core.engine.paces import format_pace

    pace_strs = {
        "T": format_pace(paces.threshold_min_per_km),
        "I": format_pace(paces.interval_min_per_km),
        "R": format_pace(paces.repetition_min_per_km),
        "M": format_pace(paces.marathon_min_per_km),
    }

    quality_km = weekly_volume_km * 0.20

    if phase == 1:
        # Base phase — strides, rotating through the stride sessions
        idx = (week_in_phase - 1) % len(STRIDE_SESSIONS)
        session_text = _format_session(STRIDE_SESSIONS[idx], pace_strs)
        return {
            "type": "Strides",
            "detail": session_text,
            "warmup_km": 2.0,
            "cooldown_km": 2.0,
            "quality_km": round(min(quality_km, 2.0), 1),
            "total_km": round(min(quality_km, 2.0) + 4.0, 1),
        }

    if phase == 2:
        cat = _select_category(REPETITION_CATEGORIES, weekly_volume_km)
        idx = (week_in_phase - 1) % len(cat["sessions"])
        session_text = _format_session(cat["sessions"][idx], pace_strs)
        wu_cd = cat["wu_cd_km"]
        return {
            "type": f"Repetitions (Cat {cat['label']})",
            "detail": session_text,
            "warmup_km": 2.0,
            "cooldown_km": 1.0,
            "quality_km": round(quality_km, 1),
            "total_km": round(quality_km + wu_cd, 1),
        }

    if phase == 3:
        cat = _select_category(INTERVAL_CATEGORIES, weekly_volume_km)
        idx = (week_in_phase - 1) % len(cat["sessions"])
        session_text = _format_session(cat["sessions"][idx], pace_strs)
        wu_cd = cat["wu_cd_km"]
        return {
            "type": f"Intervals (Cat {cat['label']})",
            "detail": session_text,
            "warmup_km": 2.0,
            "cooldown_km": 1.0,
            "quality_km": round(quality_km, 1),
            "total_km": round(quality_km + wu_cd, 1),
        }

    # Phase 4 — Threshold
    cat = _select_category(THRESHOLD_CATEGORIES, weekly_volume_km)
    idx = (week_in_phase - 1) % len(cat["sessions"])
    session_text = _format_session(cat["sessions"][idx], pace_strs)
    wu_cd = cat["wu_cd_km"]
    return {
        "type": f"Threshold (Cat {cat['label']})",
        "detail": session_text,
        "warmup_km": 2.0,
        "cooldown_km": 1.0,
        "quality_km": round(quality_km, 1),
        "total_km": round(quality_km + wu_cd, 1),
    }


# ── Ultra-specific session templates ──────────────────────────────────────

def get_ultra_long_run_notes(
    long_run_km: float,
    easy_pace_str: str,
    race_distance: str,
    phase: int,
) -> str:
    """
    Walk-break + nutrition prescription for ultra athletes on long runs >= 20 km.

    Two Oceans (ultra_56): run 20 min / walk 1 min
    Comrades (ultra_90):   run 10 min / walk 1 min — race day discipline starts now

    Nutrition: gel/chew every 45 min from 60 min in — practice race day fuelling.
    Phase-specific effort note appended.
    """
    if race_distance == "ultra_90":
        walk_cue = "run 10 min / walk 1 min from the start"
        race_tip = "Comrades walk-break discipline — build the habit now, your legs will thank you at 70km"
    else:
        walk_cue = "run 20 min / walk 1 min throughout"
        race_tip = "Two Oceans walk break — introduce it now, not on race day"

    if phase == 2:
        effort = "easy effort throughout — genuinely conversational, no heroics"
    elif phase == 3:
        effort = "first 70% easy, last 20% at M pace if legs feel good — stop if not"
    else:
        effort = "easy throughout — protecting legs for race week"

    return (
        f"{long_run_km} km @ {easy_pace_str} | "
        f"{walk_cue} | "
        f"Fuel every 45 min from 60 min — practice race day nutrition (gel/chew + water) | "
        f"{effort} | "
        f"{race_tip}"
    )


def get_ultra_back_to_back_notes(
    km: float,
    easy_pace_str: str,
    race_distance: str,
    phase: int,
) -> str:
    """
    Notes for the second day of a back-to-back long run weekend (day after long run day).
    The pre-fatigue stimulus is the entire point — this is a core ultra training block.
    """
    if race_distance == "ultra_90":
        context = "Comrades back-to-back — the most race-specific session of the week"
        walk_cue = "run 10 min / walk 1 min — same walk-break discipline as yesterday"
    else:
        context = "Two Oceans back-to-back — running on yesterday's legs"
        walk_cue = "run 20 min / walk 1 min"

    if phase == 2:
        effort = "easy — the point is cumulative fatigue, not pace"
    else:
        effort = "easy to moderate — stop early if legs are cooked"

    return (
        f"{km} km easy @ {easy_pace_str} | "
        f"Pre-fatigued legs from yesterday — this is the point | "
        f"{walk_cue} | "
        f"{effort} | "
        f"Pace is irrelevant — arrival is the goal | "
        f"{context}"
    )
