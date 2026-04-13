"""
Anchor run constants shared between the plan overlay (plan.py)
and the Telegram anchor handler (telegram_bot/handlers/anchor.py).

Single source of truth — edit here, both layers stay in sync.
"""

# Sessions that are fixed by the plan engine and must not be scaled during
# the anchor overlay. They represent peak / quality output days.
ANCHOR_FIXED_SESSIONS: frozenset[str] = frozenset({
    "Long Run",
    "Back-to-Back Run",
    "Downhill Repeats",
    "🏁 RACE DAY",
})

# Sessions that count as rest/cross-training — no running km, skip in overlay.
ANCHOR_REST_SESSIONS: frozenset[str] = frozenset({
    "Rest",
    "Cross-Train / Rest",
    "Rest / Walk",
})

# Sessions the user is NOT allowed to anchor (blocklist approach).
# Combines quality/peak sessions and rest days — everything else is eligible.
ANCHOR_BLOCKED_SESSIONS: frozenset[str] = ANCHOR_FIXED_SESSIONS | ANCHOR_REST_SESSIONS
