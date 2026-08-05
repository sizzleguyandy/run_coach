"""
/mycode — show the athlete's link code.

Every athlete is given a code when their profile is created (see
coach_core/engine/link_codes.py), so this command normally just displays it.
It still handles two older cases: an athlete created before codes were
universal whose backfill has not run, and an athlete still carrying a weak
legacy code, which is upgraded on sight.

Code generation lives in coach_core so the bot and the API can never drift
into two different formats.
"""
from __future__ import annotations

from sqlalchemy import select
from telegram import Update
from telegram.ext import ContextTypes

from coach_core.database import AsyncSessionLocal
from coach_core.engine.link_codes import assign_link_code, is_legacy_code
from coach_core.models import Athlete


async def cmd_mycode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show (or, for older profiles, mint) the athlete's personal link code."""
    tid = str(update.effective_user.id)

    async with AsyncSessionLocal() as db:
        result  = await db.execute(select(Athlete).where(Athlete.athlete_ref == tid))
        athlete = result.scalar_one_or_none()

        if not athlete:
            await update.message.reply_text(
                "❌ No training plan found. Use /start to set up your plan first."
            )
            return

        # Mint one if this profile predates universal codes, or upgrade a weak
        # legacy code. assign_link_code always yields a usable code.
        if not athlete.link_code or is_legacy_code(athlete.link_code):
            await assign_link_code(db, athlete)
            await db.commit()

        code = athlete.link_code

    await update.message.reply_text(
        f"🔗 *Your Link Code*\n\n"
        f"`{code}`\n\n"
        f"Enter this in the app to connect your training plan — "
        f"your full plan, VO2X, and race details sync instantly, "
        f"with no need to re-onboard.",
        parse_mode="Markdown",
    )
