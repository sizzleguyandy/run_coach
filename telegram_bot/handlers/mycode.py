"""
/mycode — generate and display the athlete's link code.

The link code lets them connect the Virgin Race mobile app to their
existing Telegram training plan without re-onboarding.
"""
from __future__ import annotations

import re
import secrets

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from coach_core.database import AsyncSessionLocal
from coach_core.models import Athlete

# Unambiguous alphabet (no 0/O, 1/I/L) — 8 chars ≈ 1.1 × 10^12 combinations,
# vs the old 4-digit codes (10,000) which were trivially brute-forceable.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_LEGACY_CODE_RE = re.compile(r"^[A-Z]{4}-\d{4}$")   # old weak format e.g. ANDY-4821


def _generate_link_code(name: str) -> str:
    """Generate a human-friendly but unguessable code e.g. ANDY-7K2M9QX4."""
    prefix = ''.join(c for c in name.upper() if c.isalpha())[:4].ljust(4, 'X')
    body = ''.join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{prefix}-{body}"


async def cmd_mycode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show (or generate) the athlete's personal link code."""
    tid = str(update.effective_user.id)

    async with AsyncSessionLocal() as db:
        result  = await db.execute(select(Athlete).where(Athlete.telegram_id == tid))
        athlete = result.scalar_one_or_none()

        if not athlete:
            await update.message.reply_text(
                "❌ No training plan found. Use /start to set up your plan first."
            )
            return

        # Generate a code if one doesn't exist yet, or silently upgrade a
        # legacy 4-digit code (guessable) to the strong format.
        if not athlete.link_code or _LEGACY_CODE_RE.match(athlete.link_code):
            # Ensure uniqueness (retry up to 5 times)
            for _ in range(5):
                candidate = _generate_link_code(athlete.name)
                existing  = await db.execute(
                    select(Athlete).where(Athlete.link_code == candidate)
                )
                if not existing.scalar_one_or_none():
                    athlete.link_code = candidate
                    break
            await db.commit()

        code = athlete.link_code

    await update.message.reply_text(
        f"🔗 *Your Virgin Race Link Code*\n\n"
        f"`{code}`\n\n"
        f"Open the Virgin Race app → tap *Link Telegram Account* → enter this code.\n\n"
        f"Your full training plan, VO2X, and race details will sync instantly — "
        f"no need to re-onboard.",
        parse_mode="Markdown",
    )
