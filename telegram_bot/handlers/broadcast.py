"""
/broadcast — send a message to every athlete.

This lives in the bot rather than in coach_core: broadcasting is a Telegram
delivery concern, and the engine has no business holding a bot token or
knowing how an athlete is reached. The bot already has both the token and the
send machinery, so this is where it belongs.

Usage (admins only):
    /broadcast <message>

The message body supports the same HTML formatting as every other bot message.
Authorised admins are listed in ADMIN_TELEGRAM_IDS as a comma-separated list.
"""
from __future__ import annotations

import asyncio
import html
import logging
import os

from sqlalchemy import select
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from coach_core.database import AsyncSessionLocal
from coach_core.models import Athlete

logger = logging.getLogger(__name__)

# Telegram allows ~30 messages/second; 0.04s between sends keeps us at ~25/s.
SEND_INTERVAL_SECONDS = 0.04


def _admin_ids() -> set[str]:
    raw = os.getenv("ADMIN_TELEGRAM_IDS", "")
    return {part.strip() for part in raw.split(",") if part.strip()}


def is_admin(telegram_user_id: str) -> bool:
    """True if this user may broadcast. Empty allowlist means nobody can."""
    return str(telegram_user_id) in _admin_ids()


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the given message to every athlete on the platform."""
    sender = str(update.effective_user.id)

    if not is_admin(sender):
        # Say nothing useful to non-admins — don't advertise the command.
        await update.message.reply_text("Unknown command.")
        return

    message = " ".join(context.args).strip() if context.args else ""
    if not message:
        await update.message.reply_text(
            "Usage: <code>/broadcast your message here</code>\n\n"
            "HTML formatting is supported.",
            parse_mode=ParseMode.HTML,
        )
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Athlete.athlete_ref))
        refs = [row[0] for row in result.fetchall()]

    if not refs:
        await update.message.reply_text("No athletes to send to.")
        return

    status = await update.message.reply_text(f"📣 Sending to {len(refs)} athletes…")

    sent = 0
    failed: list[str] = []
    for ref in refs:
        try:
            await context.bot.send_message(
                chat_id=ref, text=message, parse_mode=ParseMode.HTML
            )
            sent += 1
        except Exception as e:
            failed.append(ref)
            logger.warning("broadcast: failed for %s — %s", ref, e)
        await asyncio.sleep(SEND_INTERVAL_SECONDS)

    summary = f"📣 <b>Broadcast complete</b>\n\nSent: {sent}\nFailed: {len(failed)}"
    if failed:
        # Escaped — these are stored values, and an unescaped '<' would break
        # the summary message itself.
        preview = ", ".join(html.escape(str(r)) for r in failed[:10])
        summary += f"\n<code>{preview}</code>"
        if len(failed) > 10:
            summary += f"\n…and {len(failed) - 10} more"

    try:
        await status.edit_text(summary, parse_mode=ParseMode.HTML)
    except Exception:
        await update.message.reply_text(summary, parse_mode=ParseMode.HTML)

    logger.info("broadcast by %s: %d sent, %d failed", sender, sent, len(failed))
