"""
Race Knowledge RAG Engine.

Loads race-specific knowledge documents and generates personalised
checkpoint/split guidance based on the athlete's VO2X.

Usage:
    from coach_core.engine.race_knowledge import get_race_context

    context = get_race_context(
        preset_race_id="comrades",
        vo2x=39.0,
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


# ── Mapping: preset_race_id → (country_subdir, filename_stem) ────────────────

RACE_FILE_MAP: dict[str, tuple[str, str]] = {
    # SA races
    "comrades_marathon":             ("sa", "comrades_marathon"),
    "two_oceans_marathon":           ("sa", "two_oceans_marathon"),
    "cape_town_marathon":            ("sa", "cape_town_marathon"),
    "soweto_marathon":               ("sa", "soweto_marathon"),
    "durban_international_marathon": ("sa", "durban_international_marathon"),
    "knysna_forest_marathon":        ("sa", "knysna_forest_marathon"),
    # UK races
    "london_marathon":               ("uk", "london_marathon"),
    "manchester_marathon":           ("uk", "manchester_marathon"),
    "brighton_marathon":             ("uk", "brighton_marathon"),
    "edinburgh_marathon":            ("uk", "edinburgh_marathon"),
    "yorkshire_marathon":            ("uk", "yorkshire_marathon"),
    "loch_ness_marathon":            ("uk", "loch_ness_marathon"),
}

RACE_DISPLAY_NAMES: dict[str, str] = {
    # SA races
    "comrades_marathon":             "Comrades Marathon",
    "two_oceans_marathon":           "Two Oceans Marathon",
    "cape_town_marathon":            "Sanlam Cape Town Marathon",
    "soweto_marathon":               "African Bank Soweto Marathon",
    "durban_international_marathon": "Durban International Marathon",
    "knysna_forest_marathon":        "Knysna Forest Marathon",
    # UK races
    "london_marathon":               "TCS London Marathon",
    "manchester_marathon":           "adidas Manchester Marathon",
    "brighton_marathon":             "Brighton Marathon",
    "edinburgh_marathon":            "Edinburgh Marathon",
    "yorkshire_marathon":            "Yorkshire Marathon",
    "loch_ness_marathon":            "Baxters Loch Ness Marathon",
}


# ── Knowledge file loader ─────────────────────────────────────────────────────

def load_race_knowledge(preset_race_id: str) -> Optional[str]:
    """
    Return the full markdown text for a race knowledge file.
    Returns None if no file exists for this race ID.
    Files live in race_knowledge/{country}/{stem}.md
    """
    entry = RACE_FILE_MAP.get(preset_race_id)
    if not entry:
        return None
    country, stem = entry
    path = _KNOWLEDGE_DIR / country / f"{stem}.md"
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


# ── VO2X → race time helpers ──────────────────────────────────────────────────

_A = 0.000104
_B = 0.182258
_C_OFFSET = 4.60
_PCT_M = 0.810


def _vo2x_to_marathon_minutes(vo2x: float) -> float:
    v = (-_B + math.sqrt(_B * _B + 4.0 * _A * (vo2x * _PCT_M + _C_OFFSET))) / (2.0 * _A)
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


def _comrades_checkpoints(vo2x: float, race_date_str: Optional[str]) -> str:
    """
    Return personalised Comrades checkpoint splits for the given VO2X.
    Uses effort-fraction model with a 5% buffer below cut-offs.
    """
    is_down = _comrades_is_down(race_date_str)
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
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


def _two_oceans_checkpoints(vo2x: float) -> str:
    """Return personalised Two Oceans checkpoint splits."""
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
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

def _capetown_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
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

def _soweto_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
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

def _om_die_dam_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
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


# ── Durban International Marathon pacing ─────────────────────────────────────

def _durban_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
    pace = marathon_min / 42.195
    lines = [
        "**Durban International Marathon Personalised Pacing**",
        f"Predicted finish: **{_fmt_hm(marathon_min)}**  |  Goal pace: **{_fmt_pace_sa(pace)}**",
        "",
        "Durban is one of the flattest marathons in the world \u2014 just 30m elevation gain. "
        "This is a PB course. Run even or negative splits. Aim for the same pace from km 1 to km 42.",
        "",
        f"Heat and humidity are the primary risks. If race-day temperature exceeds 22\u00b0C, "
        f"add 15\u201330 seconds per km to your goal pace. Do not skip aid stations.",
    ]
    return "\n".join(lines)


# ── Knysna Forest Marathon pacing ─────────────────────────────────────────────

def _knysna_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
    # Trail factor: 700m gain, technical terrain — roughly 20% slower than road
    predicted_min = marathon_min * 1.22
    lines = [
        "**Knysna Forest Marathon Personalised Pacing**",
        f"Road marathon prediction: **{_fmt_hm(marathon_min)}**",
        f"Trail-adjusted target: **{_fmt_hm(predicted_min)}** "
        "(700m gain on technical forest terrain)",
        "",
        "Pacing by time is largely irrelevant in Knysna \u2014 run by effort. "
        "Power-hike the steep climbs aggressively; recover on the descents with a controlled short stride.",
        f"If your road marathon time is over 4:30, budget for approximately "
        f"**{_fmt_hm(predicted_min)}** to **{_fmt_hm(predicted_min * 1.08)}** on race day.",
    ]
    return "\n".join(lines)


def _fmt_pace_sa(min_per_km: float) -> str:
    m = int(min_per_km)
    s = int(round((min_per_km - m) * 60))
    return f"{m}:{s:02d}/km"


# ── UK pacing guides ─────────────────────────────────────────────────────────

def _fmt_pace(min_per_km: float) -> str:
    m = int(min_per_km)
    s = int(round((min_per_km - m) * 60))
    return f"{m}:{s:02d}/km"


def _london_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
    pace = marathon_min / 42.195
    lines = [
        "**London Marathon Personalised Pacing**",
        f"Predicted finish: **{_fmt_hm(marathon_min)}**  |  Goal pace: **{_fmt_pace(pace)}**",
        "",
        "| Segment | Target Pace | Notes |",
        "|---|---|---|",
        f"| Blackheath start (0\u20135 km) | {_fmt_pace(pace * 1.03)} | Congested. Stay patient, don\u2019t surge. |",
        f"| Through Bermondsey (5\u201315 km) | {_fmt_pace(pace)} | Goal pace. Long flat stretch \u2014 bank rhythm not time. |",
        f"| Tower Bridge & Isle of Dogs (15\u201330 km) | {_fmt_pace(pace)} | The iconic section. Focus on form. |",
        f"| Victoria Embankment (30\u201338 km) | {_fmt_pace(pace * 1.01)} | Hardest miles. Effort-match, not pace-match. |",
        f"| The Mall finish (38\u201342 km) | {_fmt_pace(pace * 0.98)} | Empty the tank \u2014 Buckingham Palace is the reward. |",
    ]
    return "\n".join(lines)


def _manchester_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
    pace = marathon_min / 42.195
    lines = [
        "**Manchester Marathon Personalised Pacing**",
        f"Predicted finish: **{_fmt_hm(marathon_min)}**  |  Goal pace: **{_fmt_pace(pace)}**",
        "",
        "Manchester is the UK's fastest marathon \u2014 flat loop, ideal for a PB.",
        f"Aim for even splits throughout at **{_fmt_pace(pace)}**. "
        "The course has minimal elevation change so there is no reason to bank time early.",
        "",
        f"Watch for the notorious headwind between km 28\u201334 along the A56. "
        "Tuck in behind other runners and do not force the pace.",
    ]
    return "\n".join(lines)


def _brighton_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
    pace = marathon_min / 42.195
    lines = [
        "**Brighton Marathon Personalised Pacing**",
        f"Predicted finish: **{_fmt_hm(marathon_min)}**  |  Goal pace: **{_fmt_pace(pace)}**",
        "",
        "| Segment | Target Pace | Notes |",
        "|---|---|---|",
        f"| Preston Park (0\u20138 km) | {_fmt_pace(pace * 1.02)} | Rolling start. Don\u2019t go out too fast with the crowd. |",
        f"| Seafront out-and-back (8\u201330 km) | {_fmt_pace(pace)} | Exposed to wind \u2014 be prepared for either direction. |",
        f"| Final loop back to Preston Park (30\u201342 km) | {_fmt_pace(pace * 0.99)} | Crowd support picks up here \u2014 use it. |",
    ]
    return "\n".join(lines)


def _edinburgh_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
    # Edinburgh has 240m elevation — slight adjustment
    adjusted_min = marathon_min * 1.02
    pace = adjusted_min / 42.195
    lines = [
        "**Edinburgh Marathon Personalised Pacing**",
        f"Predicted finish: **{_fmt_hm(adjusted_min)}** (adjusted for 240m elevation)",
        f"Goal pace: **{_fmt_pace(pace)}**",
        "",
        "Edinburgh runs point-to-point east from the city to East Lothian. "
        "The first 5 km is slightly undulating through the city; from km 8 the course "
        "is fast and mostly downhill to the coast.",
        f"Conservative target: start at **{_fmt_pace(pace * 1.03)}** for the first 8 km, "
        f"then settle to **{_fmt_pace(pace)}** once you hit the coastal flats.",
    ]
    return "\n".join(lines)


def _loch_ness_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
    # Loch Ness: 349m gain, net downhill but rolling — ~3% effort penalty
    adjusted_min = marathon_min * 1.03
    pace = adjusted_min / 42.195
    lines = [
        "**Loch Ness Marathon Personalised Pacing**",
        f"Predicted finish: **{_fmt_hm(adjusted_min)}** (adjusted for 349m rolling Highland terrain)",
        f"Effort-based goal pace: **{_fmt_pace(pace)}**",
        "",
        "Loch Ness is a net-downhill point-to-point from Fort Augustus to Inverness. "
        "Do NOT run the downhills fast \u2014 quad-busting descents after km 20 will destroy your finish.",
        "",
        f"Run the first 18 km (hilly) at **{_fmt_pace(pace * 1.04)}** by effort. "
        f"The final 24 km to Inverness open up \u2014 aim for **{_fmt_pace(pace * 0.98)}** if legs allow.",
    ]
    return "\n".join(lines)


def _yorkshire_pacing(vo2x: float) -> str:
    marathon_min = _vo2x_to_marathon_minutes(vo2x)
    pace = marathon_min / 42.195
    lines = [
        "**Yorkshire Marathon Personalised Pacing**",
        f"Predicted finish: **{_fmt_hm(marathon_min)}**  |  Goal pace: **{_fmt_pace(pace)}**",
        "",
        "Yorkshire is a flat, PB-friendly course through York city and the Vale of York. "
        "Total elevation gain is just 157m with no significant climbs.",
        f"Aim for even splits at **{_fmt_pace(pace)}** throughout. "
        "The exposed Vale of York sections can carry a cross-wind \u2014 "
        "tuck in behind other runners if wind is strong.",
        "",
        "October weather in York averages 10\u201315\u00b0C \u2014 ideal conditions. "
        "Be prepared for rain and pack a throwaway layer for the start.",
    ]
    return "\n".join(lines)


# ── Main public API ───────────────────────────────────────────────────────────

def get_race_context(
    preset_race_id: Optional[str],
    vo2x: Optional[float],
    race_date_str: Optional[str] = None,
) -> dict:
    """
    Return a dict with:
      - knowledge_text: full race markdown (empty string if no file)
      - checkpoint_summary: personalised split guidance (empty string if no VO2X)
      - race_display_name: human-readable race name
    """
    display_name = RACE_DISPLAY_NAMES.get(preset_race_id or "", "your race")
    knowledge_text = load_race_knowledge(preset_race_id or "") or ""
    checkpoint_summary = ""

    if vo2x and vo2x > 0 and preset_race_id:
        try:
            # SA races
            if preset_race_id == "comrades_marathon":
                checkpoint_summary = _comrades_checkpoints(vo2x, race_date_str)
            elif preset_race_id == "two_oceans_marathon":
                checkpoint_summary = _two_oceans_checkpoints(vo2x)
            elif preset_race_id == "cape_town_marathon":
                checkpoint_summary = _capetown_pacing(vo2x)
            elif preset_race_id == "soweto_marathon":
                checkpoint_summary = _soweto_pacing(vo2x)
            elif preset_race_id == "durban_international_marathon":
                checkpoint_summary = _durban_pacing(vo2x)
            elif preset_race_id == "knysna_forest_marathon":
                checkpoint_summary = _knysna_pacing(vo2x)
            # UK races
            elif preset_race_id == "london_marathon":
                checkpoint_summary = _london_pacing(vo2x)
            elif preset_race_id == "manchester_marathon":
                checkpoint_summary = _manchester_pacing(vo2x)
            elif preset_race_id == "brighton_marathon":
                checkpoint_summary = _brighton_pacing(vo2x)
            elif preset_race_id == "edinburgh_marathon":
                checkpoint_summary = _edinburgh_pacing(vo2x)
            elif preset_race_id == "yorkshire_marathon":
                checkpoint_summary = _yorkshire_pacing(vo2x)
            elif preset_race_id == "loch_ness_marathon":
                checkpoint_summary = _loch_ness_pacing(vo2x)
        except Exception:
            checkpoint_summary = ""

    return {
        "knowledge_text":      knowledge_text,
        "checkpoint_summary":  checkpoint_summary,
        "race_display_name":   display_name,
    }
