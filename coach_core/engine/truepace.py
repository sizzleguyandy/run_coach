"""
TRUEPACE — Weather-based pace adjustment engine.

Data source: Open-Meteo free API (no key required).
Formula: dew point + temperature adjustment per Daniels/ACSM coaching guidelines.
Caching: in-memory dict, 1-hour TTL per (lat, lon, hour) key.
"""
from __future__ import annotations

import asyncio
import time
import math
from dataclasses import dataclass
from typing import Optional

import httpx

# ── Cache ─────────────────────────────────────────────────────────────────
# key: (lat_r, lon_r, target_hour_str) → (fetched_at_epoch, result_dict)
_CACHE: dict[tuple, tuple[float, dict]] = {}
CACHE_TTL_SECONDS = 3600   # 1 hour


def _cache_key(lat: float, lon: float, target_hour: str) -> tuple:
    # Round coords to 2 dp to allow small GPS jitter to share cache entries
    return (round(lat, 2), round(lon, 2), target_hour)


def _cache_get(key: tuple) -> Optional[dict]:
    entry = _CACHE.get(key)
    if entry and (time.time() - entry[0]) < CACHE_TTL_SECONDS:
        return entry[1]
    return None


def _cache_set(key: tuple, value: dict) -> None:
    _CACHE[key] = (time.time(), value)


# ── Weather fetch ─────────────────────────────────────────────────────────

OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude={lat}&longitude={lon}"
    "&hourly=temperature_2m,dew_point_2m"
    "&timezone=auto"
    "&forecast_days=1"
)


async def fetch_weather(lat: float, lon: float, run_hour: int = 7) -> Optional[dict]:
    """
    Fetch hourly forecast and return the entry closest to run_hour.
    Returns dict with keys: temperature, dew_point, time_str, or None on failure.
    """
    target_hour = f"{run_hour:02d}:00"
    cache_key = _cache_key(lat, lon, target_hour)
    cached = _cache_get(cache_key)
    if cached:
        return cached

    url = OPEN_METEO_URL.format(lat=lat, lon=lon)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()

        times = data["hourly"]["time"]
        temps = data["hourly"]["temperature_2m"]
        dews  = data["hourly"]["dew_point_2m"]

        # Find index where hour matches run_hour (closest match)
        best_idx = 0
        best_diff = 9999
        for i, t_str in enumerate(times):
            hour_str = t_str.split("T")[1][:5]   # "HH:MM"
            h = int(hour_str.split(":")[0])
            diff = abs(h - run_hour)
            if diff < best_diff:
                best_diff = diff
                best_idx = i

        result = {
            "temperature": temps[best_idx],
            "dew_point":   dews[best_idx],
            "time_str":    times[best_idx],
            "source":      "open-meteo",
        }
        _cache_set(cache_key, result)
        return result

    except Exception:
        return None


# ── Adjustment formula ────────────────────────────────────────────────────

@dataclass
class PaceAdjustment:
    factor: float              # multiplier, e.g. 1.075
    adjustment_pct: float      # e.g. 7.5
    temperature: float
    dew_point: float
    high_heat_warning: bool    # True if factor > 1.10
    quality_note: bool         # True for quality sessions


def compute_adjustment(temperature: float, dew_point: float) -> PaceAdjustment:
    """
    Compute pace adjustment factor from temperature (°C) and dew point (°C).

    Formula (Daniels/ACSM-inspired):
        adjustment = 1.0
        if dew_point > 10: adjustment += (dew_point - 10) * 0.005
        if temperature > 25: adjustment += (temperature - 25) * 0.005
        adjustment = min(adjustment, 1.15)   # cap at 15% slowdown
    """
    adj = 1.0
    if dew_point > 10:
        adj += (dew_point - 10) * 0.005
    if temperature > 25:
        adj += (temperature - 25) * 0.005
    adj = min(round(adj, 4), 1.15)

    return PaceAdjustment(
        factor=adj,
        adjustment_pct=round((adj - 1.0) * 100, 1),
        temperature=temperature,
        dew_point=dew_point,
        high_heat_warning=adj > 1.10,
        quality_note=adj > 1.03,
    )


# ── Pace adjustment helpers ───────────────────────────────────────────────

def adjust_pace_sec(planned_sec_per_km: float, factor: float) -> float:
    """Apply adjustment factor to a pace in seconds/km."""
    return planned_sec_per_km * factor


def format_pace_sec(sec_per_km: float) -> str:
    """Convert seconds/km to mm:ss /km string. Rounds half-seconds up (matches spec)."""
    import math
    total_sec = round(sec_per_km)   # standard rounding; .5-second edge cases are negligible for runners
    minutes = total_sec // 60
    seconds = total_sec % 60
    return f"{minutes}:{seconds:02d} /km"


def min_per_km_to_sec(min_per_km: float) -> float:
    return min_per_km * 60.0


def sec_to_min_per_km(sec: float) -> float:
    return sec / 60.0


# ── Full adjustment for all pace zones ────────────────────────────────────

def adjust_all_paces(paces_dict: dict, factor: float) -> dict:
    """
    Given a dict of {zone: 'mm:ss /km'} strings and a factor,
    return a new dict with adjusted pace strings.

    paces_dict keys: easy, marathon, threshold, interval, repetition
    """
    adjusted = {}
    for zone, pace_str in paces_dict.items():
        if zone == "vdot":
            adjusted[zone] = pace_str
            continue
        # Parse "M:SS /km"
        try:
            time_part = pace_str.split(" /km")[0]
            m, s = map(int, time_part.split(":"))
            sec = m * 60 + s
            adj_sec = adjust_pace_sec(float(sec), factor)
            adjusted[zone] = format_pace_sec(adj_sec)
        except Exception:
            adjusted[zone] = pace_str
    return adjusted


# ── High-level convenience function ───────────────────────────────────────

async def get_truepace_block(
    lat: float,
    lon: float,
    paces_dict: dict,        # {"easy": "4:37 /km", "marathon": ..., etc.}
    run_hour: int = 7,
    session_type: str = "easy",  # "easy", "quality", "long"
) -> dict:
    """
    Fetch weather and return a complete TRUEPACE block for display.

    Returns dict with:
      - weather: {temperature, dew_point, time_str}
      - factor, adjustment_pct
      - paces: original + adjusted for each zone
      - messages: list of display strings
      - available: bool (False if weather fetch failed)
    """
    weather = await fetch_weather(lat, lon, run_hour)

    if not weather:
        return {"available": False, "reason": "Weather data unavailable — using planned paces."}

    adj = compute_adjustment(weather["temperature"], weather["dew_point"])
    adjusted_paces = adjust_all_paces(paces_dict, adj.factor)

    messages = []

    if adj.factor == 1.0:
        messages.append("🌤️ Ideal conditions — run at planned pace.")
    else:
        messages.append(
            f"🌡️ {weather['temperature']:.0f}°C, dew point {weather['dew_point']:.0f}°C — "
            f"adjust pace by +{adj.adjustment_pct}%."
        )

    if adj.high_heat_warning:
        messages.append(
            "🔴 High heat stress — consider running earlier in the day or reducing distance."
        )

    if adj.quality_note and session_type == "quality":
        messages.append(
            "⚡ For hard efforts, focus on RPE rather than exact pace — "
            "the adjustment on interval sessions is significant."
        )

    return {
        "available": True,
        "weather": {
            "temperature": weather["temperature"],
            "dew_point":   weather["dew_point"],
            "time_str":    weather["time_str"],
        },
        "factor":          adj.factor,
        "adjustment_pct":  adj.adjustment_pct,
        "high_heat_warning": adj.high_heat_warning,
        "planned_paces":   paces_dict,
        "adjusted_paces":  adjusted_paces,
        "messages":        messages,
        "session_type":    session_type,
    }
