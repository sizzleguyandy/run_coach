"""
Loyalty discount calculator — display only, no real billing yet.

Discount is based on streak_weeks (consecutive compliant weeks).
Capped at 4 for monthly calculation — rewards a full month of logging.

Base price: R99/month
"""
from __future__ import annotations

BASE_PRICE_RANDS = 99.0


def calculate_loyalty_discount(streak_weeks: int) -> dict:
    """
    Return discount info based on current streak.

    Returns dict with:
        pct        — discount percentage (0, 10, 25, or 50)
        weeks      — weeks complete this month (0–4)
        discounted — final price in Rands (display only)
        label      — short human label, or None if no discount
    """
    weeks = min(max(streak_weeks or 0, 0), 4)

    if weeks >= 4:
        pct   = 50
        label = "Perfect month — 50% off"
    elif weeks == 3:
        pct   = 25
        label = "3 weeks complete — 25% off"
    elif weeks == 2:
        pct   = 10
        label = "2 weeks complete — 10% off"
    else:
        pct   = 0
        label = None

    discounted = round(BASE_PRICE_RANDS * (1 - pct / 100), 2)

    return {
        "pct":        pct,
        "weeks":      weeks,
        "label":      label,
        "discounted": discounted,
        "base":       BASE_PRICE_RANDS,
    }


def loyalty_progress_bar(weeks_complete: int, total: int = 4) -> str:
    """Return a filled/empty block bar. e.g. '██░░' for 2/4."""
    filled = min(weeks_complete, total)
    return "█" * filled + "░" * (total - filled)
