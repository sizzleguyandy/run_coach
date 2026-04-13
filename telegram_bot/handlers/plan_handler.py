"""
Plan command handlers.
All primary views (today/plan/dashboard) delegate to ui.py so that
command and inline-button paths share identical logic.
"""
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import ContextTypes

from telegram_bot.config import API_BASE_URL
from telegram_bot.handlers.ui import show_plan, show_paces, show_today, show_dashboard


# ── Primary view commands ──────────────────────────────────────────────────

async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_plan(update, context, edit=False)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_today(update, context, edit=False)


async def cmd_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_dashboard(update, context, edit=False)


async def cmd_paces(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await show_paces(update, context, edit=False)


# ── Location command ───────────────────────────────────────────────────────

async def cmd_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Set athlete location for TRUEPACE.

    Flows:
      /location                      → show SA city keyboard
      /location <city name>          → resolve by name (e.g. /location Cape Town)
      /location <lat> <lon> [hour]   → manual coordinates
      (Telegram location share)      → use GPS coordinates
    """
    from coach_core.engine.sa_cities import find_city, city_keyboard_rows

    telegram_id = str(update.effective_user.id)
    args = context.args or []

    lat = lon = hour = None

    if update.message.location:
        lat  = update.message.location.latitude
        lon  = update.message.location.longitude
        hour = 7
        city_name = f"{lat:.4f}, {lon:.4f}"

    elif len(args) >= 2 and _is_number(args[0]):
        try:
            lat  = float(args[0])
            lon  = float(args[1])
            hour = int(args[2]) if len(args) >= 3 else 7
            city_name = f"{lat:.4f}, {lon:.4f}"
        except ValueError:
            await update.message.reply_text(
                "Usage: /location LAT LON HOUR\n"
                "Example: /location -33.9249 18.4241 6",
            )
            return

    elif args:
        # City name + optional hour: /location Cape Town 6
        # Last arg is hour if it's a number
        hour = 7
        name_parts = args[:]
        if args and _is_number(args[-1]) and int(float(args[-1])) in range(24):
            hour = int(float(args[-1]))
            name_parts = args[:-1]
        query = " ".join(name_parts)
        city = find_city(query)
        if not city:
            await update.message.reply_text(
                "City not found. Try a name like Cape Town or Joburg,\n"
                "or use /location to see all options.",
            )
            return
        lat       = city.latitude
        lon       = city.longitude
        city_name = f"{city.name} ({city.province})"

    else:
        rows = city_keyboard_rows(cols=2)
        await update.message.reply_text(
            "📍 <b>Where are you based?</b>\n\n"
            "Select your nearest city below, or send a city name.\n\n"
            "You can also type: /location Cape Town\n"
            "Or enter coordinates: /location -33.92 18.42",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
        context.user_data["awaiting_city"] = True
        return

    await _save_location(update, telegram_id, lat, lon, hour, city_name)


async def handle_city_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle city name typed or selected from keyboard after /location.
    Only fires when awaiting_city flag is set in user_data."""
    if not context.user_data.get("awaiting_city"):
        return  # not waiting for a city - ignore

    from coach_core.engine.sa_cities import find_city
    context.user_data.pop("awaiting_city", None)
    telegram_id = str(update.effective_user.id)
    query = update.message.text.strip()

    city = find_city(query)
    if not city:
        await update.message.reply_text("City not recognised. Try /location again.")
        return

    await _save_location(
        update, telegram_id,
        city.latitude, city.longitude, 7,
        f"{city.name} ({city.province})",
    )


def _is_number(s: str) -> bool:
    try: float(s); return True
    except ValueError: return False


async def _save_location(update, telegram_id, lat, lon, hour, display_name):
    from telegram_bot.formatting import back_keyboard
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.patch(
                f"{API_BASE_URL}/athlete/{telegram_id}/location",
                json={"latitude": lat, "longitude": lon, "run_hour": hour},
            )
            r.raise_for_status()
        except Exception as e:
            await update.message.reply_text(
                f"❌ Could not save location: {e}",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

    await update.message.reply_text(
        f"📍 Location set: <b>{display_name}</b>\n"
        f"Run hour: <b>{hour}:00</b>\n\n"
        "TRUEPACE will now adjust your paces based on weather when you view your plan.",
        reply_markup=back_keyboard(),
        parse_mode="HTML",
    )
