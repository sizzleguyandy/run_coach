"""
South Africa race presets.

Imported by race_presets.py (facade) when RACE_COUNTRY=sa.
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


RACE_PRESETS_SA: dict[str, dict] = {
    "comrades_marathon": {
        "display_name":      "Comrades Marathon",
        "emoji":             "\U0001f1ff\U0001f1e6\U0001f3d4\ufe0f",
        "race_distance":     "ultra_90",
        "exact_distance_km": 87.6,      # Up Run; Down Run is ~89.9 km
        "hilliness":         "high",
        "elevation_gain_m":  1800,
        "typical_month":     6,          # June — 2026: 14 Jun
        "typical_day":       14,
        "country":           "sa",
        "description":       (
            "The world's oldest and largest ultra-marathon, alternating annually between "
            "Durban and Pietermaritzburg. Known for its brutal hills and the legendary "
            "\"Big Five\" climbs that define this South African sporting institution."
        ),
    },
    "two_oceans_marathon": {
        "display_name":      "Two Oceans Marathon",
        "emoji":             "\U0001f30a\U0001f30a",
        "race_distance":     "ultra_56",
        "exact_distance_km": 56.0,
        "hilliness":         "medium",
        "elevation_gain_m":  747,
        "typical_month":     4,          # April (Easter Saturday — 2026: 11 Apr)
        "typical_day":       11,
        "country":           "sa",
        "description":       (
            "Iconic Cape Town ultra-marathon that traverses both the Atlantic and Indian Ocean "
            "coastlines. Renowned as one of the world's most beautiful races, featuring the "
            "challenging Chapman's Peak climb and finishing at the University of Cape Town."
        ),
    },
    "cape_town_marathon": {
        "display_name":      "Sanlam Cape Town Marathon",
        "emoji":             "\U0001f3de\ufe0f\U0001f3c3",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "medium",
        "elevation_gain_m":  282,
        "typical_month":     5,          # May — 2026: 24 May
        "typical_day":       24,
        "country":           "sa",
        "description":       (
            "A fast, predominantly flat city marathon starting and finishing at Green Point "
            "Stadium. Africa's only Abbott World Marathon Majors candidate, with Table "
            "Mountain as a constant backdrop."
        ),
    },
    "soweto_marathon": {
        "display_name":      "African Bank Soweto Marathon",
        "emoji":             "\u270a\U0001f3ff\U0001f3df\ufe0f",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "low",
        "elevation_gain_m":  300,
        "typical_month":     11,         # November — 2026: 29 Nov
        "typical_day":       29,
        "country":           "sa",
        "description":       (
            "\"The People's Race\" — winds through historic Soweto past Vilakazi Street and "
            "FNB Stadium. High altitude (1,700m+) and vibrant community support create a "
            "uniquely challenging and cultural experience."
        ),
    },
    "durban_international_marathon": {
        "display_name":      "Durban International Marathon",
        "emoji":             "\U0001f3d6\ufe0f\U0001f334",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "low",
        "elevation_gain_m":  30,
        "typical_month":     5,          # May — 2026: 3 May
        "typical_day":       3,
        "country":           "sa",
        "description":       (
            "A flat, fast, and modern marathon starting at the old Durban International "
            "Airport and finishing at Moses Mabhida Stadium. Ideal for personal bests, "
            "one of the fastest courses on the South African calendar."
        ),
    },
    "knysna_forest_marathon": {
        "display_name":      "Knysna Forest Marathon",
        "emoji":             "\U0001f332\U0001f333",
        "race_distance":     "marathon",
        "exact_distance_km": 42.195,
        "hilliness":         "medium",
        "elevation_gain_m":  700,
        "typical_month":     7,          # July — 2026: 4 Jul
        "typical_day":       4,
        "country":           "sa",
        "description":       (
            "A challenging trail marathon through the ancient, mystical forests of Knysna. "
            "Features a demanding mix of climbs and technical terrain — a completely "
            "different experience from a standard city road race."
        ),
    },
}

RACE_COORDS_SA: dict[str, tuple[float, float]] = {
    "comrades_marathon":             (-29.8587, 31.0218),   # Durban City Hall (Up Run start)
    "two_oceans_marathon":           (-33.9715, 18.4651),   # Dean St, Newlands, Cape Town
    "cape_town_marathon":            (-33.9063, 18.4074),   # Fritz Sonnenberg Rd, Green Point
    "soweto_marathon":               (-26.2363, 27.9848),   # Nasrec, near FNB Stadium
    "durban_international_marathon": (-29.9931, 30.9340),   # Prospecton Road, Durban
    "knysna_forest_marathon":        (-34.0468, 23.0710),   # Knysna Forest outskirts
}


def get_next_race_date_sa(preset_id: str) -> str:
    p = RACE_PRESETS_SA.get(preset_id)
    if not p:
        return date.today().isoformat()
    return _next_occurrence(p["typical_month"], p["typical_day"])
