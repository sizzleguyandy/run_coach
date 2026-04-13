"""
anchor.py — Anchor Runs setup flow
====================================
Lets athletes pin up to 2 club / group runs per week.
Pinned days are fixed in the plan; other easy days adjust to preserve volume.

States:
  ANCHOR_SELECT_DAY   — user picks first (or second) anchor day
  ANCHOR_ENTER_KM     — user types the distance for the selected day
"""
from __future__ import annotations

import httpx
import logging
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from telegram_bot.config import API_BASE_URL
from coach_core.engine.anchor_constants import ANCHOR_BLOCKED_SESSIONS as ANCHOR_BLOCKED

_log = logging.getLogger(__name__)

# Conversation states
ANCHOR_SELECT_DAY = "ANCHOR_SELECT_DAY"
ANCHOR_ENTER_KM   = "ANCHOR_ENTER_KM"

_DIV = "──────────────────────────"


# ── helpers ───────────────────────────────────────────────────────────────────

async def _fetch_plan(telegram_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{API_BASE_URL}/plan/{telegram_id}/current")
        return r.json() if r.status_code == 200 else {}


async def _fetch_anchors(telegram_id: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.get(f"{API_BASE_URL}/athlete/{telegram_id}/anchors")
        if r.status_code == 200:
            return r.json().get("anchors", [])
    return []


async def _save_anchors(telegram_id: str, anchors: list[dict]) -> bool:
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.patch(
            f"{API_BASE_URL}/athlete/{telegram_id}/anchors",
            json={"anchors": anchors},
        )
        return r.status_code == 200


def _eligible_days(week: dict, existing_anchors: list[dict]) -> list[tuple[str, str, float]]:
    """
    Return (day, session_name, km) for days that are eligible to be anchored
    and not already anchored.
    """
    already = {a["day"] for a in existing_anchors}
    days = week.get("days", {})
    order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    result = []
    for day in order:
        if day in already:
            continue
        s = days.get(day, {})
        name = s.get("session", "Rest")
        km   = s.get("km", 0)
        if name not in ANCHOR_BLOCKED and km > 0:
            result.append((day, name, km))
    return result


def _format_anchor_summary(anchors: list[dict]) -> str:
    if not anchors:
        return "<i>No anchor runs set.</i>"
    lines = []
    for a in anchors:
        lines.append(f"📌 <b>{a['day']}</b>  {a['km']:g} km  — Group Run")
    return "\n".join(lines)


# ── Entry point (called from menu callback) ───────────────────────────────────

async def anchor_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Show current anchors and options."""
    query = update.callback_query
    if query:
        await query.answer()

    telegram_id = str(update.effective_user.id)
    anchors = await _fetch_anchors(telegram_id)

    summary = _format_anchor_summary(anchors)
    can_add = len(anchors) < 2

    text = (
        f"<b>ANCHOR RUNS</b>\n{_DIV}\n\n"
        "Anchor runs are your fixed club or group runs.\n"
        "The system locks them in and adjusts your other easy days around them.\n\n"
        f"{_DIV}\n"
        f"<b>Current anchors</b>\n{summary}\n{_DIV}"
    )

    buttons = []
    if can_add:
        buttons.append([InlineKeyboardButton(
            "📌 Add anchor run", callback_data="anchor_add"
        )])
    if anchors:
        buttons.append([InlineKeyboardButton(
            "🗑 Remove all anchors", callback_data="anchor_clear"
        )])
    buttons.append([InlineKeyboardButton("← Menu", callback_data="menu")])

    kb = InlineKeyboardMarkup(buttons)
    msg = update.effective_message
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await msg.reply_text(text, reply_markup=kb, parse_mode="HTML")

    return ConversationHandler.END


# ── Add anchor flow ───────────────────────────────────────────────────────────

async def anchor_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Show eligible days the user can anchor."""
    query = update.callback_query
    await query.answer()

    telegram_id = str(update.effective_user.id)
    week    = await _fetch_plan(telegram_id)
    anchors = await _fetch_anchors(telegram_id)

    eligible = _eligible_days(week, anchors)

    if not eligible:
        text = (
            "No eligible easy days available to anchor.\n\n"
            "Anchor runs can only be set on easy or recovery run days."
        )
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("← Back", callback_data="anchor_menu")
        ]])
        await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
        return ConversationHandler.END

    # Store week in context for the km step
    context.user_data["anchor_week"] = week
    context.user_data["pending_anchors"] = list(anchors)

    buttons = [
        [InlineKeyboardButton(
            f"{day}  —  {name} ({km:g} km)",
            callback_data=f"anchor_day_{day}"
        )]
        for day, name, km in eligible
    ]
    buttons.append([InlineKeyboardButton("← Cancel", callback_data="anchor_menu")])

    text = (
        f"<b>SELECT ANCHOR DAY</b>\n{_DIV}\n\n"
        "Which day is your club / group run?\n\n"
        "<i>Only easy and recovery run days are shown.</i>"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    return ANCHOR_SELECT_DAY


async def anchor_day_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """User picked a day — ask for the distance."""
    query = update.callback_query
    await query.answer()

    day = query.data.replace("anchor_day_", "")
    context.user_data["anchor_pending_day"] = day

    # Suggest common distances
    buttons = [
        [
            InlineKeyboardButton("5 km",  callback_data="anchor_km_5"),
            InlineKeyboardButton("8 km",  callback_data="anchor_km_8"),
            InlineKeyboardButton("10 km", callback_data="anchor_km_10"),
        ],
        [
            InlineKeyboardButton("12 km", callback_data="anchor_km_12"),
            InlineKeyboardButton("15 km", callback_data="anchor_km_15"),
            InlineKeyboardButton("Other", callback_data="anchor_km_other"),
        ],
        [InlineKeyboardButton("← Back", callback_data="anchor_add")],
    ]

    text = (
        f"<b>{day} — Group Run distance</b>\n{_DIV}\n\n"
        "How far is your club / group run?"
    )
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons), parse_mode="HTML")
    return ANCHOR_ENTER_KM


async def anchor_km_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """User tapped a preset km button."""
    query = update.callback_query
    await query.answer()

    km_str = query.data.replace("anchor_km_", "")

    if km_str == "other":
        text = (
            f"<b>Enter distance</b>\n{_DIV}\n\n"
            "Type the distance in km (e.g. <code>11</code> or <code>6.5</code>):"
        )
        await query.edit_message_text(text, parse_mode="HTML")
        return ANCHOR_ENTER_KM

    return await _save_anchor_entry(update, context, float(km_str))


async def anchor_km_typed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """User typed a custom km value."""
    raw = (update.message.text or "").strip().replace(",", ".")
    try:
        km = float(raw)
        if km <= 0 or km > 100:
            raise ValueError
    except ValueError:
        await update.message.reply_text(
            "Please enter a valid distance in km (e.g. <code>10</code> or <code>6.5</code>).",
            parse_mode="HTML",
        )
        return ANCHOR_ENTER_KM

    return await _save_anchor_entry(update, context, km)


async def _save_anchor_entry(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    km: float,
) -> str:
    """Persist the new anchor and confirm."""
    telegram_id = str(update.effective_user.id)
    day = context.user_data.get("anchor_pending_day", "")
    pending = list(context.user_data.get("pending_anchors", []))

    # Remove any existing entry for this day, then add the new one
    pending = [a for a in pending if a["day"] != day]
    pending.append({"day": day, "km": km})
    pending.sort(key=lambda a: ["Mon","Tue","Wed","Thu","Fri","Sat","Sun"].index(a["day"]))

    ok = await _save_anchors(telegram_id, pending)

    summary = _format_anchor_summary(pending)
    can_add_more = len(pending) < 2

    if ok:
        status_line = "✅ Anchor saved."
    else:
        status_line = "⚠️ Could not save — try again."

    text = (
        f"<b>ANCHOR RUNS</b>\n{_DIV}\n\n"
        f"{status_line}\n\n"
        f"{summary}\n{_DIV}"
    )

    buttons = []
    if can_add_more and ok:
        buttons.append([InlineKeyboardButton(
            "📌 Add another anchor", callback_data="anchor_add"
        )])
    buttons.append([InlineKeyboardButton("← Menu", callback_data="menu")])

    kb = InlineKeyboardMarkup(buttons)
    msg = update.effective_message
    try:
        await msg.edit_text(text, reply_markup=kb, parse_mode="HTML")
    except Exception:
        await msg.reply_text(text, reply_markup=kb, parse_mode="HTML")

    return ConversationHandler.END


# ── Clear anchors ─────────────────────────────────────────────────────────────

async def anchor_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    """Remove all anchor runs."""
    query = update.callback_query
    await query.answer()

    telegram_id = str(update.effective_user.id)
    ok = await _save_anchors(telegram_id, [])

    text = (
        "✅ All anchor runs removed.\n\n"
        "Your plan will return to its original schedule."
        if ok else
        "⚠️ Could not clear anchors — please try again."
    )
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("← Menu", callback_data="menu")]])
    await query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    return ConversationHandler.END


# ── ConversationHandler registration ─────────────────────────────────────────

def build_anchor_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(anchor_menu,      pattern="^anchor_menu$"),
            CallbackQueryHandler(anchor_add_start, pattern="^anchor_add$"),
            CallbackQueryHandler(anchor_clear,     pattern="^anchor_clear$"),
        ],
        states={
            ANCHOR_SELECT_DAY: [
                CallbackQueryHandler(anchor_day_selected, pattern=r"^anchor_day_\w+$"),
                CallbackQueryHandler(anchor_add_start,    pattern="^anchor_add$"),
                CallbackQueryHandler(anchor_menu,         pattern="^anchor_menu$"),
            ],
            ANCHOR_ENTER_KM: [
                CallbackQueryHandler(anchor_km_selected,  pattern=r"^anchor_km_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, anchor_km_typed),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(anchor_menu, pattern="^anchor_menu$"),
        ],
        per_message=False,
        name="anchor_conv",
    )
