"""
United Kingdom race presets.

Imported by race_presets.py (facade) when RACE_COUNTRY=uk.
"""
from __future__ import annotations
from datetime import date


def _next_occurrence(month: int, day: int) -> str:
    """Return YYYY-MM-DD for the next occurrence of this month/day."""
    today = date.today()
    candidate = date(today.year, month, day)
    if candidate <= today:
        candidate = date(today.year + 1, month, day)
    return candidate.isoformat()


RACE_PRESETS_UK: dict[str, dict] = {
    "london_marathon": {
        "display_name":      "TCS London Marathon",
        "emoji":             "\U0001f451",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "low",
        "elevation_gain_m":  127,
        "typical_month":     4,          # April — 2026: 27 Apr
        "typical_day":       27,
        "country":           "uk",
        "description":       (
            "Point-to-point from Blackheath to The Mall, this flat, iconic course passes "
            "landmarks like Cutty Sark, Tower Bridge, and Buckingham Palace."
        ),
    },
    "manchester_marathon": {
        "display_name":      "adidas Manchester Marathon",
        "emoji":             "\U0001f3ed",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "low",
        "elevation_gain_m":  54,
        "typical_month":     4,          # April — 2026: 27 Apr
        "typical_day":       27,
        "country":           "uk",
        "description":       (
            "A very flat, fast course starting and finishing near Old Trafford, making it "
            "one of the UK's best for a personal best."
        ),
    },
    "brighton_marathon": {
        "display_name":      "Brighton Marathon",
        "emoji":             "\U0001f30a",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "low",
        "elevation_gain_m":  145,
        "typical_month":     4,          # April — 2026: 12 Apr
        "typical_day":       12,
        "country":           "uk",
        "description":       (
            "Starts at Preston Park, winds through the city, and finishes along the vibrant "
            "seafront. A mix of rolling inclines and flat stretches."
        ),
    },
    "edinburgh_marathon": {
        "display_name":      "Edinburgh Marathon",
        "emoji":             "\U0001f3f4",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "low",
        "elevation_gain_m":  38,
        "typical_month":     5,          # May — 2026: 25 May
        "typical_day":       25,
        "country":           "uk",
        "description":       (
            "Point-to-point with a net downhill profile from the city centre to the coast "
            "at Musselburgh, often touted as the UK's fastest marathon."
        ),
    },
    "yorkshire_marathon": {
        "display_name":      "Yorkshire Marathon",
        "emoji":             "\U0001f339",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "low",
        "elevation_gain_m":  157,
        "typical_month":     10,         # October — 2026: 19 Oct
        "typical_day":       19,
        "country":           "uk",
        "description":       (
            "Starts and finishes at the University of York, taking in the historic city "
            "centre and the surrounding Vale of York countryside."
        ),
    },
    "loch_ness_marathon": {
        "display_name":      "Baxters Loch Ness Marathon",
        "emoji":             "\U0001f3f4\U0001f409",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "medium",
        "elevation_gain_m":  349,
        "typical_month":     9,          # September — 2026: 27 Sep
        "typical_day":       27,
        "country":           "uk",
        "description":       (
            "A scenic, point-to-point course along the shores of Loch Ness, featuring a "
            "net downhill start followed by rolling terrain and a final climb into Inverness."
        ),
    },
}

RACE_COORDS_UK: dict[str, tuple[float, float]] = {
    "london_marathon":     (51.4870, -0.0224),    # Blackheath start
    "manchester_marathon": (53.4808, -2.2426),    # Old Trafford area
    "brighton_marathon":   (50.8208, -0.1395),    # Preston Park start
    "edinburgh_marathon":  (55.9533, -3.1883),    # Holyrood, Edinburgh
    "yorkshire_marathon":  (53.9490, -1.0501),    # University of York start
    "loch_ness_marathon":  (57.1716, -4.6934),    # Whitebridge start area
}


def get_next_race_date_uk(preset_id: str) -> str:
    p = RACE_PRESETS_UK.get(preset_id)
    if not p:
        return date.today().isoformat()
    return _next_occurrence(p["typical_month"], p["typical_day"])
