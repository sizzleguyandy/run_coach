"""
Onboarding V2 — Race-first, prediction-driven.

Flow:
  /start -> NAME -> RACE_SELECT
    -> (preset) -> EXPERIENCE
    -> (custom)  -> CUSTOM_DIST -> CUSTOM_HILLS -> CUSTOM_DATE -> EXPERIENCE

  EXPERIENCE:
    -> "Yes, recent race"  -> RECENT_DIST -> RECENT_TIME -> WEEKLY_KM
    -> "No, beginner"      -> BEGINNER_ABILITY            -> WEEKLY_KM
    -> "I know my VO2X"    -> VO2X_INPUT                  -> WEEKLY_KM

  WEEKLY_KM -> LONGEST_RUN -> PLAN_TYPE
           -> LONG_RUN_DAY -> QUALITY_DAY -> EASY_DAYS
           -> LOCATION -> (prediction shown + profile created)

Rules (project-wide):
  - parse_mode="HTML" everywhere
  - use update.effective_message, not update.message
  - never call _pad() / pad_message()
"""
from __future__ import annotations

import html
import json
import os
import httpx
from datetime import date, datetime
from typing import Optional

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes, ConversationHandler

from telegram_bot.config import API_BASE_URL
from telegram_bot.formatting import format_main_menu, main_menu_keyboard, _DIV
from coach_core.engine.race_presets import (
    RACE_PRESETS, preset_keyboard_rows, find_preset_by_label, get_next_race_date,
)
from coach_core.engine.race_presets_sa import (
    RACE_PRESETS_SA, RACE_COORDS_SA, get_next_race_date_sa,
)
from coach_core.engine.race_presets_uk import (
    RACE_PRESETS_UK, RACE_COORDS_UK, get_next_race_date_uk,
)
from coach_core.engine.sa_cities import city_keyboard_rows, find_city
from coach_core.engine.predictor import (
    predict, PredictionInput,
    PRESET_HILL_FACTORS, HILL_PROFILES, PLAN_TYPE_TO_PROFILE,
    BEGINNER_5K_TIMES, fmt_time, km_to_race_distance,
)
from coach_core.engine.adaptation import calculate_vo2x_from_race

# ── Conversation states ────────────────────────────────────────────────────
(
    COUNTRY,          # 0  — 🇿🇦 South Africa | 🇬🇧 United Kingdom
    NAME,             # 1
    RACE_SELECT,      # 2
    CUSTOM_DIST,      # 3
    CUSTOM_HILLS,     # 4
    CUSTOM_DATE,      # 5
    EXPERIENCE,       # 6
    RECENT_DIST,      # 7
    RECENT_TIME,      # 8
    BEGINNER_ABILITY, # 9
    WEEKLY_KM,        # 10
    LONGEST_RUN,      # 11
    PLAN_TYPE,        # 12
    LOCATION,         # 13
    VO2X_INPUT,       # 14
    LONG_RUN_DAY,     # 15
    QUALITY_DAY,      # 16
    EASY_DAYS,        # 17  — first easy day button selection
    EASY_DAY_2,       # 18  — second easy day button selection
    ANCHOR_QUESTION,  # 19  — do you run with a club/group?
    ANCHOR_KM,        # 20  — how far is the group run?
) = range(21)

# ── Country helpers ────────────────────────────────────────────────────────

COUNTRY_OPTIONS = {
    "🇿🇦 South Africa": "sa",
    "🇬🇧 United Kingdom": "uk",
}

def _country_presets(country: str) -> dict:
    return RACE_PRESETS_UK if country == "uk" else RACE_PRESETS_SA

def _country_keyboard_rows(country: str) -> list[list[str]]:
    presets = _country_presets(country)
    labels = [p["display_name"] for p in presets.values()]
    rows = [labels[i:i+2] for i in range(0, len(labels), 2)]
    rows.append(["Other race — I will enter details"])
    return rows

def _find_preset_by_label_country(text: str, country: str):
    presets = _country_presets(country)
    for pid, p in presets.items():
        if p["display_name"].lower() == text.lower():
            return pid
    return None

def _get_next_race_date_country(preset_id: str, country: str) -> str:
    if country == "uk":
        return get_next_race_date_uk(preset_id)
    return get_next_race_date_sa(preset_id)

# ── Constants ──────────────────────────────────────────────────────────────

TOTAL_STEPS = 13  # shown in progress indicator (added anchor step)

RECENT_DIST_OPTIONS = {
    "5km":            5.0,
    "10km":           10.0,
    "Half marathon":  21.0975,
    "Marathon":       42.195,
}

HILL_DISPLAY = [
    "Flat (little or no hills)",
    "Rolling (gentle hills)",
    "Hilly (significant climbs)",
    "Mountain (very hilly)",
]

HILL_KEY_MAP = {
    "Flat (little or no hills)": "flat",
    "Rolling (gentle hills)":    "rolling",
    "Hilly (significant climbs)": "hilly",
    "Mountain (very hilly)":     "mountain",
}

PLAN_DISPLAY = {
    "Balanced":     "balanced",
    "Conservative": "conservative",
    "Injury Prone": "injury_prone",
}

# ── Training day options ───────────────────────────────────────────────────

ALL_DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# Days that make sense for a long run (typically weekends or mid-week days off)
LONG_RUN_DAY_OPTIONS = ["Sat", "Sun", "Fri", "Thu"]
LONG_RUN_DAY_LABELS = {
    "Saturday": "Sat",
    "Sunday":   "Sun",
    "Friday":   "Fri",
    "Thursday": "Thu",
}

# Quality/hard session options (avoid day before or after long run where possible)
QUALITY_DAY_LABELS = {
    "Tuesday":   "Tue",
    "Wednesday": "Wed",
    "Thursday":  "Thu",
    "Monday":    "Mon",
}

# Easy run day multi-select — shown as comma-separated button options
EASY_DAY_LABELS = {
    "Monday":    "Mon",
    "Tuesday":   "Tue",
    "Wednesday": "Wed",
    "Thursday":  "Thu",
    "Friday":    "Fri",
    "Saturday":  "Sat",
    "Sunday":    "Sun",
}

BEGINNER_DISPLAY = {
    "I mostly walk, with some running":  "couch",
    "I can run 5km (slowly)":            "run5k_slow",
    "I completed Couch to 5K":           "finished_c25k",
    "I can run 10km comfortably":        "run10k",
}

# Button options for weekly mileage — label → km value
WEEKLY_KM_OPTIONS: dict[str, float] = {
    "Not running yet":  0.0,
    "Under 15 km/wk":  10.0,
    "15–30 km/wk":     22.0,
    "30–50 km/wk":     40.0,
    "50–70 km/wk":     60.0,
    "70+ km/wk":       80.0,
}

# Button options for longest recent run — label → km value
LONGEST_RUN_OPTIONS: dict[str, float] = {
    "Under 5 km":  3.0,
    "5–10 km":     7.0,
    "10–16 km":    13.0,
    "16–21 km":    18.0,
    "21–32 km":    26.0,
    "32+ km":      38.0,
}

# Common race distances for custom race entry — label → km value (None = ask for text)
CUSTOM_DIST_OPTIONS: dict[str, float | None] = {
    "10 km":              10.0,
    "21.1 km (Half)":     21.1,
    "42.2 km (Marathon)": 42.2,
    "50 km":              50.0,
    "Other distance":     None,   # prompts text input
}


# ── Helpers ────────────────────────────────────────────────────────────────

def _e(s) -> str:
    """HTML-escape user-provided strings."""
    return html.escape(str(s)) if s else ""


def _step(n: int, label: str) -> str:
    """
    TR3D branded progress block — appears at top of every onboarding message.

    Renders as:
      TR3D  ·  ATHLETE PROFILE
      ──────────────────────────
      [▓▓▓▓░░░░░░]  44%  ·  Step 4 of 9
      ──────────────────────────
      <label>
    """
    filled = round((n / TOTAL_STEPS) * 10)
    bar    = "▓" * filled + "░" * (10 - filled)
    pct    = round((n / TOTAL_STEPS) * 100)
    return (
        f"<b>TR3D  ·  ATHLETE PROFILE</b>\n{_DIV}\n"
        f"[<code>{bar}</code>]  <b>{pct}%  ·  Step {n} of {TOTAL_STEPS}</b>\n"
        f"{_DIV}\n\n"
        f"<b>{label}</b>\n"
    )


def _parse_time(text: str) -> Optional[float]:
    """
    Parse a race time into float minutes.

    Formats accepted:
      h:mm:ss  → always hours:minutes:seconds  (e.g. 1:52:30 → 112.5 min)
      h:mm     → treated as hours:minutes when first part ≤ 12
                 (e.g. 2:05 → 125 min, 9:45 → 585 min for Comrades)
      mm:ss    → treated as minutes:seconds when first part > 12
                 (e.g. 24:30 → 24.5 min for a 5K)

    This matches how South African runners naturally write race times.
    """
    text = text.strip().replace(".", ":")  # allow "1.52.30" style
    parts = text.split(":")
    try:
        if len(parts) == 3:
            # Always h:mm:ss
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
            if 0 <= m < 60 and 0 <= s < 60:
                return h * 60 + m + s / 60
        elif len(parts) == 2:
            a, b = int(parts[0]), int(parts[1])
            if 0 <= b < 60:
                if a <= 12:
                    # e.g. "2:05" → 2 hours 5 min (race time convention)
                    return a * 60 + b
                else:
                    # e.g. "24:30" → 24 min 30 sec
                    return a + b / 60
    except (ValueError, IndexError):
        pass
    return None


def _parse_date(text: str) -> Optional[date]:
    """Parse dd/mm/yyyy or yyyy-mm-dd."""
    text = text.strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _ud(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Shortcut to context.user_data."""
    return context.user_data


def _clear_v2(context: ContextTypes.DEFAULT_TYPE) -> None:
    keys = [k for k in context.user_data if k.startswith("v2_")]
    for k in keys:
        del context.user_data[k]


# ── Entry point ────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for /start. Routes returning users to menu, new users to onboarding."""
    telegram_id = str(update.effective_user.id)

    # Check for existing profile
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(f"{API_BASE_URL}/athlete/{telegram_id}")
        if r.status_code == 200:
            athlete = r.json()
            text = format_main_menu(athlete.get("name", ""), athlete.get("plan_type", "full"))
            await update.effective_message.reply_text(
                text,
                reply_markup=main_menu_keyboard(),
                parse_mode="HTML",
            )
            return ConversationHandler.END
    except Exception:
        pass

    # New user — start onboarding
    _clear_v2(context)
    _TR3D_LOGO = "https://sizzleguyandy.github.io/run-coach-apps/logos/logo_tr3d_v2.jpg"
    _welcome_caption = (
        f"<b>TR3D  ·  ATHLETE PROFILE</b>\n{_DIV}\n\n"
        "Welcome. TR3D calculates a race-specific training plan built around "
        "your exact event, your current fitness, and the time you have.\n\n"
        "<i>Science-Built. Race-Ready.</i>\n\n"
        f"{_DIV}\n\n"
        "<b>Choose your country to get started.</b>"
    )
    country_rows = [[k] for k in COUNTRY_OPTIONS.keys()]
    try:
        await update.effective_message.reply_photo(
            photo=_TR3D_LOGO,
            caption=_welcome_caption,
            reply_markup=ReplyKeyboardMarkup(country_rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
    except Exception:
        await update.effective_message.reply_text(
            _welcome_caption,
            reply_markup=ReplyKeyboardMarkup(country_rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
    return COUNTRY


# ── Step 0: Country ────────────────────────────────────────────────────────

async def get_country(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    country = COUNTRY_OPTIONS.get(text)
    if not country:
        rows = [[k] for k in COUNTRY_OPTIONS.keys()]
        await update.effective_message.reply_text(
            "Please select your country using the buttons below.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return COUNTRY

    _ud(context)["v2_country"] = country
    flag = "🇿🇦" if country == "sa" else "🇬🇧"
    await update.effective_message.reply_text(
        f"{flag} Got it — loading <b>{'South Africa' if country == 'sa' else 'United Kingdom'}</b> races.\n\n"
        "<b>What's your first name?</b>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    return NAME


# ── Step 1: Name ───────────────────────────────────────────────────────────

async def get_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.effective_message.text.strip()
    if not name or len(name) > 50:
        await update.effective_message.reply_text(
            "Please enter your first name (up to 50 characters)."
        )
        return NAME

    _ud(context)["v2_name"] = name

    country = _ud(context).get("v2_country", "sa")
    rows = _country_keyboard_rows(country)
    country_label = "United Kingdom" if country == "uk" else "South Africa"
    await update.effective_message.reply_text(
        _step(2, "Your target race")
        + f"Great, <b>{_e(name)}</b>! Which race are you training for?\n\n"
        f"Select a race from <b>{country_label}</b>, or choose <b>Other race</b> to enter a custom one.",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return RACE_SELECT


# ── Step 2: Race selection ─────────────────────────────────────────────────

async def get_race(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    ud = _ud(context)

    if "Other race" in text or "other" in text.lower():
        # Custom race path — show common distances as buttons
        dist_rows = [
            [k for k in list(CUSTOM_DIST_OPTIONS.keys())[:2]],
            [k for k in list(CUSTOM_DIST_OPTIONS.keys())[2:4]],
            [list(CUSTOM_DIST_OPTIONS.keys())[4]],
        ]
        await update.effective_message.reply_text(
            _step(2, "Custom race — distance")
            + "What distance is your race?",
            reply_markup=ReplyKeyboardMarkup(dist_rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
        return CUSTOM_DIST

    # Try to match a preset using the user's country
    country = ud.get("v2_country", "sa")
    preset_id = _find_preset_by_label_country(text, country)
    if not preset_id:
        rows = _country_keyboard_rows(country)
        await update.effective_message.reply_text(
            "Please choose a race from the buttons below.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return RACE_SELECT

    presets = _country_presets(country)
    preset = presets[preset_id]
    hill_factor = PRESET_HILL_FACTORS.get(preset_id, 0.05)
    race_date_str = _get_next_race_date_country(preset_id, country)
    race_date = date.fromisoformat(race_date_str)

    ud["v2_preset_id"]        = preset_id
    ud["v2_race_name"]        = preset["display_name"]
    ud["v2_race_distance_km"] = preset["exact_distance_km"]
    ud["v2_race_distance"]    = preset["race_distance"]
    ud["v2_hill_factor"]      = hill_factor
    ud["v2_race_hilliness"]   = preset.get("hilliness", "low")
    ud["v2_race_date"]        = race_date_str

    await update.effective_message.reply_text(
        _step(3, "Your running experience")
        + f"<b>{_e(preset['display_name'])}</b> — great choice!\n\n"
        f"{_e(preset.get('description', ''))}\n\n"
        "Have you completed a race in the last 12 months and have a finish time?",
        reply_markup=ReplyKeyboardMarkup(
            [["Yes, I have a recent race time"], ["No, I am a beginner / returning runner"], ["I know my VO2X number"]],
            one_time_keyboard=True, resize_keyboard=True,
        ),
        parse_mode="HTML",
    )
    return EXPERIENCE


# ── Custom race: distance ──────────────────────────────────────────────────

async def get_custom_dist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()

    # Check button options first
    km: float | None = CUSTOM_DIST_OPTIONS.get(text)

    if km is None and text == "Other distance":
        # User wants to type a custom distance
        await update.effective_message.reply_text(
            "Enter your race distance in km (e.g. <code>56</code> for Comrades).",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML",
        )
        return CUSTOM_DIST

    if km is None:
        # Try parsing typed number
        try:
            km = float(text.replace("km", "").replace("KM", "").strip())
            if not (5 <= km <= 200):
                raise ValueError
        except ValueError:
            dist_rows = [
                [k for k in list(CUSTOM_DIST_OPTIONS.keys())[:2]],
                [k for k in list(CUSTOM_DIST_OPTIONS.keys())[2:4]],
                [list(CUSTOM_DIST_OPTIONS.keys())[4]],
            ]
            await update.effective_message.reply_text(
                "Please choose a distance or enter a number between 5 and 200 km.",
                reply_markup=ReplyKeyboardMarkup(dist_rows, one_time_keyboard=True, resize_keyboard=True),
            )
            return CUSTOM_DIST

    _ud(context)["v2_race_distance_km"] = km
    _ud(context)["v2_race_distance"]    = km_to_race_distance(km)

    await update.effective_message.reply_text(
        _step(2, "Custom race — terrain")
        + "What is the terrain like?",
        reply_markup=ReplyKeyboardMarkup(
            [HILL_DISPLAY[:2], HILL_DISPLAY[2:]],
            one_time_keyboard=True, resize_keyboard=True,
        ),
        parse_mode="HTML",
    )
    return CUSTOM_HILLS


# ── Custom race: hill profile ──────────────────────────────────────────────

async def get_custom_hills(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    key = HILL_KEY_MAP.get(text)
    if not key:
        await update.effective_message.reply_text(
            "Please choose a terrain type from the buttons.",
            reply_markup=ReplyKeyboardMarkup(
                [HILL_DISPLAY[:2], HILL_DISPLAY[2:]], one_time_keyboard=True, resize_keyboard=True,
            ),
        )
        return CUSTOM_HILLS

    profile = HILL_PROFILES[key]
    _ud(context)["v2_hill_factor"]    = profile["hill_factor"]
    _ud(context)["v2_race_hilliness"] = profile["race_hilliness"]

    await update.effective_message.reply_text(
        _step(2, "Custom race — date")
        + "When is the race? Enter the date as <code>dd/mm/yyyy</code>\n"
        "Example: <code>15/04/2026</code>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    return CUSTOM_DATE


# ── Custom race: date ──────────────────────────────────────────────────────

async def get_custom_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    race_date = _parse_date(text)
    if not race_date or race_date <= date.today():
        await update.effective_message.reply_text(
            "Please enter a future date as <code>dd/mm/yyyy</code> (e.g. <code>15/04/2027</code>).",
            parse_mode="HTML",
        )
        return CUSTOM_DATE

    ud = _ud(context)
    ud["v2_race_date"] = race_date.isoformat()
    ud["v2_race_name"] = f"Custom {ud['v2_race_distance_km']:.0f}km race"
    ud["v2_preset_id"] = None

    await update.effective_message.reply_text(
        _step(3, "Your running experience")
        + "Have you completed a race in the last 12 months and have a finish time?",
        reply_markup=ReplyKeyboardMarkup(
            [["Yes, I have a recent race time"], ["No, I am a beginner / returning runner"], ["I know my VO2X number"]],
            one_time_keyboard=True, resize_keyboard=True,
        ),
        parse_mode="HTML",
    )
    return EXPERIENCE


# ── Step 3: Experience ─────────────────────────────────────────────────────

async def get_experience(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip().lower()

    if "vo2x" in text:
        await update.effective_message.reply_text(
            _step(4, "Your VO2X")
            + "Enter your VO2X number.\n\n"
            "<b>VO2X — Velocity–Oxygen Performance Index</b>\n"
            "A single number that measures how efficiently you convert "
            "oxygen into speed. It combines aerobic capacity, pace, and "
            "durability into one practical score.\n\n"
            "Typical range: <b>30</b> (beginner) → <b>75</b> (elite)\n\n"
            "Example: <code>48</code> or <code>52.5</code>",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML",
        )
        return VO2X_INPUT

    if "yes" in text or "recent" in text:
        _ud(context)["v2_has_recent_race"] = True
        rows = [list(RECENT_DIST_OPTIONS.keys())[:2], list(RECENT_DIST_OPTIONS.keys())[2:]]
        await update.effective_message.reply_text(
            _step(4, "Recent race distance")
            + "What distance was your most recent race?",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
        return RECENT_DIST

    else:
        _ud(context)["v2_has_recent_race"] = False
        rows = [[k] for k in BEGINNER_DISPLAY.keys()]
        await update.effective_message.reply_text(
            _step(4, "Your current running level")
            + "How would you describe your running right now?",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
        return BEGINNER_ABILITY


# ── Recent race: distance ──────────────────────────────────────────────────

async def get_recent_dist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    dist = RECENT_DIST_OPTIONS.get(text)
    if dist is None:
        rows = [list(RECENT_DIST_OPTIONS.keys())[:2], list(RECENT_DIST_OPTIONS.keys())[2:]]
        await update.effective_message.reply_text(
            "Please choose a distance from the buttons.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return RECENT_DIST

    _ud(context)["v2_recent_dist"] = dist
    await update.effective_message.reply_text(
        _step(5, "Recent race time")
        + f"What was your finish time for the <b>{_e(text)}</b>?\n\n"
        "Enter as <code>h:mm:ss</code> or <code>mm:ss</code>\n"
        "Examples: <code>1:52:30</code> or <code>24:15</code>",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode="HTML",
    )
    return RECENT_TIME


# ── VO2X direct input ─────────────────────────────────────────────────────

async def get_vo2x_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.effective_message.text.strip().replace(",", ".")
    try:
        vo2x = float(raw)
        if not (20.0 <= vo2x <= 90.0):
            raise ValueError("out of range")
    except ValueError:
        await update.effective_message.reply_text(
            "Please enter a VO2X number between <b>20</b> and <b>90</b>.\n"
            "Example: <code>48</code> or <code>52.5</code>",
            parse_mode="HTML",
        )
        return VO2X_INPUT

    _ud(context)["v2_direct_vo2x"] = round(vo2x, 1)
    return await _ask_weekly_km(update, context, step=5)


# ── Recent race: time ──────────────────────────────────────────────────────

async def get_recent_time(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    minutes = _parse_time(update.effective_message.text.strip())
    if minutes is None or minutes <= 0:
        await update.effective_message.reply_text(
            "Couldn't read that time. Try <code>1:52:30</code> or <code>24:15</code>.",
            parse_mode="HTML",
        )
        return RECENT_TIME

    _ud(context)["v2_recent_time"] = minutes
    return await _ask_weekly_km(update, context, step=6)


# ── Beginner ability ───────────────────────────────────────────────────────

async def get_beginner_ability(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    key = BEGINNER_DISPLAY.get(text)
    if key is None:
        rows = [[k] for k in BEGINNER_DISPLAY.keys()]
        await update.effective_message.reply_text(
            "Please choose from the buttons.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return BEGINNER_ABILITY

    _ud(context)["v2_beginner_ability"] = key

    # C25K routing: couch and run5k_slow go straight to the 12-week beginner programme
    if key in ("couch", "run5k_slow"):
        _ud(context)["v2_is_c25k"] = True
        rows = city_keyboard_rows(cols=2)
        rows.append(["Skip for now"])
        await update.effective_message.reply_text(
            _step(4, "Couch to 5K")
            + "Great — Couch to 5K is the perfect starting point.\n\n"
            "You'll follow a 12-week walk/run programme that builds from "
            "walking intervals all the way to a non-stop 5 km run.\n\n"
            "No VO2X, no paces — just three sessions a week and a plan that "
            "meets you exactly where you are.\n\n"
            "📍 <b>One last thing — where are you based?</b>\n"
            "This lets me adjust your sessions for local weather conditions. "
            "Select your nearest city or tap <b>Skip for now</b>.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
        return LOCATION

    return await _ask_weekly_km(update, context, step=5)


# ── Weekly mileage (shared) ────────────────────────────────────────────────

async def _ask_weekly_km(update: Update, context: ContextTypes.DEFAULT_TYPE, step: int) -> int:
    rows = [
        [k for k in list(WEEKLY_KM_OPTIONS.keys())[:2]],
        [k for k in list(WEEKLY_KM_OPTIONS.keys())[2:4]],
        [k for k in list(WEEKLY_KM_OPTIONS.keys())[4:6]],
    ]
    await update.effective_message.reply_text(
        _step(step, "Weekly mileage")
        + "How many kilometres per week do you currently run on average?",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return WEEKLY_KM


async def get_weekly_km(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()

    # Check button options first
    km: float | None = WEEKLY_KM_OPTIONS.get(text)

    if km is None:
        # Try parsing a typed number (power-user fallback)
        try:
            km = float(text.replace("km", "").replace("KM", "").strip())
            if not (0 <= km <= 300):
                raise ValueError
        except ValueError:
            rows = [
                [k for k in list(WEEKLY_KM_OPTIONS.keys())[:2]],
                [k for k in list(WEEKLY_KM_OPTIONS.keys())[2:4]],
                [k for k in list(WEEKLY_KM_OPTIONS.keys())[4:6]],
            ]
            await update.effective_message.reply_text(
                "Please choose an option or type a number (0–300 km).",
                reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
            )
            return WEEKLY_KM

    _ud(context)["v2_weekly_km"] = km

    ud   = _ud(context)
    step = 7 if ud.get("v2_has_recent_race") else 6

    long_rows = [
        [k for k in list(LONGEST_RUN_OPTIONS.keys())[:2]],
        [k for k in list(LONGEST_RUN_OPTIONS.keys())[2:4]],
        [k for k in list(LONGEST_RUN_OPTIONS.keys())[4:6]],
    ]
    await update.effective_message.reply_text(
        _step(step, "Longest recent run")
        + "What is the longest run you have done in the last 6 weeks?",
        reply_markup=ReplyKeyboardMarkup(long_rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return LONGEST_RUN


# ── Longest run ────────────────────────────────────────────────────────────

async def get_longest_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()

    # Check button options first
    km: float | None = LONGEST_RUN_OPTIONS.get(text)

    if km is None:
        # Try parsing a typed number (power-user fallback)
        try:
            km = float(text.replace("km", "").strip())
            if not (0 <= km <= 300):
                raise ValueError
        except ValueError:
            long_rows = [
                [k for k in list(LONGEST_RUN_OPTIONS.keys())[:2]],
                [k for k in list(LONGEST_RUN_OPTIONS.keys())[2:4]],
                [k for k in list(LONGEST_RUN_OPTIONS.keys())[4:6]],
            ]
            await update.effective_message.reply_text(
                "Please choose an option or type a distance in km.",
                reply_markup=ReplyKeyboardMarkup(long_rows, one_time_keyboard=True, resize_keyboard=True),
            )
            return LONGEST_RUN

    ud     = _ud(context)
    weekly = ud.get("v2_weekly_km", 0)
    if km > weekly and weekly > 0:
        km = weekly   # cap at weekly volume (spec edge case)

    ud["v2_longest_run"] = km

    await update.effective_message.reply_text(
        _step(8, "Training approach")
        + "Choose your training approach:\n\n"
        "<b>Balanced</b> — standard weekly build (+12%). Best for most runners.\n\n"
        "<b>Conservative</b> — slower weekly build (+8%), more recovery time. "
        "Good if you have had training gaps or recent niggles.\n\n"
        "<b>Injury Prone</b> — same slower build rate as Conservative (+8%), "
        "but with a wider finish time range and prediction adjusted for "
        "the likelihood of missed sessions. Choose this if injury has disrupted "
        "your training in the past and you want the plan to account for that.",
        reply_markup=ReplyKeyboardMarkup(
            [["Balanced"], ["Conservative"], ["Injury Prone"]],
            one_time_keyboard=True, resize_keyboard=True,
        ),
        parse_mode="HTML",
    )
    return PLAN_TYPE


# ── Plan type -> ask location ─────────────────────────────────────────────

async def get_plan_type(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    plan_key = PLAN_DISPLAY.get(text)
    if plan_key is None:
        await update.effective_message.reply_text(
            "Please choose Balanced, Conservative, or Injury Prone.",
            reply_markup=ReplyKeyboardMarkup(
                [["Balanced"], ["Conservative"], ["Injury Prone"]],
                one_time_keyboard=True, resize_keyboard=True,
            ),
        )
        return PLAN_TYPE

    _ud(context)["v2_plan_type"] = plan_key

    # Route to long-run day selection
    return await _ask_long_run_day(update, context)


# ── Training day selection ─────────────────────────────────────────────────

async def _ask_long_run_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    rows = [[day] for day in LONG_RUN_DAY_LABELS.keys()]
    await update.effective_message.reply_text(
        _step(9, "Long run day")
        + "Which day works best for your <b>long run</b>?\n\n"
        "This is your biggest session of the week — choose a day when you "
        "have the most time and energy.",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return LONG_RUN_DAY


async def get_long_run_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    day = LONG_RUN_DAY_LABELS.get(text)
    if day is None:
        rows = [[d] for d in LONG_RUN_DAY_LABELS.keys()]
        await update.effective_message.reply_text(
            "Please choose a day from the options.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return LONG_RUN_DAY

    _ud(context)["v2_long_run_day"] = day

    # Build quality day options — exclude the selected long run day
    options = {k: v for k, v in QUALITY_DAY_LABELS.items() if v != day}
    rows = [[k] for k in options.keys()]
    await update.effective_message.reply_text(
        _step(10, "Quality day")
        + "Which day is best for your <b>hard/quality session</b>?\n\n"
        "This is your interval, threshold, or hill workout — "
        "choose a day you'll have good energy and ideally 2+ days before your long run.",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return QUALITY_DAY


async def get_quality_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    day = QUALITY_DAY_LABELS.get(text)
    ud = _ud(context)
    long_run_day = ud.get("v2_long_run_day", "Sat")

    if day is None or day == long_run_day:
        options = {k: v for k, v in QUALITY_DAY_LABELS.items() if v != long_run_day}
        rows = [[k] for k in options.keys()]
        await update.effective_message.reply_text(
            "Please choose a different day to your long run day.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return QUALITY_DAY

    ud["v2_quality_day"] = day

    # Build easy day options — exclude long run day and quality day
    taken = {long_run_day, day}
    options = {k: v for k, v in EASY_DAY_LABELS.items() if v not in taken}
    rows = [[k] for k in options.keys()]
    await update.effective_message.reply_text(
        _step(11, "Easy run days")
        + "Which day works best for your <b>first easy run</b>?\n\n"
        "Easy runs are low-intensity — a comfortable pace where you can hold a conversation.",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return EASY_DAYS


async def get_easy_days(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """First easy day — button selection."""
    text = update.effective_message.text.strip()
    ud = _ud(context)
    long_run_day = ud.get("v2_long_run_day", "Sat")
    quality_day  = ud.get("v2_quality_day", "Tue")
    taken = {long_run_day, quality_day}

    day = EASY_DAY_LABELS.get(text)
    if day is None or day in taken:
        options = {k: v for k, v in EASY_DAY_LABELS.items() if v not in taken}
        rows = [[k] for k in options.keys()]
        await update.effective_message.reply_text(
            "Please choose a day from the buttons.",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        )
        return EASY_DAYS

    ud["v2_easy_day_1"] = day

    # Build options for second day
    taken2 = taken | {day}
    options2 = {k: v for k, v in EASY_DAY_LABELS.items() if v not in taken2}
    rows2 = [[k] for k in options2.keys()]
    rows2.append(["Only one easy day"])
    await update.effective_message.reply_text(
        _step(11, "Easy run days")
        + "Great! Would you like to add a <b>second easy run day</b>?\n\n"
        "Two easy days help build your aerobic base without adding too much fatigue.",
        reply_markup=ReplyKeyboardMarkup(rows2, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return EASY_DAY_2


async def get_easy_day_2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Second easy day — button selection or skip."""
    text = update.effective_message.text.strip()
    ud = _ud(context)
    long_run_day = ud.get("v2_long_run_day", "Sat")
    quality_day  = ud.get("v2_quality_day", "Tue")
    easy_day_1   = ud.get("v2_easy_day_1", "Mon")
    taken = {long_run_day, quality_day, easy_day_1}

    if text == "Only one easy day":
        ud["v2_extra_training_days"] = easy_day_1
    else:
        day = EASY_DAY_LABELS.get(text)
        if day is None or day in taken:
            options = {k: v for k, v in EASY_DAY_LABELS.items() if v not in taken}
            rows = [[k] for k in options.keys()]
            rows.append(["Only one easy day"])
            await update.effective_message.reply_text(
                "Please choose a day from the buttons, or tap <b>Only one easy day</b>.",
                reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
                parse_mode="HTML",
            )
            return EASY_DAY_2
        ud["v2_extra_training_days"] = f"{easy_day_1},{day}"

    return await _ask_anchor_question(update, context)


# ── Anchor runs — onboarding steps ───────────────────────────────────────

_ANCHOR_DAYS_ALL = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_ANCHOR_KM_OPTS  = ["5 km", "8 km", "10 km", "12 km", "15 km", "Other distance"]


async def _ask_anchor_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Step 12 — ask if the user runs with a club or group."""
    ud = _ud(context)
    ud.setdefault("v2_anchors", [])   # initialise anchor list

    # How many anchors already collected?
    count = len(ud["v2_anchors"])
    if count >= 2:
        # Already have 2 — move on
        return await _ask_location(update, context)

    rows = [["Yes, I have a group run"], ["No group runs"]]
    if count == 1:
        rows = [["Yes, add another group run"], ["Done — no more"]]

    prompt = (
        _step(12, "Club / group runs")
        + "Do you run with a club or group on a fixed day each week?\n\n"
        "These runs will be <b>locked into your plan</b> — the system adjusts "
        "your other sessions around them.\n\n"
        "<i>You can set up to 2 group runs.</i>"
    ) if count == 0 else (
        _step(12, "Club / group runs")
        + f"You've added <b>{count}</b> group run. Want to add a second one?"
    )

    await update.effective_message.reply_text(
        prompt,
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return ANCHOR_QUESTION


async def get_anchor_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Yes / No to the group run question."""
    text = update.effective_message.text.strip()
    ud   = _ud(context)

    if text in ("No group runs", "Done — no more"):
        return await _ask_location(update, context)

    # Yes — ask which day
    existing_days = {a["day"] for a in ud.get("v2_anchors", [])}
    available     = [d for d in _ANCHOR_DAYS_ALL if d not in existing_days]

    rows = [[d] for d in available]
    rows.append(["Skip"])

    await update.effective_message.reply_text(
        "Which day is your group run?",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return ANCHOR_KM   # re-use ANCHOR_KM state; first message selects day


async def get_anchor_day_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User picked a day (or Skip). Now ask for the distance."""
    text = update.effective_message.text.strip()
    ud   = _ud(context)

    if text == "Skip":
        return await _ask_location(update, context)

    day = text[:3]  # "Monday" → "Mon" etc.; buttons are 3-char already
    if day not in _ANCHOR_DAYS_ALL:
        await update.effective_message.reply_text("Please choose a day from the buttons.")
        return ANCHOR_KM

    ud["v2_anchor_pending_day"] = day

    rows = [_ANCHOR_KM_OPTS[:3], _ANCHOR_KM_OPTS[3:]]
    await update.effective_message.reply_text(
        f"How far is your <b>{day}</b> group run?",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return ANCHOR_KM


async def get_anchor_km(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User entered a distance (preset button or typed value)."""
    text = update.effective_message.text.strip()
    ud   = _ud(context)
    day  = ud.get("v2_anchor_pending_day")

    # If no day set yet, this response is a day selection
    if not day:
        return await get_anchor_day_select(update, context)

    # Parse km
    raw = text.replace(" km", "").replace("km", "").replace(",", ".").strip()
    if raw == "Other distance":
        await update.effective_message.reply_text(
            f"Type the distance for your <b>{day}</b> group run in km "
            "(e.g. <code>10</code> or <code>6.5</code>):",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML",
        )
        return ANCHOR_KM

    try:
        km = float(raw)
        if km <= 0 or km > 100:
            raise ValueError
    except ValueError:
        rows = [_ANCHOR_KM_OPTS[:3], _ANCHOR_KM_OPTS[3:]]
        await update.effective_message.reply_text(
            "Please enter a valid distance (e.g. <code>10</code> or <code>6.5</code>).",
            reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
            parse_mode="HTML",
        )
        return ANCHOR_KM

    # Save anchor entry
    anchors = ud.setdefault("v2_anchors", [])
    anchors = [a for a in anchors if a["day"] != day]   # dedupe
    anchors.append({"day": day, "km": km})
    anchors.sort(key=lambda a: _ANCHOR_DAYS_ALL.index(a["day"]))
    ud["v2_anchors"] = anchors
    ud.pop("v2_anchor_pending_day", None)

    return await _ask_anchor_question(update, context)


async def _ask_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Transition from anchor step → location step."""
    rows = city_keyboard_rows(cols=2)
    rows.append(["Skip for now"])
    await update.effective_message.reply_text(
        _step(13, "Your location")
        + "Where are you based? This lets me adjust your training paces for local weather "
        "conditions (TRUEPACE).\n\n"
        "Select your nearest city or tap <b>Skip for now</b>.",
        reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
        parse_mode="HTML",
    )
    return LOCATION


# ── C25K profile creation ─────────────────────────────────────────────────

async def _create_c25k_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """POST to /athlete/c25k and send welcome message. Called from get_location for C25K path."""
    import logging as _log
    _logger = _log.getLogger(__name__)
    ud = _ud(context)
    telegram_id = str(update.effective_user.id)

    payload = {
        "telegram_id": telegram_id,
        "name":        ud.get("v2_name", "Runner"),
        "start_date":  date.today().isoformat(),
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{API_BASE_URL}/athlete/c25k", json=payload)
            if r.status_code == 409:
                await client.delete(f"{API_BASE_URL}/athlete/{telegram_id}")
                r = await client.post(f"{API_BASE_URL}/athlete/c25k", json=payload)
            if not r.is_success:
                raise RuntimeError(f"Server error {r.status_code}: {r.text[:200]}")

            # PATCH location if provided
            lat = ud.get("v2_lat")
            lon = ud.get("v2_lon")
            if lat is not None and lon is not None:
                try:
                    await client.patch(
                        f"{API_BASE_URL}/athlete/{telegram_id}/location",
                        json={"latitude": lat, "longitude": lon, "run_hour": 6},
                    )
                except Exception as loc_err:
                    _logger.warning(f"C25K location PATCH failed (non-fatal): {loc_err}")

    except Exception as e:
        _logger.exception(f"_create_c25k_profile failed for {telegram_id}: {e}")
        await update.effective_message.reply_text(
            "<b>Could not create your profile.</b>\n\n"
            f"<i>{_e(str(e))}</i>\n\n"
            "Please type /start to try again.",
            parse_mode="HTML",
            reply_markup=ReplyKeyboardRemove(),
        )
        return

    _clear_v2(context)

    city_label = ud.get("v2_city_label")
    location_line = f"\n📍 Location set to <b>{_e(city_label)}</b>." if city_label else ""

    await update.effective_message.reply_text(
        "🎉 <b>Your Couch to 5K plan is ready!</b>"
        + location_line
        + "\n\n"
        "You'll run <b>3 sessions a week</b> for 12 weeks. "
        "Each session mixes walking and running intervals — the balance shifts "
        "week by week until you're running non-stop.\n\n"
        "Use /today to see today's session, or /menu to explore your plan.",
        parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove(),
    )


# ── Location -> prediction -> show result ────────────────────────────────

async def get_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.effective_message.text.strip()
    ud = _ud(context)

    lat = lon = None
    city_label = None

    if text.lower() != "skip for now":
        city = find_city(text)
        if city:
            lat = city.latitude
            lon = city.longitude
            city_label = f"{city.name} ({city.province})"
        else:
            rows = city_keyboard_rows(cols=2)
            rows.append(["Skip for now"])
            await update.effective_message.reply_text(
                "City not recognised — please choose from the list or tap Skip for now.",
                reply_markup=ReplyKeyboardMarkup(rows, one_time_keyboard=True, resize_keyboard=True),
            )
            return LOCATION

    ud["v2_lat"]       = lat
    ud["v2_lon"]       = lon
    ud["v2_city_label"] = city_label

    # ── C25K path: skip prediction entirely, create profile now ──────────
    if ud.get("v2_is_c25k"):
        await _create_c25k_profile(update, context)
        return ConversationHandler.END

    # Compute prediction
    await update.effective_message.reply_text(
        "Calculating your race prediction...",
        reply_markup=ReplyKeyboardRemove(),
    )

    plan_key  = ud.get("v2_plan_type", "balanced")
    race_date = date.fromisoformat(ud["v2_race_date"])

    inp = PredictionInput(
        race_name                = ud.get("v2_race_name", "Your race"),
        race_distance_km         = ud["v2_race_distance_km"],
        hill_factor              = ud.get("v2_hill_factor", 0.05),
        race_date                = race_date,
        has_recent_race          = ud.get("v2_has_recent_race", False),
        recent_race_distance_km  = ud.get("v2_recent_dist"),
        recent_race_time_minutes = ud.get("v2_recent_time"),
        beginner_ability         = ud.get("v2_beginner_ability"),
        weekly_mileage_km        = ud.get("v2_weekly_km", 0),
        longest_run_km           = ud.get("v2_longest_run", 0),
        plan_type                = plan_key,
        direct_vo2x              = ud.get("v2_direct_vo2x"),
    )
    result = predict(inp)

    # ── Format prediction result ───────────────────────────────────────────
    race_name  = _e(ud.get("v2_race_name", "Your race"))
    race_dist  = ud["v2_race_distance_km"]
    date_str   = f"{race_date.day} {race_date.strftime('%b %Y')}"

    lines = [
        f"<b>TR3D  ·  YOUR PREDICTION</b>",
        _DIV,
        f"<b>{race_name}</b>",
        f"{race_dist:g} km  ·  {date_str}",
    ]

    if city_label:
        lines.append(f"📍 {_e(city_label)}")

    lines += [
        f"<i>{result.weeks_to_race} weeks to race day</i>",
        "",
        _DIV,
        "<b>FINISH TIME</b>",
        f"<b>{result.low_fmt()} — {result.high_fmt()}</b>",
        f"Goal target:  <b>{result.mid_fmt()}</b>",
    ]

    if result.training_focus:
        lines += ["", _DIV, "<b>TRAINING PRIORITIES</b>"]
        for tip in result.training_focus:
            lines.append(f"  • {_e(tip)}")

    if result.warnings:
        lines.append("")
        for w in result.warnings:
            lines.append(f"<i>{_e(w)}</i>")

    lines += ["", _DIV, "Ready to build your plan?"]

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Create my training plan", callback_data="v2_confirm")],
        [InlineKeyboardButton("↩ Start over",              callback_data="v2_restart")],
    ])

    await update.effective_message.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode="HTML",
    )
    return ConversationHandler.END


# ── Inline button: confirm or restart ────────────────────────────────────

async def v2_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called from handle_callback when data == 'v2_confirm'.
    NOTE: handle_callback already calls query.answer() — do NOT call it again here.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    ud          = _ud(context)
    telegram_id = str(update.effective_user.id)

    try:
        # ── Guard: user_data may be missing if bot restarted ──────────────
        if "v2_race_date" not in ud or "v2_race_distance_km" not in ud:
            await update.effective_message.reply_text(
                "Your session has expired — please type /start to begin again.",
                reply_markup=ReplyKeyboardRemove(),
            )
            return

        # ── Recompute prediction to get VO2X ──────────────────────────────
        race_date = date.fromisoformat(ud["v2_race_date"])
        inp = PredictionInput(
            race_name                = ud.get("v2_race_name", "Your race"),
            race_distance_km         = ud["v2_race_distance_km"],
            hill_factor              = ud.get("v2_hill_factor", 0.05),
            race_date                = race_date,
            has_recent_race          = ud.get("v2_has_recent_race", False),
            recent_race_distance_km  = ud.get("v2_recent_dist"),
            recent_race_time_minutes = ud.get("v2_recent_time"),
            beginner_ability         = ud.get("v2_beginner_ability"),
            weekly_mileage_km        = ud.get("v2_weekly_km", 0),
            longest_run_km           = ud.get("v2_longest_run", 0),
            plan_type                = ud.get("v2_plan_type", "balanced"),
            direct_vo2x              = ud.get("v2_direct_vo2x"),
        )
        result = predict(inp)

        training_profile = PLAN_TYPE_TO_PROFILE.get(ud.get("v2_plan_type", "balanced"), "aggressive")
        race_distance    = ud.get("v2_race_distance", km_to_race_distance(ud["v2_race_distance_km"]))

        payload = {
            "telegram_id":            telegram_id,
            "name":                   ud.get("v2_name", "Runner"),
            "current_weekly_mileage": max(ud.get("v2_weekly_km", 10), 5.0),
            "vo2x":                   result.vo2x or 35.0,
            "race_distance":          race_distance,
            "race_hilliness":         ud.get("v2_race_hilliness", "low"),
            "race_date":              ud["v2_race_date"],
            "start_date":             date.today().isoformat(),
            "race_name":              ud.get("v2_race_name", ""),
            "preset_race_id":         ud.get("v2_preset_id"),
            "training_profile":       training_profile,
            "plan_type":              "full",
            "long_run_day":           ud.get("v2_long_run_day", "Sat"),
            "quality_day":            ud.get("v2_quality_day", "Tue"),
            "extra_training_days":    ud.get("v2_extra_training_days", "Thu"),
            "latitude":               ud.get("v2_lat"),
            "longitude":              ud.get("v2_lon"),
        }

        # ── POST to API ───────────────────────────────────────────────────
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{API_BASE_URL}/athlete/", json=payload)
            if r.status_code == 409:
                # Profile already exists — delete and recreate
                await client.delete(f"{API_BASE_URL}/athlete/{telegram_id}")
                r = await client.post(f"{API_BASE_URL}/athlete/", json=payload)
            if not r.is_success:
                _logger.error(f"v2_confirm POST failed {r.status_code}: {r.text}")
                raise RuntimeError(f"Server error {r.status_code}: {r.text[:200]}")

            # ── Belt-and-suspenders: PATCH location separately ────────────
            lat = payload.get("latitude")
            lon = payload.get("longitude")
            if lat is not None and lon is not None:
                try:
                    await client.patch(
                        f"{API_BASE_URL}/athlete/{telegram_id}/location",
                        json={"latitude": lat, "longitude": lon, "run_hour": 6},
                    )
                except Exception as loc_err:
                    _logger.warning(f"Location PATCH failed (non-fatal): {loc_err}")

            # ── Save anchor runs collected during onboarding ──────────────
            anchors = ud.get("v2_anchors", [])
            if anchors:
                try:
                    await client.patch(
                        f"{API_BASE_URL}/athlete/{telegram_id}/anchors",
                        json={"anchors": anchors},
                    )
                except Exception as anc_err:
                    _logger.warning(f"Anchor PATCH failed (non-fatal): {anc_err}")

    except Exception as e:
        _logger.exception(f"v2_confirm_callback failed for {telegram_id}: {e}")
        await update.effective_message.reply_text(
            f"<b>Could not create your profile.</b>\n\n"
            f"<i>{_e(str(e))}</i>\n\n"
            "Please type /start to try again.",
            parse_mode="HTML",
        )
        return

    _clear_v2(context)

    name = payload["name"]
    await update.effective_message.reply_text(
        f"<b>TR3D  ·  PLAN CREATED</b>\n{_DIV}\n\n"
        f"Your training plan is live, <b>{_e(name)}</b>!\n\n"
        f"Target:  <b>{result.low_fmt()} — {result.high_fmt()}</b>\n"
        f"VO2X:    <b>{result.vo2x:.1f}</b>  <i>· Velocity–Oxygen Performance Index</i>\n\n"
        f"{_DIV}\n\n"
        "Use the menu below to see today's session.",
        reply_markup=main_menu_keyboard(),
        parse_mode="HTML",
    )


async def v2_restart_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Called from handle_callback when data == 'v2_restart'.
    NOTE: handle_callback already calls query.answer() — do NOT call it again here.
    """
    _clear_v2(context)
    await update.effective_message.reply_text(
        "No problem — let's start over. Type /start to begin.",
    )


# ── Shared handlers (cancel, reset, web app) ──────────────────────────────

async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _clear_v2(context)
    await update.effective_message.reply_text(
        "Onboarding cancelled. Type /start to begin again.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete athlete profile and restart onboarding."""
    telegram_id = str(update.effective_user.id)
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.delete(f"{API_BASE_URL}/athlete/{telegram_id}")
    except Exception:
        pass
    _clear_v2(context)
    await update.effective_message.reply_text(
        "Profile deleted. Type /start to create a new one.",
        reply_markup=ReplyKeyboardRemove(),
    )


async def handle_web_app_data(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Placeholder for Telegram Web App (mini-app) data events.

    The v2 onboarding does not currently use web_app_data — this handler
    is registered to prevent unhandled-update noise and to reserve the hook
    for future mini-app flows. It logs the payload and does nothing else.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)
    try:
        raw = update.effective_message.web_app_data.data
        _logger.info(f"handle_web_app_data received (unhandled): {raw[:200]}")
    except Exception as e:
        _logger.warning(f"handle_web_app_data: could not read payload — {e}")
