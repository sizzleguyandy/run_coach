from coach_core.engine.adaptation import calculate_vo2x_from_race
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes, ConversationHandler
from telegram_bot.config import API_BASE_URL

# ── Conversation states ────────────────────────────────────────────────────
LOG_DAY, LOG_DISTANCE, LOG_DURATION, LOG_RPE = range(4)

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_LOG_KEYS = ("log_day", "log_distance", "log_duration", "log_rpe")


def _clear_log(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Remove only the log-specific keys from user_data (leaves onboarding etc. intact)."""
    for k in _LOG_KEYS:
        context.user_data.pop(k, None)


# ── /log entry point ───────────────────────────────────────────────────────

async def cmd_log(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the run-logging conversation. Works from /log command or inline Log button."""
    if update.callback_query:
        await update.callback_query.answer()

    keyboard = [DAYS[:4], DAYS[4:]]
    await update.effective_message.reply_text(
        "📝 <b>Log a run</b> — which day are you logging?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return LOG_DAY


async def log_get_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    day = update.effective_message.text.strip()
    if day not in DAYS:
        keyboard = [DAYS[:4], DAYS[4:]]
        await update.effective_message.reply_text(
            "Please choose a day from the buttons.",
            reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
        )
        return LOG_DAY

    context.user_data["log_day"] = day
    await update.effective_message.reply_text(
        f"<b>{day}</b> — how far did you run? (km, e.g. 12.5)",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    return LOG_DISTANCE


async def log_get_distance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        dist = float(update.effective_message.text.strip())
        if not (0.1 <= dist <= 200):
            raise ValueError
    except ValueError:
        await update.effective_message.reply_text("Please enter a distance in km (e.g. 12.5).")
        return LOG_DISTANCE

    context.user_data["log_distance"] = dist
    await update.effective_message.reply_text(
        "How long did it take? (minutes, e.g. <code>65</code> — or type <b>skip</b>)",
        parse_mode="HTML",
    )
    return LOG_DURATION


async def log_get_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip().lower()
    if text == "skip":
        context.user_data["log_duration"] = None
    else:
        try:
            mins = float(text)
            if not (1 <= mins <= 1440):
                raise ValueError
            context.user_data["log_duration"] = mins
        except ValueError:
            await update.effective_message.reply_text(
                "Enter duration in minutes (e.g. <code>65</code>) or type <b>skip</b>.",
                parse_mode="HTML",
            )
            return LOG_DURATION

    keyboard = [["1", "2", "3", "4", "5"], ["6", "7", "8", "9", "10"], ["skip"]]
    await update.effective_message.reply_text(
        "Rate the effort: <b>1</b> (very easy) → <b>10</b> (maximal)\n"
        "Or tap <b>skip</b>.",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return LOG_RPE


async def log_get_rpe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip().lower()
    if text == "skip":
        rpe = None
    else:
        try:
            rpe = int(text)
            if not (1 <= rpe <= 10):
                raise ValueError
        except ValueError:
            keyboard = [["1", "2", "3", "4", "5"], ["6", "7", "8", "9", "10"], ["skip"]]
            await update.effective_message.reply_text(
                "Please tap a number 1–10 or <b>skip</b>.",
                reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
                parse_mode="HTML",
            )
            return LOG_RPE

    context.user_data["log_rpe"] = rpe
    telegram_id = str(update.effective_user.id)

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            athlete_r = await client.get(f"{API_BASE_URL}/athlete/{telegram_id}")
            athlete_r.raise_for_status()
            athlete = athlete_r.json()

            from datetime import date
            import math
            start = date.fromisoformat(athlete["start_date"])
            week_number = max(1, math.floor((date.today() - start).days / 7) + 1)

            payload = {
                "telegram_id":        telegram_id,
                "week_number":        week_number,
                "day_name":           context.user_data["log_day"],
                "actual_distance_km": context.user_data["log_distance"],
                "duration_minutes":   context.user_data.get("log_duration"),
                "rpe":                context.user_data.get("log_rpe"),
            }

            log_r = await client.post(f"{API_BASE_URL}/log/run", json=payload)
            log_r.raise_for_status()

        except Exception as e:
            await update.effective_message.reply_text(
                f"❌ <b>Failed to save run.</b>\n\n<i>{e}</i>",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="HTML",
            )
            _clear_log(context)
            return ConversationHandler.END

    dist    = context.user_data["log_distance"]
    day     = context.user_data["log_day"]
    rpe_str = f" · RPE {rpe}/10" if rpe else ""
    dur_str = (
        f" in {int(context.user_data['log_duration'])} min"
        if context.user_data.get("log_duration") else ""
    )

    from telegram_bot.formatting import main_menu_keyboard
    await update.effective_message.reply_text(
        f"✅ <b>Run logged!</b>\n\n"
        f"<b>{day}</b>  ·  {dist} km{dur_str}{rpe_str}",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )
    _clear_log(context)
    return ConversationHandler.END


async def log_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_log(context)
    await update.effective_message.reply_text(
        "Logging cancelled.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ── /progress command ──────────────────────────────────────────────────────

async def cmd_progress(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show week summary + compliance + coaching notes."""
    telegram_id = str(update.effective_user.id)

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            athlete_r = await client.get(f"{API_BASE_URL}/athlete/{telegram_id}")
            if athlete_r.status_code == 404:
                await update.effective_message.reply_text("No profile found. Use /start first.")
                return
            athlete_r.raise_for_status()
            athlete = athlete_r.json()

            from datetime import date
            import math
            start = date.fromisoformat(athlete["start_date"])
            week_number = max(1, math.floor((date.today() - start).days / 7) + 1)

            summary_r = await client.get(
                f"{API_BASE_URL}/log/{telegram_id}/week/{week_number}/summary"
            )
            summary_r.raise_for_status()
            summary = summary_r.json()

            week_r = await client.get(f"{API_BASE_URL}/plan/{telegram_id}/week/{week_number}")
            week_r.raise_for_status()
            week = week_r.json()

        except Exception as e:
            await update.effective_message.reply_text(f"❌ Could not fetch progress: {e}")
            return

    planned    = week["planned_volume_km"]
    actual     = summary["actual_volume_km"]
    compliance = round(actual / max(planned, 0.1) * 100, 1)

    bar_filled = int(compliance / 10)
    bar        = "█" * min(bar_filled, 10) + "░" * max(0, 10 - bar_filled)
    emoji      = "✅" if compliance >= 90 else ("⚠️" if compliance >= 70 else "🔴")

    lines = [
        f"📊 <b>Week {week_number} Progress</b>\n",
        f"Planned:  <b>{planned} km</b>",
        f"Logged:   <b>{actual} km</b>",
        f"\n{emoji}  <code>{bar}</code>  {compliance}%\n",
        f"Sessions logged: {summary['sessions_logged']}\n",
    ]

    if summary["runs"]:
        lines.append("<b>Runs this week:</b>")
        for run in summary["runs"]:
            p     = f"/{run['planned_km']} km" if run.get("planned_km") else ""
            r_str = f" RPE {run['rpe']}" if run.get("rpe") else ""
            lines.append(f"  {run['day']}: {run['actual_km']} km{p}{r_str}")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


# ── /lograce ConversationHandler ───────────────────────────────────────────
# States offset by 10 to avoid collision with LOG_* states
RACE_DIST, RACE_TIME, RACE_CONFIRM = range(10, 13)

RACE_DIST_OPTIONS = {
    "5k":       5.0,
    "10k":      10.0,
    "half":     21.0975,
    "marathon": 42.195,
    "ultra":    None,
}

_RACE_KEYS = ("race_dist_km", "race_dist_label", "race_time_min", "new_vo2x_preview",
              "awaiting_custom_dist", "pending_force")


def _clear_race(context: ContextTypes.DEFAULT_TYPE) -> None:
    for k in _RACE_KEYS:
        context.user_data.pop(k, None)


async def cmd_lograce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the race-result logging conversation."""
    telegram_id = str(update.effective_user.id)
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{API_BASE_URL}/athlete/{telegram_id}")
        if r.status_code == 404:
            await update.effective_message.reply_text("No profile found. Use /start first.")
            return ConversationHandler.END

    keyboard = [["5k", "10k"], ["half", "marathon"], ["ultra / other"]]
    await update.effective_message.reply_text(
        "🏁 <b>Log a race result</b>\n\n"
        "This will calculate your new VO2X and update your training paces.\n\n"
        "What distance did you race?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return RACE_DIST


async def race_get_dist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip().lower()

    if text in ("ultra / other", "ultra", "other"):
        context.user_data["race_dist_km"] = None
        await update.effective_message.reply_text(
            "Enter the race distance in km (e.g. <code>56</code> for Comrades):",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML",
        )
        context.user_data["awaiting_custom_dist"] = True
        return RACE_DIST

    if context.user_data.get("awaiting_custom_dist"):
        try:
            km = float(text)
            if not (1 <= km <= 500):
                raise ValueError
            context.user_data["race_dist_km"] = km
            context.user_data.pop("awaiting_custom_dist", None)
        except ValueError:
            await update.effective_message.reply_text(
                "Please enter a valid distance in km (e.g. <code>56</code>).",
                parse_mode="HTML",
            )
            return RACE_DIST
    else:
        dist_km = RACE_DIST_OPTIONS.get(text)
        if dist_km is None and text not in RACE_DIST_OPTIONS:
            keyboard = [["5k", "10k"], ["half", "marathon"], ["ultra / other"]]
            await update.effective_message.reply_text(
                "Please choose from the buttons.",
                reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
            )
            return RACE_DIST
        context.user_data["race_dist_km"]    = dist_km
        context.user_data["race_dist_label"] = text

    label = context.user_data.get("race_dist_label") or f"{context.user_data['race_dist_km']} km"
    await update.effective_message.reply_text(
        f"<b>{label.title()}</b> — what was your finish time?\n\n"
        "Enter as <code>h:mm:ss</code> or <code>mm:ss</code>\n"
        "e.g. <code>1:52:30</code> or <code>24:15</code>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    return RACE_TIME


async def race_get_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from telegram_bot.handlers.onboarding import _parse_race_time

    time_min = _parse_race_time(update.effective_message.text.strip())
    if not time_min or time_min <= 0:
        await update.effective_message.reply_text(
            "Couldn't read that time. Try <code>h:mm:ss</code> (e.g. <code>1:52:30</code>) "
            "or <code>mm:ss</code> (e.g. <code>24:15</code>).",
            parse_mode="HTML",
        )
        return RACE_TIME

    context.user_data["race_time_min"] = time_min
    dist_km = context.user_data["race_dist_km"]
    label   = context.user_data.get("race_dist_label", f"{dist_km} km")

    new_vo2x = calculate_vo2x_from_race(dist_km, time_min)

    h = int(time_min // 60)
    m = int(time_min % 60)
    s = round((time_min - int(time_min)) * 60)
    time_str = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    context.user_data["new_vo2x_preview"] = new_vo2x

    keyboard = [["✅ Yes, log it"], ["❌ Cancel"]]
    await update.effective_message.reply_text(
        f"🏁 <b>Race summary</b>\n\n"
        f"Distance:  <b>{label.title()}</b>\n"
        f"Time:      <b>{time_str}</b>\n"
        f"VO2X:      <b>{new_vo2x}</b>\n\n"
        "Log this result and update your training paces?",
        reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return RACE_CONFIRM


async def race_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    from datetime import date
    from telegram_bot.formatting import main_menu_keyboard

    text = update.effective_message.text.strip().lower()
    if "cancel" in text or "❌" in text:
        await update.effective_message.reply_text(
            "Race not logged.",
            reply_markup=ReplyKeyboardRemove(),
        )
        _clear_race(context)
        return ConversationHandler.END

    telegram_id = str(update.effective_user.id)
    dist_km  = context.user_data["race_dist_km"]
    time_min = context.user_data["race_time_min"]
    today    = date.today().isoformat()

    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(f"{API_BASE_URL}/log/race", json={
                "telegram_id":          telegram_id,
                "race_distance_km":     dist_km,
                "finish_time_minutes":  time_min,
                "race_date":            today,
                "force":                False,
            })
            r.raise_for_status()
            result = r.json()
        except Exception as e:
            await update.effective_message.reply_text(
                f"❌ <b>Could not save race.</b>\n\n<i>{e}</i>",
                reply_markup=ReplyKeyboardRemove(),
                parse_mode="HTML",
            )
            _clear_race(context)
            return ConversationHandler.END

    message = result.get("message", "")
    note    = result.get("coaching_note", "")

    lines = [f"<b>{message}</b>"]
    if note:
        lines += ["", note]

    if not result.get("vo2x_updated") and result.get("drop_points", 0) > 3:
        lines += [
            "",
            "<i>To accept this result anyway, use /lograce again and confirm.</i>",
        ]

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )
    _clear_race(context)
    return ConversationHandler.END


async def race_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_race(context)
    await update.effective_message.reply_text(
        "Race logging cancelled.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END
