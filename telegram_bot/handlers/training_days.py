"""
Training days change handler.

Lets an existing athlete re-choose their long run day, quality day, and easy
run days from the Settings menu — without having to redo full onboarding.

Flow (inline button "🗓️ Change training days"):
  settings → CHANGE_LONG_RUN_DAY → CHANGE_QUALITY_DAY → CHANGE_EASY_DAYS → done

PATCHes /athlete/{telegram_id} with the updated day fields and regenerates
the plan by calling /plan/{telegram_id}/rebuild.
"""
from __future__ import annotations

import logging

import httpx
from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    Update,
)
from telegram.ext import ContextTypes, ConversationHandler

from telegram_bot.config import API_BASE_URL
from telegram_bot.handlers.onboarding_v2 import (
    LONG_RUN_DAY_LABELS,
    QUALITY_DAY_LABELS,
    EASY_DAY_LABELS,
)

_log = logging.getLogger(__name__)

# Conversation states — use high numbers to avoid collision with onboarding
CHANGE_LONG_RUN_DAY = 200
CHANGE_QUALITY_DAY  = 201
CHANGE_EASY_DAYS    = 202   # first easy day button selection
CHANGE_EASY_DAY_2   = 203   # second easy day button selection


# ── Entry point ───────────────────────────────────────────────────────────────

async def start_change_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via ⚙️ Settings → 🗓️ Change training days button."""
    if update.callback_query:
        await update.callback_query.answer()

    rows = [[day] for day in LONG_RUN_DAY_LABELS.keys()]
    text = (
        "🗓️ <b>Change training days</b>\n\n"
        "Which day works best for your <b>long run</b>?\n\n"
        "This is your biggest session of the week — pick a day when you "
        "have the most time and energy."
    )
    if update.callback_query:
        # Need to send a new message — can't edit to a ReplyKeyboard
        await update.effective_message.reply_text(
            text,
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
    else:
        await update.effective_message.reply_text(
            text,
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
    return CHANGE_LONG_RUN_DAY


# ── Step 1: long run day ──────────────────────────────────────────────────────

async def change_long_run_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    day  = LONG_RUN_DAY_LABELS.get(text)

    if day is None:
        rows = [[d] for d in LONG_RUN_DAY_LABELS.keys()]
        await update.effective_message.reply_text(
            "Please choose a day from the buttons.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return CHANGE_LONG_RUN_DAY

    context.user_data["chg_long_run_day"] = day

    options = {k: v for k, v in QUALITY_DAY_LABELS.items() if v != day}
    rows = [[k] for k in options.keys()]
    await update.effective_message.reply_text(
        "Which day is best for your <b>hard/quality session</b>?\n\n"
        "Choose a day with good energy — ideally 2+ days before your long run.\n"
        "It should also not be the day before your long run.",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return CHANGE_QUALITY_DAY


# ── Step 2: quality day ───────────────────────────────────────────────────────

async def change_quality_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text         = update.effective_message.text.strip()
    day          = QUALITY_DAY_LABELS.get(text)
    long_run_day = context.user_data.get("chg_long_run_day", "Sat")

    if day is None or day == long_run_day:
        options = {k: v for k, v in QUALITY_DAY_LABELS.items() if v != long_run_day}
        rows = [[k] for k in options.keys()]
        await update.effective_message.reply_text(
            "Please choose a different day to your long run day.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return CHANGE_QUALITY_DAY

    context.user_data["chg_quality_day"] = day

    taken   = {long_run_day, day}
    options = {k: v for k, v in EASY_DAY_LABELS.items() if v not in taken}
    rows = [[k] for k in options.keys()]
    await update.effective_message.reply_text(
        "Which day works best for your <b>first easy run</b>?\n\n"
        "Easy runs are low-intensity — a comfortable, conversational pace.",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return CHANGE_EASY_DAYS


# ── Step 3a: first easy day ───────────────────────────────────────────────────

async def change_easy_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """First easy day — button selection."""
    text         = update.effective_message.text.strip()
    long_run_day = context.user_data.get("chg_long_run_day", "Sat")
    quality_day  = context.user_data.get("chg_quality_day", "Tue")
    taken        = {long_run_day, quality_day}

    day = EASY_DAY_LABELS.get(text)
    if day is None or day in taken:
        options = {k: v for k, v in EASY_DAY_LABELS.items() if v not in taken}
        rows = [[k] for k in options.keys()]
        await update.effective_message.reply_text(
            "Please choose a day from the buttons.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return CHANGE_EASY_DAYS

    context.user_data["chg_easy_day_1"] = day
    taken2  = taken | {day}
    options = {k: v for k, v in EASY_DAY_LABELS.items() if v not in taken2}
    rows    = [[k] for k in options.keys()]
    rows.append(["Only one easy day"])
    await update.effective_message.reply_text(
        "Would you like to add a <b>second easy run day</b>?\n\n"
        "Two easy days help build your aerobic base without piling on too much fatigue.",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return CHANGE_EASY_DAY_2


# ── Step 3b: second easy day → PATCH athlete ─────────────────────────────────

async def change_easy_day_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Second easy day — button selection or skip."""
    text         = update.effective_message.text.strip()
    long_run_day = context.user_data.get("chg_long_run_day", "Sat")
    quality_day  = context.user_data.get("chg_quality_day", "Tue")
    easy_day_1   = context.user_data.get("chg_easy_day_1", "Mon")
    taken        = {long_run_day, quality_day, easy_day_1}

    if text == "Only one easy day":
        chosen = [easy_day_1]
    else:
        day = EASY_DAY_LABELS.get(text)
        if day is None or day in taken:
            options = {k: v for k, v in EASY_DAY_LABELS.items() if v not in taken}
            rows    = [[k] for k in options.keys()]
            rows.append(["Only one easy day"])
            await update.effective_message.reply_text(
                "Please choose a day from the buttons, or tap <b>Only one easy day</b>.",
                reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
                parse_mode="HTML",
            )
            return CHANGE_EASY_DAY_2
        chosen = [easy_day_1, day]

    extra_training_days = ",".join(chosen)
    telegram_id = str(update.effective_user.id)

    # PATCH the athlete record
    payload = {
        "long_run_day":        long_run_day,
        "quality_day":         quality_day,
        "extra_training_days": extra_training_days,
    }

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.patch(f"{API_BASE_URL}/athlete/{telegram_id}", json=payload)
            if not r.is_success:
                raise RuntimeError(f"{r.status_code}: {r.text[:120]}")
    except Exception as e:
        _log.error(f"training_days PATCH failed for {telegram_id}: {e}")
        await update.effective_message.reply_text(
            "⚠️ Something went wrong saving your days. Please try again.",
            reply_markup=ReplyKeyboardRemove(),
        )
        return ConversationHandler.END

    # Format a readable day summary
    easy_readable = " and ".join(
        {v: k for k, v in EASY_DAY_LABELS.items()}.get(d, d) for d in chosen
    )
    lr_readable = {v: k for k, v in LONG_RUN_DAY_LABELS.items()}.get(long_run_day, long_run_day)
    q_readable  = {v: k for k, v in QUALITY_DAY_LABELS.items()}.get(quality_day, quality_day)

    back_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
        InlineKeyboardButton("← Menu",      callback_data="menu"),
    ]])

    await update.effective_message.reply_text(
        "✅ <b>Training days updated!</b>\n\n"
        f"🏃 Long run:   <b>{lr_readable}</b>\n"
        f"⚡ Quality:    <b>{q_readable}</b>\n"
        f"🔄 Easy runs:  <b>{easy_readable}</b>\n\n"
        "Your plan will use these days from the next week.",
        reply_markup=back_kb,
        parse_mode="HTML",
    )
    return ConversationHandler.END


# ── Cancel ────────────────────────────────────────────────────────────────────

async def change_days_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    back_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("← Menu", callback_data="menu"),
    ]])
    await update.effective_message.reply_text(
        "Cancelled. Your training days are unchanged.",
        reply_markup=back_kb,
    )
    return ConversationHandler.END
