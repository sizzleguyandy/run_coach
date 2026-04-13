"""
Famous South African race presets.

When an athlete selects a preset race during onboarding, the system
auto-fills race_distance, race_date, and race_hilliness — skipping
those questions entirely.

Date logic:
  - next_race_date is the next upcoming occurrence.
  - If today is past the stored date, next_year() is used automatically.
"""
from __future__ import annotations
from datetime import date, timedelta


def _next_occurrence(month: int, day: int) -> str:
    """Return YYYY-MM-DD for the next occurrence of this month/day."""
    today = date.today()
    candidate = date(today.year, month, day)
    if candidate <= today:
        candidate = date(today.year + 1, month, day)
    return candidate.isoformat()


def _parkrun_target_date(weeks_ahead: int = 8) -> str:
    """
    Return a Saturday approx. weeks_ahead weeks from now.
    parkrun is every Saturday — default to 8 weeks out as a sensible training window.
    """
    today = date.today()
    days_to_sat = (5 - today.weekday()) % 7 or 7  # days until next Saturday
    next_sat = today + timedelta(days=days_to_sat)
    return (next_sat + timedelta(weeks=weeks_ahead)).isoformat()


RACE_PRESETS: dict[str, dict] = {
    "capetown_marathon": {
        "display_name":      "Cape Town Marathon",
        "emoji":             "🏔️",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "medium",
        "elevation_gain_m":  250,
        "typical_month":     5,    # May (2026: 24 May)
        "typical_day":       24,
        "description":       "Scenic coastal route through the Mother City with rolling hills.",
    },
    "two_oceans": {
        "display_name":      "Two Oceans Ultra Marathon",
        "emoji":             "🌊",
        "race_distance":     "ultra_56",
        "exact_distance_km": 56.0,
        "hilliness":         "high",
        "elevation_gain_m":  1100,
        "typical_month":     4,    # April (Easter Saturday — 2026: 12 Apr)
        "typical_day":       12,
        "description":       "The Beautiful Race — Chapman's Peak and Constantia Nek await.",
    },
    "comrades": {
        "display_name":      "Comrades Marathon",
        "emoji":             "🏆",
        "race_distance":     "ultra_90",
        "exact_distance_km": 90.0,
        "hilliness":         "high",
        "elevation_gain_m":  2000,
        "typical_month":     6,    # June (Youth Day weekend — 2026: 14 Jun, Up Run)
        "typical_day":       14,
        "description":       "The Ultimate Human Race — 2026 is an Up Run, Durban to Pietermaritzburg.",
    },
    "soweto_marathon": {
        "display_name":      "Soweto Marathon",
        "emoji":             "🌆",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "low",
        "elevation_gain_m":  180,
        "typical_month":     11,   # November
        "typical_day":       2,
        "description":       "South Africa's biggest marathon through the heart of Soweto.",
    },
    "om_die_dam": {
        "display_name":      "Om Die Dam Ultra",
        "emoji":             "💧",
        "race_distance":     "ultra_56",
        "exact_distance_km": 50.0,
        "hilliness":         "medium",
        "elevation_gain_m":  600,
        "typical_month":     2,    # February
        "typical_day":       14,
        "description":       "50 km around the Vaal Dam — flat to rolling, hot and tough.",
    },
    "durban_city_marathon": {
        "display_name":      "Durban City Marathon",
        "emoji":             "🏖️",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "medium",
        "elevation_gain_m":  300,
        "typical_month":     5,    # May
        "typical_day":       17,
        "description":       "Coastal marathon through Durban's beachfront and city streets.",
    },
    "parkrun": {
        "display_name":      "parkrun 5K",
        "emoji":             "🏃",
        "race_distance":     "5k",
        "exact_distance_km": 5.0,
        "hilliness":         "low",
        "elevation_gain_m":  30,
        "typical_month":     None,  # every Saturday — special case
        "typical_day":       None,
        "description":       "Free, weekly, timed 5K every Saturday morning. Perfect first race and post-C25K goal.",
    },
}


# ── Race start-line coordinates (lat, lon) ─────────────────────────────────
# Used to fetch accurate race-day weather from Open-Meteo.
# Values are approximate start-line / race-city centroids.
RACE_COORDS: dict[str, tuple[float, float]] = {
    "capetown_marathon":    (-33.9249, 18.4241),   # Cape Town city centre
    "two_oceans":           (-34.0576, 18.4631),   # UCT / start area
    "comrades":             (-29.8587, 30.9830),   # Durban (Up Run start — 2026)
    "soweto_marathon":      (-26.2678, 27.8585),   # FNB Stadium, Soweto
    "om_die_dam":           (-26.8766, 28.0961),   # Vaal Dam area
    "durban_city_marathon": (-29.8587, 31.0218),   # Durban city
    # parkrun: no fixed coords — falls back to athlete's stored location
}


def get_preset(preset_id: str) -> dict | None:
    return RACE_PRESETS.get(preset_id)


def get_next_race_date(preset_id: str) -> str:
    """Return the next occurrence date for this preset as YYYY-MM-DD."""
    if preset_id == "parkrun":
        return _parkrun_target_date(weeks_ahead=8)
    p = RACE_PRESETS.get(preset_id)
    if not p:
        return date.today().isoformat()
    return _next_occurrence(p["typical_month"], p["typical_day"])


def preset_keyboard_rows() -> list[list[str]]:
    """
    Returns keyboard rows for Telegram — preset display names + Other.
    Two presets per row, Other on its own row.
    """
    labels = [
        f"{p['emoji']} {p['display_name']}"
        for p in RACE_PRESETS.values()
    ]
    rows = [labels[i:i+2] for i in range(0, len(labels), 2)]
    rows.append(["Other race — I will enter details"])
    return rows


def find_preset_by_label(text: str) -> str | None:
    """
    Match user button text back to a preset_id.
    Returns preset_id or None if no match.
    """
    text_lower = text.lower().strip()
    for pid, p in RACE_PRESETS.items():
        label = f"{p['emoji']} {p['display_name']}".lower()
        if text_lower == label or p["display_name"].lower() in text_lower:
            return pid
    return None
