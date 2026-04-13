"""
Race Knowledge RAG Engine.

Loads race-specific knowledge documents and generates personalised
checkpoint/split guidance based on the athlete's VDOT.

Usage:
    from coach_core.engine.race_knowledge import get_race_context

    context = get_race_context(
        preset_race_id="comrades",
        vdot=39.0,
        race_date_str="2026-06-14",
    )
    # Returns a dict with:
    #   "knowledge_text"      — the full race knowledge markdown
    #   "checkpoint_summary"  — personalised split/checkpoint guidance (str)
    #   "race_display_name"   — human-readable race name
"""
from __future__ import annotations

import math
import os
from datetime import date
from pathlib import Path
from typing import Optional

# ── Path to knowledge files ───────────────────────────────────────────────────

_KNOWLEDGE_DIR = Path(__file__).parent / "race_knowledge"


# ── Mapping: preset_race_id → filename stem ───────────────────────────────────

RACE_FILE_MAP: dict[str, str] = {
    "comrades":             "comrades",
    "two_oceans":           "two_oceans",
    "capetown_marathon":    "capetown_marathon",
    "soweto_marathon":      "soweto_marathon",
    "om_die_dam":           "om_die_dam",
    "durban_city_marathon": "durban_city_marathon",
    "parkrun":              "parkrun",
}

RACE_DISPLAY_NAMES: dict[str, str] = {
    "comrades":             "Comrades Marathon",
    "two_oceans":           "Two Oceans Ultra Marathon",
    "capetown_marathon":    "Cape Town Marathon",
    "soweto_marathon":      "Soweto Marathon",
    "om_die_dam":           "Om Die Dam Ultra Marathon",
    "durban_city_marathon": "Durban City Marathon",
    "parkrun":              "parkrun (5K)",
}


# ── Knowledge file loader ─────────────────────────────────────────────────────

def load_race_knowledge(preset_race_id: str) -> Optional[str]:
    """
    Return the full markdown text for a race knowledge file.
    Returns None if no file exists for this race ID.
    """
    stem = RACE_FILE_MAP.get(preset_race_id)
    if not stem:
        return None
    path = _KNOWLEDGE_DIR / f"{stem}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


# ── VDOT → race time helpers ──────────────────────────────────────────────────

_A = 0.000104
_B = 0.182258
_C_OFFSET = 4.60
_PCT_M = 0.810


def _vdot_to_marathon_minutes(vdot: float) -> float:
    v = (-_B + math.sqrt(_B * _B + 4.0 * _A * (vdot * _PCT_M + _C_OFFSET))) / (2.0 * _A)
    return 42195.0 / v


def _comrades_is_down(race_date_str: Optional[str]) -> bool:
    """Return True if the given race year is a Down Run year."""
    _DOWN_YEARS = {2026, 2028, 2030, 2032}
    if race_date_str:
        try:
            yr = date.fromisoformat(race_date_str).year
            return yr in _DOWN_YEARS
        except Exception:
            pass
    return date.today().year in _DOWN_YEARS


def _fmt_time(minutes: float) -> str:
    """Format minutes as H:MM:SS."""
    total_secs = int(round(minutes * 60))
    h = total_secs // 3600
    m = (total_secs % 3600) // 60
    s = total_secs % 60
    return f"{h}:{m:02d}:{s:02d}"


def _fmt_hm(minutes: float) -> str:
    """Format minutes as Xh YYm."""
    h = int(minutes // 60)
    m = int(round(minutes % 60))
    if m == 60:
        h += 1; m = 0
    return f"{h}h {m:02d}m"


# ── Comrades checkpoint calculator ───────────────────────────────────────────

# Up Run checkpoints: name, distance_km, fraction_of_total_effort
# Effort fraction accounts for hills — not proportional to distance.
_COMRADES_UP_CHECKPOINTS = [
    ("Fields Hill (~17 km)",     17.0,  0.175),
    ("Botha's Hill (~42 km)",    42.0,  0.435),
    ("Drummond (~45 km)",        45.0,  0.465),
    ("Inchanga (~62 km)",        62.0,  0.655),
    ("Camperdown (~74 km)",      74.0,  0.790),
    ("Polly Shortts (~80 km)",   80.0,  0.865),
    ("Finish (PMB)",             90.0,  1.000),
]

_COMRADES_DOWN_CHECKPOINTS = [
    ("Polly Shortts (~10 km)",   10.0,  0.100),
    ("Camperdown (~16 km)",      16.0,  0.165),
    ("Inchanga (~28 km)",        28.0,  0.295),
    ("Drummond (~45 km)",        45.0,  0.475),
    ("Harrison Flats (~48 km)",  48.0,  0.510),
    ("45th Cutting (~67 km)",    67.0,  0.715),
    ("Pinetown (~75 km)",        75.0,  0.805),
    ("Finish (Durban)",          90.0,  1.000),
]

# Official approximate cut-offs from gun in minutes
_COMRADES_UP_CUTOFFS: dict[str, int] = {
    "Halfway (~45 km)":          375,   # 6:15
    "Camperdown (~74 km)":       560,   # 9:20
    "Polly Shortts (~80 km)":    615,   # 10:15
    "Finish (PMB)":              720,   # 12:00
}

_COMRADES_DOWN_CUTOFFS: dict[str, int] = {
    "Halfway (~45 km)":          375,   # 6:15
    "Camperdown (~16 km)":       None,  # no early cut-off listed
    "Pinetown (~75 km)":         600,   # ~10:00
    "Finish (Durban)":           720,   # 12:00
}

_COMRADES_MEDALS = [
    ("Wally Hayward", 360),
    ("Gold",          450),
    ("Bill Rowan",    540),
    ("Silver",        600),
    ("Bronze",        660),
    ("Vic Clapham",   720),
]


def _comrades_medal(minutes: float) -> str:
    for name, cutoff in _COMRADES_MEDALS:
        if minutes < cutoff:
            return name
    return "Finisher"


def _comrades_checkpoints(vdot: float, race_date_str: Optional[str]) -> str:
    """
    Return personalised Comrades checkpoint splits for the given VDOT.
    Uses effort-fraction model with a 5% buffer below cut-offs.
    """
    is_down = _comrades_is_down(race_date_str)
    marathon_min = _vdot_to_marathon_minutes(vdot)
    direction_factor = 1.0 if is_down else 1.08
    ratio = 90.0 / 42.2
    predicted_min = marathon_min * (ratio ** 1.12) * direction_factor

    direction = "Down Run ↓ (PMB → Durban)" if is_down else "Up Run ↑ (Durban → PMB)"
    medal = _comrades_medal(predicted_min)
    cutoffs = _COMRADES_DOWN_CUTOFFS if is_down else _COMRADES_UP_CUTOFFS
    checkpoints = _COMRADES_DOWN_CHECKPOINTS if is_down else _COMRADES_UP_CHECKPOINTS

    lines = [
        f"**Comrades Personalised Checkpoint Guide ({direction})**",
        f"Predicted finish: **{_fmt_hm(predicted_min)}** → Target medal: **{medal}**",
        "",
        "| Checkpoint | Your Target Time | Official Cut-Off |",
        "|---|---|---|",
    ]

    for name, _dist, effort_frac in checkpoints:
        athlete_time = predicted_min * effort_frac
        cutoff_min = None
        # Try to match a cut-off by rough checkpoint name
        for co_name, co_val in cutoffs.items():
            if any(kw in name for kw in co_name.split() if len(kw) > 4):
                cutoff_min = co_val
                break
        cutoff_str = _fmt_hm(cutoff_min) if cutoff_min else "—"
        lines.append(f"| {name} | {_fmt_hm(athlete_time)} | {cutoff_str} |")

    # Safety note: flag if predicted time is within 60 min of cut-off
    buffer = 720 - predicted_min  # minutes before the 12h cut-off
    if buffer < 90:
        lines.append("")
        lines.append(
            f"⚠️ **Cut-off warning:** Your predicted time ({_fmt_hm(predicted_min)}) "
            f"leaves only {int(buffer)} minutes of buffer to the 12-hour cut-off. "
            "Focus on conservative early pacing and never skip an aid station."
        )
    elif buffer < 150:
        lines.append("")
        lines.append(
            f"ℹ️ You have approximately {int(buffer)} minutes of buffer to the 12-hour cut-off. "
            "This is manageable but leaves no room for a major blow-up or lengthy stop."
        )

    return "\n".join(lines)


# ── Two Oceans checkpoint calculator ─────────────────────────────────────────

_TWO_OCEANS_CHECKPOINTS = [
    ("Muizenberg (~21 km)",       21.0, 0.355),
    ("Chapman's Peak (~32 km)",   32.0, 0.565),
    ("Constantia Nek (~46 km)",   46.0, 0.840),
    ("Finish",                    56.0, 1.000),
]

_TWO_OCEANS_CUTOFFS = {
    "Muizenberg (~21 km)":      150,  # ~2:30
    "Chapman's Peak (~32 km)":  225,  # ~3:45
    "Constantia Nek (~46 km)":  330,  # ~5:30
    "Finish":                   420,  # 7:00
}

_TWO_OCEANS_MEDALS = [
    ("Gold",    240),
    ("Silver",  300),
    ("Bronze",  360),
]


def _two_oceans_medal(minutes: float) -> str:
    for name, cutoff in _TWO_OCEANS_MEDALS:
        if minutes < cutoff:
            return name
    return "Finisher"


def _two_oceans_checkpoints(vdot: float) -> str:
    """Return personalised Two Oceans checkpoint splits."""
    marathon_min = _vdot_to_marathon_minutes(vdot)
    ratio = 56.0 / 42.2
    predicted_min = marathon_min * (ratio ** 1.10)

    medal = _two_oceans_medal(predicted_min)

    lines = [
        "**Two Oceans Personalised Checkpoint Guide**",
        f"Predicted finish: **{_fmt_hm(predicted_min)}** → Target medal: **{medal}**",
        "",
        "| Checkpoint | Your Target Time | Official Cut-Off |",
        "|---|---|---|",
    ]

    for name, _dist, effort_frac in _TWO_OCEANS_CHECKPOINTS:
        athlete_time = predicted_min * effort_frac
        cutoff_min = _TWO_OCEANS_CUTOFFS.get(name)
        cutoff_str = _fmt_hm(cutoff_min) if cutoff_min else "—"
        lines.append(f"| {name} | {_fmt_hm(athlete_time)} | {cutoff_str} |")

    buffer = 420 - predicted_min
    if buffer < 60:
        lines.append("")
        lines.append(
            f"⚠️ **Cut-off warning:** Your predicted time ({_fmt_hm(predicted_min)}) "
            f"leaves only {int(buffer)} minutes of buffer to the 7-hour cut-off. "
            "Walk Chapman's Peak without hesitation and be very conservative on the flats."
        )

    return "\n".join(lines)


# ── Cape Town Marathon pacing ─────────────────────────────────────────────────

def _capetown_pacing(vdot: float) -> str:
    marathon_min = _vdot_to_marathon_minutes(vdot)
    marathon_pace_per_km = marathon_min / 42.195  # min/km
    easy_pace = marathon_pace_per_km * (1 / 0.81) * 0.70  # easy zone

    def fmt_pace(min_per_km: float) -> str:
        m = int(min_per_km)
        s = int(round((min_per_km - m) * 60))
        return f"{m}:{s:02d}/km"

    lines = [
        "**Cape Town Marathon Personalised Pacing**",
        f"Predicted finish: **{_fmt_hm(marathon_min)}**",
        f"Goal marathon pace: **{fmt_pace(marathon_pace_per_km)}**",
        "",
        "| Segment | Target Pace | Notes |",
        "|---|---|---|",
        f"| V&A Waterfront (0–5 km) | {fmt_pace(marathon_pace_per_km * 1.02)} | Slightly conservative — atmosphere is electric |",
        f"| Sea Point Promenade (5–12 km) | {fmt_pace(marathon_pace_per_km)} | Goal pace. Watch for SE wind. |",
        f"| Rolling Middle (12–30 km) | {fmt_pace(marathon_pace_per_km * 1.01)} | Effort-based on the hills |",
        f"| Final 12 km (30–42 km) | {fmt_pace(marathon_pace_per_km * 0.98)} | If paced correctly, this is where you gain time |",
    ]
    return "\n".join(lines)


# ── Soweto altitude adjustment ────────────────────────────────────────────────

def _soweto_pacing(vdot: float) -> str:
    marathon_min = _vdot_to_marathon_minutes(vdot)
    # Soweto altitude adjustment: ~4% for sea-level runners
    adjusted_min = marathon_min * 1.04
    pace_per_km = adjusted_min / 42.195

    def fmt_pace(min_per_km: float) -> str:
        m = int(min_per_km)
        s = int(round((min_per_km - m) * 60))
        return f"{m}:{s:02d}/km"

    lines = [
        "**Soweto Marathon Personalised Pacing**",
        f"Sea-level predicted finish: **{_fmt_hm(marathon_min)}**",
        f"Altitude-adjusted target: **{_fmt_hm(adjusted_min)}** (Johannesburg at 1,750m)",
        f"Goal pace at altitude: **{fmt_pace(pace_per_km)}**",
        "",
        "Note: If you train in Johannesburg, use your sea-level predicted time directly. "
        "The 4% adjustment applies to sea-level athletes arriving within a week.",
    ]
    return "\n".join(lines)


# ── Om Die Dam pacing ─────────────────────────────────────────────────────────

def _om_die_dam_pacing(vdot: float) -> str:
    marathon_min = _vdot_to_marathon_minutes(vdot)
    ratio = 50.0 / 42.2
    predicted_min = marathon_min * (ratio ** 1.08)  # moderate ultra exponent

    lines = [
        "**Om Die Dam Personalised Target**",
        f"Predicted finish: **{_fmt_hm(predicted_min)}**",
        f"Target starting pace: **{_fmt_hm(predicted_min / 50 * 5)}/km** "
        "(aim for the first 25 km — faster is a mistake)",
        "",
        "Run/walk strategy: Consider 8 min run / 1 min walk from the start. "
        "This feels conservative at first but pays off hugely after 35 km.",
    ]
    return "\n".join(lines)


# ── Main public API ───────────────────────────────────────────────────────────

def get_race_context(
    preset_race_id: Optional[str],
    vdot: Optional[float],
    race_date_str: Optional[str] = None,
) -> dict:
    """
    Return a dict with:
      - knowledge_text: full race markdown (empty string if no file)
      - checkpoint_summary: personalised split guidance (empty string if no VDOT)
      - race_display_name: human-readable race name
    """
    display_name = RACE_DISPLAY_NAMES.get(preset_race_id or "", "your race")
    knowledge_text = load_race_knowledge(preset_race_id or "") or ""
    checkpoint_summary = ""

    if vdot and vdot > 0 and preset_race_id:
        try:
            if preset_race_id == "comrades":
                checkpoint_summary = _comrades_checkpoints(vdot, race_date_str)
            elif preset_race_id == "two_oceans":
                checkpoint_summary = _two_oceans_checkpoints(vdot)
            elif preset_race_id == "capetown_marathon":
                checkpoint_summary = _capetown_pacing(vdot)
            elif preset_race_id == "soweto_marathon":
                checkpoint_summary = _soweto_pacing(vdot)
            elif preset_race_id == "om_die_dam":
                checkpoint_summary = _om_die_dam_pacing(vdot)
        except Exception:
            checkpoint_summary = ""

    return {
        "knowledge_text":      knowledge_text,
        "checkpoint_summary":  checkpoint_summary,
        "race_display_name":   display_name,
    }
