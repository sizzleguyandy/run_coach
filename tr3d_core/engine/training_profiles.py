"""
Training profiles — Conservative vs Aggressive.

These constants govern how aggressively the system builds mileage
and how it responds to missed weeks and high RPE.

Conservative (default):
  Lower injury risk, deeper recovery weeks, slower progression.
  Recommended for: returning runners, injury history, beginners on
  a full plan, anyone who has struggled with consistency.

Aggressive:
  Faster mileage build, shallower cutbacks, higher peak potential.
  Recommended for: experienced runners with consistent history,
  athletes chasing a specific time goal, those who know their body.
"""
from __future__ import annotations

PROFILES: dict[str, dict] = {
    "conservative": {
        "label":             "Conservative",
        "build_rate":        1.08,    # +8% per week
        "cutback_factor":    0.82,    # every 4th week drops to 82% (deeper recovery)
        "peak_cap_factor":   2.0,     # target peak capped at 2× starting mileage
        "over_boost":        1.01,    # +1% if >105% compliance
        "under_penalty":     0.85,    # -15% if <80% compliance
        "rpe9_cap":          0.85,    # max modifier when avg RPE ≥ 9.0
        "rpe85_cap":         0.90,    # max modifier when avg RPE ≥ 8.5
        "pros": [
            "Lower injury risk — gradual progression protects tendons and bones",
            "Deeper recovery weeks keep fatigue manageable across the full plan",
            "Better for runners returning after a break or with injury history",
            "More forgiving if you miss a week — penalties are smaller",
            "Sustainable over a full 18-24 week season",
        ],
        "cons": [
            "Slower mileage progression — takes longer to reach peak volume",
            "May not reach the target peak for marathon or ultra distance",
            "Less aggressive race time improvement compared to the aggressive plan",
        ],
    },

    "aggressive": {
        "label":             "Aggressive",
        "build_rate":        1.12,    # +12% per week
        "cutback_factor":    0.88,    # every 4th week drops to 88% (shallower cutback)
        "peak_cap_factor":   3.0,     # target peak capped at 3× starting mileage
        "over_boost":        1.03,    # +3% if >105% compliance
        "under_penalty":     0.90,    # -10% if <80% compliance (less punishment)
        "rpe9_cap":          0.90,    # max modifier when avg RPE ≥ 9.0
        "rpe85_cap":         0.95,    # max modifier when avg RPE ≥ 8.5
        "pros": [
            "Faster mileage build — reaches peak volume sooner",
            "Higher peak = better race performance for longer distances",
            "Shallower cutback weeks keep fitness gains from tailing off",
            "Rewards consistent execution with greater volume boosts",
            "Better suited for runners targeting a specific finish time",
        ],
        "cons": [
            "Higher injury risk — demands consistent, disciplined execution",
            "Fatigue can accumulate if you push through tired weeks",
            "Not recommended if you have a history of stress fractures or overuse injury",
            "Missing weeks hurts less but the higher baseline volume means more to catch up",
            "Requires honest RPE logging — the system relies on your feedback to protect you",
        ],
    },
}


def get_profile(name: str) -> dict:
    """Return profile dict. Falls back to conservative if name unknown."""
    return PROFILES.get(name, PROFILES["conservative"])


def format_profile_choice() -> str:
    """Returns HTML comparison message for onboarding."""
    c = PROFILES["conservative"]
    a = PROFILES["aggressive"]

    lines = [
        "<b>Choose your training approach</b>\n",

        "🛡 <b>Conservative</b>",
        "\n".join(f"  ✅ {p}" for p in c["pros"]),
        "",
        "\n".join(f"  ⚠️ {p}" for p in c["cons"]),
        "",

        "⚡ <b>Aggressive</b>",
        "\n".join(f"  ✅ {p}" for p in a["pros"]),
        "",
        "\n".join(f"  ⚠️ {p}" for p in a["cons"]),
    ]
    return "\n".join(lines)
