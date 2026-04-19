"""
UI handler — inline button callbacks and primary views.

All three main views (Today / Plan / Dashboard) live here so they can be
triggered both from commands and from inline button callbacks without duplication.

Callback data map:
  menu       → show main menu
  today      → today's run
  plan       → this week's plan
  dashboard  → dashboard snapshot
  log        → redirect to /log command flow
  paces      → training paces
  settings   → location and run hour settings
"""
from __future__ import annotations

import asyncio
from datetime import date

import httpx
from telegram import Update
from telegram.ext import ContextTypes

import os
from telegram_bot.config import API_BASE_URL

N8N_TODAY_WEBHOOK = os.getenv("N8N_TODAY_WEBHOOK", "")
import logging
_log = logging.getLogger(__name__)

# ── Race logos (GitHub Pages) ──────────────────────────────────────────────
# Update LOGOS_BASE if the repo or branch ever changes.
# File extension: change .png → .jpg below if you saved them as JPEGs.
LOGOS_BASE = "https://sizzleguyandy.github.io/run-coach-apps/logos"

RACE_LOGO_MAP: dict[str, str] = {
    # SA logos confirmed in GitHub repo
    "cape_town_marathon":            f"{LOGOS_BASE}/logo_cape_town_marathon.jpg",
    "two_oceans_marathon":           f"{LOGOS_BASE}/logo_two_oceans.jpg",
    "comrades_marathon":             f"{LOGOS_BASE}/logo_comrades.jpg",
    # "soweto_marathon":             f"{LOGOS_BASE}/logo_soweto_marathon.jpg",          # add when uploaded
    # "durban_international_marathon": f"{LOGOS_BASE}/logo_durban_international.jpg",   # add when uploaded
    # "knysna_forest_marathon":      f"{LOGOS_BASE}/logo_knysna_forest.jpg",            # add when uploaded
    # UK logos (add as uploaded)
    # "london_marathon":             f"{LOGOS_BASE}/logo_london_marathon.jpg",
    # "manchester_marathon":         f"{LOGOS_BASE}/logo_manchester_marathon.jpg",
}
_GENERIC_LOGO = f"{LOGOS_BASE}/logo_generic.jpg"


def _race_logo_url(preset_id: str | None) -> str:
    """Return the logo URL for a race preset, falling back to the generic logo."""
    if preset_id and preset_id in RACE_LOGO_MAP:
        return RACE_LOGO_MAP[preset_id]
    return _GENERIC_LOGO

from telegram_bot.formatting import (
    format_main_menu, format_today, format_week, format_c25k_week,
    format_dashboard, format_paces, format_truepace,
    main_menu_keyboard, today_keyboard, plan_keyboard,
    dashboard_keyboard, back_keyboard,
    WEEKDAY_TO_DAY,
)


# ── Shared data fetchers ───────────────────────────────────────────────────

async def _fetch_all(telegram_id: str) -> tuple[dict | None, dict | None, dict | None]:
    """
    Fetch athlete, current week plan, and week log summary in parallel.
    Returns (athlete, week, summary) — any may be None on error.
    """
    async with httpx.AsyncClient(timeout=12) as client:
        results = await asyncio.gather(
            client.get(f"{API_BASE_URL}/athlete/{telegram_id}"),
            client.get(f"{API_BASE_URL}/plan/{telegram_id}/current"),
            return_exceptions=True,
        )

    athlete_r, week_r = results
    athlete = week_r_data = None

    if not isinstance(athlete_r, Exception) and athlete_r.status_code == 200:
        athlete = athlete_r.json()
    if not isinstance(week_r, Exception) and week_r.status_code == 200:
        week_r_data = week_r.json()

    # Fetch log summary (needs week number from week plan)
    summary = None
    if athlete and week_r_data:
        week_num = week_r_data.get("week_number", 1)
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                sr = await client.get(
                    f"{API_BASE_URL}/log/{telegram_id}/week/{week_num}/summary"
                )
                if sr.status_code == 200:
                    summary = sr.json()
        except Exception:
            pass

    return athlete, week_r_data, summary or {}


async def _fetch_weather(telegram_id: str, session_type: str = "easy") -> dict:
    """Fetch TRUEPACE adjustment. Returns empty dict on failure."""
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(
                f"{API_BASE_URL}/weather/{telegram_id}/adjustment",
                params={"session_type": session_type},
            )
            if r.status_code == 200:
                return r.json()
    except Exception:
        pass
    return {}


def _build_session_url(
    athlete: dict,
    session: dict,
    paces_dict: dict,
    week_number: int = 0,
    day_name: str = "",
) -> "str | None":
    """
    Build a session.html URL for quality sessions (threshold / interval / rep / hills / strides).
    Returns None for easy runs, long runs, and rest days — no player needed.
    Silent fail on any error.

    New params (for auto-log):
      week_number — current plan week number
      day_name    — "Mon", "Tue", etc. (today's day key)
    """
    try:
        session_name = session.get("session", "")
        km           = session.get("km", 0)
        notes        = session.get("notes", "")
        if not session_name or km == 0 or not notes:
            return None

        QUALITY_KEYS = ("Threshold", "Tempo", "Interval", "Repetition", "R-Pace", "Hill", "Stride", "Cruise")
        if not any(k in session_name for k in QUALITY_KEYS):
            return None

        from urllib.parse import urlencode
        MINI_APP_BASE = os.getenv("MINI_APP_BASE_URL", "https://sizzleguyandy.github.io/run-coach-apps").rstrip("/")
        API_BASE      = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")

        # Derive prescribed pace (min/km as float) from the primary effort zone
        def _pace_str_to_float(s: str) -> float:
            """Convert 'M:SS' pace string to float min/km."""
            try:
                parts = str(s).split(":")
                return int(parts[0]) + int(parts[1]) / 60
            except Exception:
                return 0.0

        name_lower = session_name.lower()
        if any(k in name_lower for k in ("rep", "stride", "r-pace")):
            ppace = _pace_str_to_float(paces_dict.get("repetition", ""))
        elif any(k in name_lower for k in ("interval",)):
            ppace = _pace_str_to_float(paces_dict.get("interval", ""))
        elif any(k in name_lower for k in ("threshold", "tempo", "cruise")):
            ppace = _pace_str_to_float(paces_dict.get("threshold", ""))
        else:
            ppace = _pace_str_to_float(paces_dict.get("easy", ""))

        qs = {
            "name":      athlete.get("name", "Runner"),
            "session":   session_name,
            "km":        km,
            "notes":     notes,
            "easy":      paces_dict.get("easy", ""),
            "threshold": paces_dict.get("threshold", ""),
            "interval":  paces_dict.get("interval", ""),
            "rep":       paces_dict.get("repetition", ""),
            # Auto-log params
            "tid":       athlete.get("telegram_id", ""),
            "week":      week_number,
            "day":       day_name,
            "dist":      km,
            "ppace":     round(ppace, 4) if ppace else "",
            "api":       API_BASE,
        }
        return f"{MINI_APP_BASE}/session.html?{urlencode(qs)}"
    except Exception:
        return None


def _session_type_for_week(week: dict) -> str:
    phase = week.get("phase", 1)
    today_key = WEEKDAY_TO_DAY.get(date.today().weekday(), "Mon")
    session_name = week.get("days", {}).get(today_key, {}).get("session", "")
    if any(kw in session_name for kw in ("Interval", "Cruise", "Threshold", "R-Pace", "Hill")):
        return "quality"
    if "Long" in session_name:
        return "long"
    return "easy"


def _today_logged(summary: dict) -> bool:
    today_key = WEEKDAY_TO_DAY.get(date.today().weekday(), "Mon")
    runs = summary.get("runs", [])
    return any(r.get("day") == today_key for r in runs)


def _no_profile_text() -> str:
    return "No profile found. Use /start to set up your training plan."


# ── Show main menu ─────────────────────────────────────────────────────────

async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    """
    Send or edit-in-place the main menu message.
    `edit=True` is used when responding to a callback to update the existing message.
    """
    telegram_id = str(update.effective_user.id)

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{API_BASE_URL}/athlete/{telegram_id}")
            if r.status_code != 200:
                text = _no_profile_text()
                if edit and update.callback_query:
                    await update.callback_query.edit_message_text(text)
                else:
                    await update.effective_message.reply_text(text)
                return
            athlete = r.json()
    except Exception:
        text = "⚠️ Could not reach the server. Try again shortly."
        if edit and update.callback_query:
            await update.callback_query.edit_message_text(text)
        else:
            await update.effective_message.reply_text(text)
        return

    text = format_main_menu(athlete.get("name", ""), athlete.get("plan_type", "full"))
    keyboard = main_menu_keyboard()

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            if "not modified" not in str(e).lower():
                _log.error(f"show_main_menu edit failed: {e}")
                await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


# ── Today's run ────────────────────────────────────────────────────────────

async def _fetch_coached_message(
    athlete: dict,
    week: dict,
    today_key: str,
) -> str:
    """
    Posts today's session data to the n8n AI coaching webhook.
    Returns the coached message string, or empty string on any failure.
    Silent fallback — never raises, never blocks the main today view.
    """
    if not N8N_TODAY_WEBHOOK:
        return ""

    from telegram_bot.formatting import PHASE_NAMES
    from coach_core.engine.paces import calculate_paces, format_pace

    days       = week.get("days", {})
    session    = days.get(today_key, {})
    session_km = session.get("km", 0)
    phase_num  = week.get("phase", 1)

    # Calculate weeks to race
    weeks_to_race = max(0, (week.get("total_weeks", 18) - week.get("week_number", 1)))

    # Get paces from VO2X
    vo2x  = athlete.get("vo2x", 40)
    paces = calculate_paces(vo2x)

    payload = {
        "athlete_name":     athlete.get("name", "Runner"),
        "session_name":     session.get("session", "Rest"),
        "session_km":       session_km,
        "session_notes":    session.get("notes", ""),
        "phase_num":        phase_num,
        "phase_name":       PHASE_NAMES.get(phase_num, "Training"),
        "week_num":         week.get("week_number", 1),
        "total_weeks":      week.get("total_weeks", 18),
        "weeks_to_race":    weeks_to_race,
        "race_name":        athlete.get("race_name") or "your race",
        "is_rest":          session_km == 0 or session.get("session") == "Rest",
        "total_vol_km":     week.get("planned_volume_km", 0),
        "training_profile": athlete.get("training_profile", "conservative"),
        "easy_pace":        format_pace(paces.easy_min_per_km),
        "marathon_pace":    format_pace(paces.marathon_min_per_km),
        "threshold_pace":   format_pace(paces.threshold_min_per_km),
        "interval_pace":    format_pace(paces.interval_min_per_km),
        # Instruction prevents n8n from prescribing specific strength exercises
        "coaching_instruction": (
            "Write 2-3 motivational sentences focused on today's running session and race context. "
            "If is_rest is true, acknowledge recovery importance briefly — do NOT list specific "
            "strength exercises, sets, reps, or workout names. Never prescribe a strength programme. "
            "Keep the tone like a knowledgeable running coach, not a personal trainer."
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.post(N8N_TODAY_WEBHOOK, json=payload)
            if r.status_code == 200:
                data = r.json()
                _log.info(f"n8n response keys: {list(data.keys())} | data: {str(data)[:200]}")
                return data.get("coached_message") or data.get("output") or data.get("text", "")
    except Exception as e:
        _log.warning(f"n8n call failed: {type(e).__name__}: {e}")
    return ""



async def show_today(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    telegram_id = str(update.effective_user.id)
    athlete, week, summary = await _fetch_all(telegram_id)

    if not week:
        text = _no_profile_text() if not athlete else "⚠️ Could not load your plan."
        await _send_or_edit(update, text, back_keyboard(), edit)
        return

    logged_today = _today_logged(summary)
    today_key    = WEEKDAY_TO_DAY.get(date.today().weekday(), "Mon")

    # Calculate paces dict for notification-first formatting (silent fail)
    paces_dict = None
    if athlete and week.get("plan_type") != "c25k":
        try:
            from coach_core.engine.paces import calculate_paces, format_pace
            _vo2x = athlete.get("vo2x", 40)
            _p = calculate_paces(_vo2x)
            paces_dict = {
                "easy":        format_pace(_p.easy_min_per_km),
                "threshold":   format_pace(_p.threshold_min_per_km),
                "interval":    format_pace(_p.interval_min_per_km),
                "repetition":  format_pace(_p.repetition_min_per_km),
            }
        except Exception:
            pass

    # Try AI coaching via n8n — silent fallback to standard format
    coached = ""
    if athlete and week.get("plan_type") != "c25k":
        coached = await _fetch_coached_message(athlete, week, today_key)

    if coached:
        # AI coached message — notification-first line 1, then TR3D-branded body
        from telegram_bot.formatting import PHASE_NAMES, PHASE_EMOJIS, _primary_pace_for_session, _hdr, _sec
        phase_num   = week.get("phase", 1)
        phase_name  = PHASE_NAMES.get(phase_num, "Training")
        phase_emoji = PHASE_EMOJIS.get(phase_num, "")
        week_num    = week.get("week_number", "?")
        total_vol   = week.get("planned_volume_km", 0)
        days        = week.get("days", {})
        session     = days.get(today_key, {})
        session_name = session.get("session", "")
        km          = session.get("km", 0)

        if session_name and km > 0:
            primary_pace = _primary_pace_for_session(session_name, paces_dict)
            pace_str   = f" @ {primary_pace}/km" if primary_pace else ""
            notif_line = f"🏃 <b>Today: {session_name} {km} km{pace_str}</b>"
        else:
            notif_line = f"😴 <b>Today ({today_key}): Rest Day — Week {week_num}</b>"

        text = f"{notif_line}\n\n{_hdr('TODAY')}\n{coached}"
        if logged_today:
            text += "\n\n✅ <b>Run logged for today.</b>"
        text += f"\n\n{_sec('WEEK')}\nWeek <b>{week_num}</b>  ·  {phase_emoji} {phase_name}  ·  <b>{total_vol} km</b> target"
    else:
        # Standard deterministic format — always works
        text = format_today(week, logged_today, paces=paces_dict)

    # Append TRUEPACE block
    if athlete and week.get("plan_type") != "c25k":
        session_type = _session_type_for_week(week)
        weather = await _fetch_weather(telegram_id, session_type)
        truepace = format_truepace(weather)
        if truepace:
            text = text + "\n\n" + truepace

    # Build session player URL for quality days (silent fail)
    session_url = None
    if athlete and paces_dict and week.get("plan_type") != "c25k":
        today_session = week.get("days", {}).get(today_key, {})
        session_url = _build_session_url(
            athlete, today_session, paces_dict,
            week_number=week.get("week_number", 0),
            day_name=today_key,
        )

    keyboard = today_keyboard(logged_today, session_url)
    await _send_or_edit(update, text, keyboard, edit)


# ── Weekly plan ────────────────────────────────────────────────────────────

async def show_plan(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    telegram_id = str(update.effective_user.id)

    # Use _fetch_all to get athlete, week, and log summary in one parallel call
    athlete, week, summary = await _fetch_all(telegram_id)

    if not week:
        text = _no_profile_text() if not athlete else "⚠️ Could not load your plan."
        await _send_or_edit(update, text, back_keyboard(), edit)
        return

    plan_type = week.get("plan_type", "full")
    text = format_c25k_week(week) if plan_type == "c25k" else format_week(week, summary or {})

    await _send_or_edit(update, text, plan_keyboard(), edit)


# ── Dashboard ──────────────────────────────────────────────────────────────

async def _send_dashboard_photo(
    update,
    photo_url: str,
    caption: str,
    keyboard,
    edit: bool,
) -> None:
    """
    Send (or edit-to) a photo with caption and inline keyboard.

    When edit=True (inline button callback), attempts edit_message_media so
    the message updates in place. Falls back to sending a new photo, and if
    that also fails (e.g. photo URL unreachable), falls back to plain text.
    """
    from telegram import InputMediaPhoto

    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_media(
                media=InputMediaPhoto(
                    media=photo_url,
                    caption=caption,
                    parse_mode="HTML",
                ),
                reply_markup=keyboard,
            )
            return
        except Exception as e:
            _log.warning(f"_send_dashboard_photo edit_message_media failed: {e}")
            # Fall through to send a new photo message below

    try:
        await update.effective_message.reply_photo(
            photo=photo_url,
            caption=caption,
            reply_markup=keyboard,
            parse_mode="HTML",
        )
    except Exception as e:
        _log.error(f"_send_dashboard_photo reply_photo failed: {e} — falling back to text")
        await _send_or_edit(update, caption, keyboard, edit)


async def show_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    telegram_id = str(update.effective_user.id)
    athlete, week, summary = await _fetch_all(telegram_id)

    if not athlete:
        await _send_or_edit(update, _no_profile_text(), back_keyboard(), edit)
        return

    if not week:
        await _send_or_edit(update, "⚠️ Could not load plan data.", back_keyboard(), edit)
        return

    # Generate race prediction using the full volume-aware predictor
    prediction = None
    try:
        from datetime import date as _date
        from coach_core.engine.predictor import predict, PredictionInput, PRESET_HILL_FACTORS
        from coach_core.engine.race_presets import RACE_PRESETS
        from coach_core.engine.paces import (
            RacePrediction, _comrades_direction, _comrades_medal
        )

        vo2x          = athlete.get("vo2x")
        race_distance = athlete.get("race_distance", "")
        race_date_str = str(athlete.get("race_date") or "")
        preset_id     = athlete.get("preset_race_id")
        hilliness     = athlete.get("race_hilliness", "low")
        weekly_km     = float(athlete.get("current_weekly_mileage") or 30.0)

        if vo2x and race_distance and race_date_str:
            _DIST_MAP = {
                "5k": 5.0, "10k": 10.0, "half": 21.0975,
                "marathon": 42.195, "ultra_56": 56.0, "ultra_90": 90.0,
            }
            _HILL_MAP = {
                "low": 0.045, "medium": 0.08, "high": 0.115, "mountain": 0.15,
            }
            dist_km     = RACE_PRESETS[preset_id]["exact_distance_km"] if preset_id and preset_id in RACE_PRESETS else _DIST_MAP.get(race_distance, 42.195)
            hill_factor = PRESET_HILL_FACTORS.get(preset_id) if preset_id else _HILL_MAP.get(hilliness, 0.045)
            race_date_obj = _date.fromisoformat(race_date_str) if race_date_str else _date.today()

            # Map the DB training_profile (Daniels vocabulary) to the
            # predictor's plan_type vocabulary before calling predict().
            # DB stores "aggressive" when the athlete chose a "balanced" plan;
            # the predictor expects "balanced" | "conservative" | "injury_prone".
            _PROFILE_TO_PLAN_TYPE = {
                "aggressive":   "balanced",
                "conservative": "conservative",
                "injury_prone": "injury_prone",
            }
            raw_profile  = athlete.get("training_profile") or "conservative"
            pred_plan_type = _PROFILE_TO_PLAN_TYPE.get(raw_profile, "conservative")

            result = predict(PredictionInput(
                race_name         = athlete.get("race_name") or race_distance,
                race_distance_km  = dist_km,
                hill_factor       = hill_factor,
                race_date         = race_date_obj,
                direct_vo2x       = float(vo2x),
                weekly_mileage_km = weekly_km,
                longest_run_km    = weekly_km * 0.4,
                plan_type         = pred_plan_type,
            ))

            # Build a RacePrediction so format_prediction() works unchanged
            medal = None
            if race_distance in ("ultra_90", "comrades"):
                direction  = _comrades_direction(race_date_str)
                dir_label  = "↑ Up Run" if direction == "up" else "↓ Down Run"
                medal_low  = _comrades_medal(result.low_minutes)
                medal_high = _comrades_medal(result.high_minutes)
                medal      = medal_low if medal_low == medal_high else f"{medal_low} / {medal_high}"
                dist_label = f"Comrades {dir_label}"
            elif race_distance in ("ultra_56", "two_oceans"):
                dist_label = "Two Oceans (56km)"
            else:
                dist_label = athlete.get("race_name") or race_distance.replace("_", " ").title()

            prediction = RacePrediction(
                distance_label = dist_label,
                low_minutes    = result.low_minutes,
                high_minutes   = result.high_minutes,
                medal          = medal,
            )
    except Exception:
        pass  # never block dashboard on prediction failure

    caption = format_dashboard(athlete, week, summary, prediction)
    logo_url = _race_logo_url(athlete.get("preset_race_id"))
    await _send_dashboard_photo(update, logo_url, caption, dashboard_keyboard(), edit)


# ── Paces ──────────────────────────────────────────────────────────────────

async def show_paces(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    telegram_id = str(update.effective_user.id)

    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{API_BASE_URL}/athlete/{telegram_id}/paces")
            if r.status_code == 404:
                await _send_or_edit(update, _no_profile_text(), back_keyboard(), edit)
                return
            if r.status_code == 400:
                await _send_or_edit(
                    update,
                    "No VO2X yet — complete C25K and log a time trial to unlock paces.",
                    back_keyboard(), edit,
                )
                return
            r.raise_for_status()
            paces = r.json()
    except Exception as e:
        await _send_or_edit(update, f"⚠️ Could not load paces: {e}", back_keyboard(), edit)
        return

    # Fetch TRUEPACE and pass into format_paces for integrated two-column table
    weather = await _fetch_weather(telegram_id, "easy")
    text = format_paces(paces, weather)

    await _send_or_edit(update, text, back_keyboard(), edit, parse_mode="HTML")


# ── Settings ───────────────────────────────────────────────────────────────

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE, edit: bool = False) -> None:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    text = (
        "⚙️ <b>Settings</b>\n\n"
        "📍 <b>Location &amp; TRUEPACE</b>\n"
        "Use /location to set your city for weather-adjusted paces.\n"
        "Example: <code>/location Cape Town</code> or <code>/location Cape Town 6</code> (6 AM runs)\n\n"
        "🗓️ <b>Training days</b>\n"
        "Change which days you do your long run, quality session, and easy runs.\n\n"
        "🔄 <b>Reset profile</b>\n"
        "Use /reset to delete your profile and start over."
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗓️ Change training days", callback_data="change_training_days")],
        [InlineKeyboardButton("📌 Anchor runs",          callback_data="anchor_menu")],
        [InlineKeyboardButton("← Menu",                  callback_data="menu")],
    ])
    await _send_or_edit(update, text, keyboard, edit, parse_mode="HTML")


# ── Callback dispatcher ────────────────────────────────────────────────────

async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all inline button callbacks."""
    query = update.callback_query
    await query.answer()   # dismiss loading spinner

    data = query.data

    # V2 onboarding confirmation callbacks
    if data == "v2_confirm":
        from telegram_bot.handlers.onboarding_v2 import v2_confirm_callback
        await v2_confirm_callback(update, context)
        return
    if data == "v2_restart":
        from telegram_bot.handlers.onboarding_v2 import v2_restart_callback
        await v2_restart_callback(update, context)
        return

    if data == "menu":
        await show_main_menu(update, context, edit=True)
    elif data == "today":
        await show_today(update, context, edit=True)
    elif data == "plan":
        await show_plan(update, context, edit=True)
    elif data == "dashboard":
        await show_dashboard(update, context, edit=True)
    elif data == "paces":
        await show_paces(update, context, edit=True)
    elif data == "settings":
        await show_settings(update, context, edit=True)
    elif data == "change_training_days":
        from telegram_bot.handlers.training_days import start_change_days
        await start_change_days(update, context)
    elif data in ("anchor_menu", "anchor_add", "anchor_clear") or data.startswith("anchor_day_") or data.startswith("anchor_km_"):
        from telegram_bot.handlers.anchor import (
            anchor_menu, anchor_add_start, anchor_clear,
            anchor_day_selected, anchor_km_selected,
        )
        if data == "anchor_menu":
            await anchor_menu(update, context)
        elif data == "anchor_add":
            await anchor_add_start(update, context)
        elif data == "anchor_clear":
            await anchor_clear(update, context)
        elif data.startswith("anchor_day_"):
            await anchor_day_selected(update, context)
        elif data.startswith("anchor_km_"):
            await anchor_km_selected(update, context)
    elif data == "calendar_ics":
        await show_calendar_ics(update, context)


# ── Calendar ICS ───────────────────────────────────────────────────────────

async def show_calendar_ics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Generate and send the week's .ics file so the user can import it into
    their phone calendar.  Triggered by the 📲 Add to Calendar button.
    """
    telegram_id = str(update.effective_user.id)
    athlete, week, _ = await _fetch_all(telegram_id)

    if not athlete or not week:
        await update.effective_message.reply_text(
            "⚠️ Could not load your plan. Try again shortly."
        )
        return

    # Build paces dict (same logic as show_today / show_plan)
    paces_dict = None
    if week.get("plan_type") != "c25k":
        try:
            from coach_core.engine.paces import calculate_paces, format_pace
            _p = calculate_paces(athlete.get("vo2x", 40))
            paces_dict = {
                "easy":       format_pace(_p.easy_min_per_km),
                "threshold":  format_pace(_p.threshold_min_per_km),
                "interval":   format_pace(_p.interval_min_per_km),
                "rep":        format_pace(_p.repetition_min_per_km),
            }
        except Exception:
            pass

    # Build a session URL factory for quality sessions
    def _url_builder(session: dict) -> "str | None":
        if paces_dict:
            return _build_session_url(athlete, session, paces_dict)
        return None

    # Generate ICS bytes
    from telegram_bot.ics_generator import generate_week_ics
    try:
        ics_bytes = generate_week_ics(
            week       = week,
            athlete    = athlete,
            paces_dict = paces_dict,
            session_url_builder = _url_builder,
        )
    except Exception as e:
        _log.exception(f"ICS generation failed for {telegram_id}: {e}")
        await update.effective_message.reply_text(
            "⚠️ Could not generate calendar file. Please try again."
        )
        return

    from io import BytesIO
    week_num = week.get("week_number", 1)
    filename = f"tr3d_week_{week_num}.ics"

    caption = (
        f"📲 <b>Week {week_num} · Add to Calendar</b>\n\n"
        "Tap the file to import your training sessions into your phone calendar.\n\n"
        "<i>iOS: tap → Add All · Android: tap → import into Google Calendar</i>"
    )

    await update.effective_message.reply_document(
        document = BytesIO(ics_bytes),
        filename = filename,
        caption  = caption,
        parse_mode = "HTML",
    )


# ── Command entry points ──────────────────────────────────────────────────

async def cmd_menu(update, context) -> None:
    await show_main_menu(update, context, edit=False)

async def cmd_today(update, context) -> None:
    await show_today(update, context, edit=False)

async def cmd_dashboard(update, context) -> None:
    await show_dashboard(update, context, edit=False)


# ── Helper ─────────────────────────────────────────────────────────────────

async def _send_or_edit(update, text: str, keyboard, edit: bool, parse_mode: str = "HTML") -> None:
    """Send new message or edit existing one depending on context."""
    if edit and update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text, reply_markup=keyboard, parse_mode=parse_mode,
            )
        except Exception as e:
            if "not modified" not in str(e).lower():
                _log.error(f"_send_or_edit edit failed: {type(e).__name__}: {e}")
                try:
                    await update.effective_message.reply_text(
                        text, reply_markup=keyboard, parse_mode=parse_mode,
                    )
                except Exception as e2:
                    _log.error(f"_send_or_edit fallback also failed: {e2}")
    else:
        try:
            await update.effective_message.reply_text(
                text, reply_markup=keyboard, parse_mode=parse_mode,
            )
        except Exception as e:
            _log.error(f"_send_or_edit reply failed: {type(e).__name__}: {e}")
