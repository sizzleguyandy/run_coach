"""
Formatting helpers for all TR3D Telegram messages and inline keyboards.
"""
from __future__ import annotations
from datetime import date
from typing import Optional


# ── Design system ──────────────────────────────────────────────────────────

_DIV  = "─" * 26          # primary section divider
_DIV2 = "─" * 18          # lighter sub-divider


def _hdr(screen: str) -> str:
    """TR3D branded screen header — appears at top of every view."""
    return f"<b>TR3D  ·  {screen}</b>\n{_DIV}"


def _sec(title: str) -> str:
    """Sub-section with divider above."""
    return f"{_DIV}\n<b>{title}</b>"


# ── Screen padding (legacy — kept for import compat) ───────────────────────

_SCREEN_PAD_LINES = 20


def pad_message(text: str) -> str:
    """Kept for backward compatibility — do NOT use in new code."""
    return text


# ── Constants ──────────────────────────────────────────────────────────────

PHASE_NAMES = {
    1: "Base & Foundation",
    2: "Early Quality",
    3: "Peak Quality",
    4: "Race Prep & Taper",
}

PHASE_EMOJIS = {1: "🧱", 2: "⚙️", 3: "🔥", 4: "🎯"}

DAY_ORDER     = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_TO_DAY = {0: "Mon", 1: "Tue", 2: "Wed", 3: "Thu", 4: "Fri", 5: "Sat", 6: "Sun"}

# Session-type emojis — first keyword match wins
SESSION_EMOJI_MAP: list[tuple[str, str]] = [
    ("hill",         "⛰️"),
    ("interval",     "⚡"),
    ("tempo",        "🔥"),
    ("threshold",    "🔥"),
    ("cruise",       "🔥"),
    ("stride",       "💨"),
    ("repetition",   "💨"),
    ("r-pace",       "💨"),
    ("back-to-back", "🏃"),
    ("long run",     "🏃"),
    ("medium-long",  "🏃"),
    ("medium long",  "🏃"),
    ("recovery",     "🟢"),
    ("easy",         "🟢"),
    ("rest",         "😴"),
    ("cross-train",  "😴"),
    ("walk",         "🚶"),
    ("run/walk",     "🌱"),
]


def _session_emoji(session_name: str) -> str:
    name = session_name.lower()
    for keyword, emoji in SESSION_EMOJI_MAP:
        if keyword in name:
            return emoji
    return "🏃"


def _short_date(date_str: str) -> str:
    """'2026-03-23' → '23 Mar'."""
    try:
        d = date.fromisoformat(date_str)
        return f"{d.day} {d.strftime('%b')}"
    except Exception:
        return date_str


# Clean display names for the weekly table — avoids mid-word truncation.
_SESSION_ABBR: dict[str, str] = {
    "Recovery Run":     "Recovery",
    "Medium-Long Run":  "Med-Long",
    "Back-to-Back Run": "B2B Run",
    "Downhill Repeats": "Downhill",
    "Easy + Strides":   "Easy+Strides",
    "Cross-Train":      "Cross-Train",
    "Rest / Walk":      "Rest/Walk",
    "🏁 RACE DAY":      "Race Day 🏁",
}


def _short_session(name: str, max_len: int = 15) -> str:
    """Return a clean short name — checks abbreviation map before truncating."""
    abbr = _SESSION_ABBR.get(name)
    if abbr:
        return abbr
    return name if len(name) <= max_len else name[:max_len - 1] + "…"


# ── Strength days ──────────────────────────────────────────────────────────

STRENGTH_NOTE = (
    "💪 Strength — 2 sets each: squats, lunges, single-leg deadlifts, "
    "calf raises, glute bridges. 20–30 min. No gym needed."
)


def _get_strength_days(days: dict) -> set:
    REST_SESSIONS = {"Rest", "Cross-Train / Rest"}
    rest_days = [
        d for d in DAY_ORDER
        if (days.get(d, {}).get("session", "Rest") in REST_SESSIONS
            or days.get(d, {}).get("km", 0) == 0)
        and d != "Sun"
    ]
    return set(rest_days[:2])


# ── Two Oceans seeding ──────────────────────────────────────────────────────

_TWO_OCEANS_BATCHES = [
    {"batch": "A", "fastest_min": 58,  "slowest_min": 85},
    {"batch": "B", "fastest_min": 85,  "slowest_min": 96},
    {"batch": "C", "fastest_min": 96,  "slowest_min": 105},
    {"batch": "D", "fastest_min": 105, "slowest_min": 110},
    {"batch": "E", "fastest_min": 110, "slowest_min": 115},
    {"batch": "F", "fastest_min": 115, "slowest_min": 119},
    {"batch": "G", "fastest_min": 119, "slowest_min": 120},
    {"batch": "H", "fastest_min": 120, "slowest_min": 124},
    {"batch": "J", "fastest_min": 124, "slowest_min": 128},
    {"batch": "K", "fastest_min": 128, "slowest_min": 130},
    {"batch": "L", "fastest_min": 130, "slowest_min": 150},
    {"batch": "M", "fastest_min": 133, "slowest_min": 135},
    {"batch": "N", "fastest_min": 135, "slowest_min": 140},
    {"batch": "P", "fastest_min": 140, "slowest_min": 143},
    {"batch": "Q", "fastest_min": 143, "slowest_min": 149},
    {"batch": "R", "fastest_min": 149, "slowest_min": 150},
    {"batch": "S", "fastest_min": 150, "slowest_min": 209},
    {"batch": "T", "fastest_min": 150, "slowest_min": 157},
    {"batch": "U", "fastest_min": 157, "slowest_min": 161},
    {"batch": "V", "fastest_min": 161, "slowest_min": 169},
    {"batch": "W", "fastest_min": 169, "slowest_min": 177},
    {"batch": "X", "fastest_min": 177, "slowest_min": 186},
    {"batch": "Y", "fastest_min": 186, "slowest_min": 276},
]


def get_two_oceans_batch(vdot: float) -> dict | None:
    try:
        import math
        _A, _B, _C = 0.000104, 0.182258, 4.6
        vdot_f = max(30.0, min(85.0, float(vdot)))
        v = (-_B + math.sqrt(_B * _B + 4.0 * _A * (vdot_f * 0.88 + _C))) / (2.0 * _A)
        hm_min = 21097 / v
        for b in _TWO_OCEANS_BATCHES:
            if b["fastest_min"] <= hm_min <= b["slowest_min"]:
                h, m = divmod(int(hm_min), 60)
                hm_str = f"{h}:{m:02d}" if h else f"{m} min"
                return {"batch": b["batch"], "hm_time": hm_str}
        h, m = divmod(int(hm_min), 60)
        hm_str = f"{h}:{m:02d}" if h else f"{m} min"
        return {"batch": "Y", "hm_time": hm_str}
    except Exception:
        return None


def _primary_pace_for_session(session_name: str, paces: "dict | None") -> str:
    if not paces or not session_name:
        return ""
    name = session_name.lower()
    if any(k in name for k in ("threshold", "tempo", "cruise")):
        return paces.get("threshold", "")
    if any(k in name for k in ("interval", "repetition", "r-pace")):
        return paces.get("interval", "")
    if "hill" in name:
        return paces.get("threshold", "")
    if "stride" in name:
        return paces.get("repetition", "")
    return paces.get("easy", "")


# ── Inline keyboard builders ────────────────────────────────────────────────

def main_menu_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏃 Today",     callback_data="today"),
            InlineKeyboardButton("📅 This Week", callback_data="plan"),
        ],
        [
            InlineKeyboardButton("📊 Dashboard", callback_data="dashboard"),
            InlineKeyboardButton("📝 Log Run",   callback_data="log"),
        ],
        [
            InlineKeyboardButton("💬 Coach chat", callback_data="coach_chat"),
            InlineKeyboardButton("⚙️ Settings",  callback_data="settings"),
        ],
    ])


def back_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("← Menu", callback_data="menu"),
    ]])


def today_keyboard(logged: bool = False, session_url: "str | None" = None):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    log_label = "✅ Already logged" if logged else "📝 Log this run"
    rows = []
    if session_url:
        rows.append([InlineKeyboardButton("▶ Start Session", web_app=WebAppInfo(url=session_url))])
    rows.append([InlineKeyboardButton(log_label, callback_data="log")])
    rows.append([
        InlineKeyboardButton("📅 Full week", callback_data="plan"),
        InlineKeyboardButton("📊 Dashboard", callback_data="dashboard"),
    ])
    rows.append([InlineKeyboardButton("← Menu", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def plan_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏃 Today",     callback_data="today"),
            InlineKeyboardButton("📊 Dashboard", callback_data="dashboard"),
        ],
        [InlineKeyboardButton("📲 Add to Calendar", callback_data="calendar_ics")],
        [InlineKeyboardButton("← Menu", callback_data="menu")],
    ])


def dashboard_keyboard():
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🏃 Today",     callback_data="today"),
            InlineKeyboardButton("📅 This Week", callback_data="plan"),
        ],
        [
            InlineKeyboardButton("📝 Log Run",   callback_data="log"),
        ],
        [InlineKeyboardButton("← Menu", callback_data="menu")],
    ])


# ── Main menu ──────────────────────────────────────────────────────────────

def format_main_menu(athlete_name: str, plan_type: str = "full") -> str:
    if plan_type == "c25k":
        return (
            f"{_hdr('TR3D')}\n\n"
            f"Hey <b>{athlete_name}</b>  ·  Couch to 5K\n\n"
            "What would you like to do?"
        )
    return (
        f"{_hdr('TR3D')}\n\n"
        f"Hey <b>{athlete_name}</b>! What would you like to do?\n\n"
        "<i>Science-Built. Race-Ready.</i>"
    )


# ── Today's run ────────────────────────────────────────────────────────────

def format_today(week: dict, logged_today: bool = False, paces: "dict | None" = None) -> str:
    today_key = WEEKDAY_TO_DAY.get(date.today().weekday(), "Mon")
    plan_type = week.get("plan_type", "full")

    if plan_type == "c25k":
        return _format_today_c25k(week, today_key)

    days        = week.get("days", {})
    session     = days.get(today_key, {})
    phase_num   = week.get("phase", 1)
    phase_name  = PHASE_NAMES.get(phase_num, "Training")
    phase_emoji = PHASE_EMOJIS.get(phase_num, "")
    week_num    = week.get("week_number", "?")
    total_weeks = week.get("total_weeks", "?")
    total_vol   = week.get("planned_volume_km", 0)

    session_name = session.get("session", "Rest")
    km           = session.get("km", 0)
    notes        = session.get("notes", "")
    is_rest      = session_name in ("Rest", "Cross-Train / Rest") or km == 0
    is_strength  = is_rest and today_key in _get_strength_days(days)

    # ── Notification-first line 1 (push notification preview) ─────────────
    if is_rest:
        if is_strength:
            notif_line = f"💪 <b>Today ({today_key}): Strength Day — Week {week_num}</b>"
        else:
            notif_line = f"😴 <b>Today ({today_key}): Rest Day — Week {week_num}</b>"
    else:
        primary_pace = _primary_pace_for_session(session_name, paces)
        pace_str     = f" @ {primary_pace}/km" if primary_pace else ""
        notif_line   = f"🏃 <b>Today: {session_name} {km} km{pace_str}</b>"

    # ── Date string ────────────────────────────────────────────────────────
    try:
        today = date.today()
        day_label = f"{today.strftime('%A')}, {today.day} {today.strftime('%B')}"
    except Exception:
        day_label = today_key

    # ── Hero ───────────────────────────────────────────────────────────────
    if is_rest:
        if is_strength:
            hero_icon  = "💪"
            hero_title = "Strength Day"
            hero_sub   = ""
        else:
            hero_icon  = "😴"
            hero_title = "Rest Day"
            hero_sub   = ""
    else:
        s_emoji      = _session_emoji(session_name)
        primary_pace = _primary_pace_for_session(session_name, paces)
        pace_str     = f"  ·  {primary_pace}/km" if primary_pace else ""
        hero_icon    = s_emoji
        hero_title   = f"{session_name}  ·  {km} km{pace_str}"
        hero_sub     = ""

    lines = [
        notif_line,
        "",
        _hdr("TODAY"),
        f"{hero_icon}  <b>{hero_title}</b>",
        f"<i>{day_label}</i>",
        "",
    ]

    # ── Session body ───────────────────────────────────────────────────────
    if is_rest:
        if is_strength:
            lines += [STRENGTH_NOTE, "", "<i>Running adapts faster with 2 strength sessions per week.</i>"]
        else:
            lines += [
                "Rest day — recovery is part of the programme.",
                "<i>Eat well, sleep, and let the adaptations happen.</i>",
            ]
    else:
        if notes:
            lines.append(notes)

    if logged_today:
        lines += ["", "✅ <b>Run logged for today.</b>"]

    # ── Footer ─────────────────────────────────────────────────────────────
    lines += [
        "",
        _sec("WEEK"),
        f"Week <b>{week_num}</b>  ·  {phase_emoji} {phase_name}  ·  <b>{total_vol} km</b> target",
    ]

    return "\n".join(lines)


def _format_today_c25k(week: dict, today_key: str) -> str:
    wn        = week.get("week_number", "?")
    run_days  = {"Mon", "Wed", "Fri"}
    days      = week.get("days", {})
    session   = days.get(today_key, {})
    total_min = week.get("total_minutes", 30)
    run_min   = week.get("total_run_minutes_per_session", 0)
    is_run_day = today_key in run_days

    if is_run_day:
        notif_line = f"🌱 <b>Today: C25K Week {wn} — Run/Walk ~{total_min} min</b>"
    else:
        notif_line = f"😴 <b>Today: C25K Week {wn} — Rest Day</b>"

    lines = [
        notif_line,
        "",
        _hdr("TODAY  ·  C25K"),
        f"🌱  <b>Week {wn} of 12</b>",
        "",
    ]

    if is_run_day:
        notes = session.get("notes", "")
        lines += [
            f"⏱ Duration: ~{total_min} min  ·  Running: ~{run_min:.0f} min",
            "",
            notes,
        ]
    else:
        lines += [session.get("notes", "Active recovery — gentle walk or full rest.")]

    return "\n".join(lines)


# ── Weekly plan ────────────────────────────────────────────────────────────

def format_week(week: dict, summary: "dict | None" = None) -> str:
    """
    Weekly overview with per-day status tracking.
    Uses a <pre> block for pixel-perfect column alignment on all devices.
    summary: optional log summary dict (from GET /log/{id}/week/{n}/summary).
    """
    phase_num   = week.get("phase", 1)
    phase_name  = PHASE_NAMES.get(phase_num, "Training")
    phase_emoji = PHASE_EMOJIS.get(phase_num, "")
    week_num    = week.get("week_number", "?")
    total_vol   = week.get("planned_volume_km", 0)
    week_start  = _short_date(week.get("week_start", ""))
    today_key   = WEEKDAY_TO_DAY.get(date.today().weekday(), "Mon")
    today_idx   = DAY_ORDER.index(today_key) if today_key in DAY_ORDER else 0

    REST_SESSIONS = {"Rest", "Cross-Train / Rest"}

    # Logged days from summary
    logged_days: set[str] = set()
    if summary:
        logged_days = {r["day"] for r in summary.get("runs", [])}

    all_days      = week.get("days", {})
    strength_days = _get_strength_days(all_days)

    # Week date range
    week_end_str = ""
    try:
        from datetime import timedelta
        ws = date.fromisoformat(week.get("week_start", ""))
        we = ws + timedelta(days=6)
        week_end_str = f"–{we.day} {we.strftime('%b')}"
    except Exception:
        pass

    lines = [
        _hdr("THIS WEEK"),
        f"<b>Week {week_num}  ·  {phase_emoji} {phase_name}</b>",
        f"<i>{week_start}{week_end_str}  ·  {total_vol} km planned</i>",
        "",
    ]

    # ── Day table in <pre> block for perfect alignment ─────────────────────
    #
    # Column layout (all monospace):
    #   DAY(3)   LABEL(18)  STATUS(4) EMOJI(2)
    #   "MON   Strength           done ✅"
    #   "TUE   Intervals 8km      done ✅"
    #   "FRI   Rest day           now  ✨"
    #   "SAT   Long run 10km      tmrw 🔒"
    #   "SUN   Recovery walk          🔒"
    #
    # STATUS values are fixed 4 chars: "done", "now ", "rest", "tmrw", "    "
    # EMOJI are all full-width (2 visual chars): ✅ ✨ 🔒
    # This ensures both columns align perfectly across all rows.

    table_rows = []
    for i, day in enumerate(DAY_ORDER):
        session     = all_days.get(day, {})
        name        = session.get("session", "Rest")
        km          = session.get("km", 0)
        is_rest     = name in REST_SESSIONS or km == 0
        is_today    = day == today_key
        is_future   = i > today_idx
        is_tomorrow = i == today_idx + 1
        is_strength = is_rest and day in strength_days

        # Anchor flag
        is_anchor   = bool(session.get("anchor"))

        # Session label — abbr map keeps names clean; 20-char col fits all
        if is_strength:
            label = "Strength"
        elif is_rest:
            label = "Rest day"
        else:
            short = _short_session(name, 14)
            km_str = f" {km:g}km" if km else ""
            label = f"{short}{km_str}"

        label_col = f"{label:<20}"   # padded to exactly 20 chars

        # Status: 4-char text + space + 2-char emoji
        if is_anchor:
            # Anchor overrides the icon column to show the pin — keeps alignment
            if day in logged_days:
                status, icon = "done", "📌"
            elif is_today:
                status, icon = "now ", "📌"
            elif is_tomorrow:
                status, icon = "tmrw", "📌"
            else:
                status, icon = "    ", "📌"
        elif day in logged_days:
            status, icon = "done", "✅"
        elif is_today and not is_rest:
            status, icon = "now ", "✨"
        elif is_today and is_rest:
            status, icon = "rest", "✦ "
        elif is_tomorrow and not is_rest:
            status, icon = "tmrw", "🔒"
        elif is_future and not is_rest:
            status, icon = "    ", "🔒"
        else:
            status, icon = "    ", "· "   # rest or past unlogged: quiet dot

        table_rows.append(f"{day}  {label_col}{status} {icon}")

    lines.append("<pre>" + "\n".join(table_rows) + "</pre>")

    # ── Footer ─────────────────────────────────────────────────────────────
    sessions_done  = len([d for d in DAY_ORDER if d in logged_days])
    run_days_total = len([d for d in DAY_ORDER if all_days.get(d, {}).get("km", 0) > 0])

    lines += [
        "",
        _sec("PROGRESS"),
        f"Sessions logged:  <b>{sessions_done}</b> of {run_days_total} runs this week",
    ]

    return "\n".join(lines)


def format_c25k_week(week: dict) -> str:
    """C25K week plan."""
    wn        = week.get("week_number", "?")
    total_min = week.get("total_minutes", 30)
    run_min   = week.get("total_run_minutes_per_session", 0)
    est_km    = week.get("estimated_km_per_session", 0)
    today_key = WEEKDAY_TO_DAY.get(date.today().weekday(), "Mon")
    run_days  = {"Mon", "Wed", "Fri"}

    prog_filled = min(10, round((wn / 12) * 10))
    prog_bar    = "▓" * prog_filled + "░" * (10 - prog_filled)

    lines = [
        _hdr("C25K  ·  THIS WEEK"),
        f"<b>Week {wn} of 12</b>  ·  <code>{prog_bar}</code>",
        f"<i>~{total_min} min per session  ·  ~{run_min:.0f} min running  ·  est. {est_km} km</i>",
        "",
    ]

    rows = []
    for day in DAY_ORDER:
        session  = week.get("days", {}).get(day, {})
        name     = session.get("session", "Rest")
        is_today = day == today_key
        is_run   = day in run_days
        icon     = "▶" if is_today else ("🌱" if is_run else "·")
        today_tag = "  ← today" if is_today else ""
        rows.append(f"{icon}  {day}   {name:<18}{today_tag}")

    lines.append("<pre>" + "\n".join(rows) + "</pre>")

    return "\n".join(lines)


# ── Dashboard ──────────────────────────────────────────────────────────────

def format_dashboard(
    athlete: dict,
    week: dict,
    log_summary: dict,
    prediction=None,
) -> str:
    plan_type = athlete.get("plan_type", "full")
    if plan_type == "c25k":
        return _format_c25k_dashboard(athlete, week, log_summary)

    # ── Data ───────────────────────────────────────────────────────────────
    name          = athlete.get("name", "Athlete")
    vdot          = athlete.get("vdot", "?")
    race_distance = athlete.get("race_distance", "")
    race_date_str = athlete.get("race_date", "")
    week_num      = week.get("week_number", 1)
    total_weeks   = week.get("total_weeks", week_num)
    phase_num     = week.get("phase", 1)
    phase_name    = PHASE_NAMES.get(phase_num, "Training")
    phase_emoji   = PHASE_EMOJIS.get(phase_num, "")
    planned_vol   = week.get("planned_volume_km", 0)

    actual_vol    = log_summary.get("actual_volume_km", 0)

    dist_label = {
        "5k": "5K", "10k": "10K", "half": "Half Marathon",
        "marathon": "Marathon", "ultra": "Ultra",
        "ultra_56": "Two Oceans", "ultra_90": "Comrades",
        "parkrun": "parkrun",
    }.get(race_distance, race_distance.title() if race_distance else "Race")

    days_left  = None
    weeks_left = "?"
    if race_date_str:
        try:
            race_dt    = date.fromisoformat(race_date_str)
            days_left  = max(0, (race_dt - date.today()).days)
            weeks_left = round(days_left / 7)
        except ValueError:
            pass

    # Compliance bar
    compliance = actual_vol / max(planned_vol, 0.1)
    bar_filled = min(10, round(compliance * 10))
    bar        = "▓" * bar_filled + "░" * (10 - bar_filled)
    pct        = round(compliance * 100)

    days_display = f"{days_left}d" if days_left is not None else "?"

    # ── Build message ──────────────────────────────────────────────────────
    lines = [
        f"📊 {name}  ·  VDOT {vdot}  ·  {weeks_left} weeks to {dist_label}",
        "",
        _hdr("DASHBOARD"),
        f"<b>{name}</b>  ·  VDOT <b>{vdot}</b>",
        f"🎯 {dist_label}  ·  <b>{days_display} to race</b>",
        "",
        _sec("TRAINING"),
        f"{phase_emoji} <b>Phase {phase_num}  ·  {phase_name}</b>",
        f"Week <b>{week_num}</b> of {total_weeks}",
        "",
        _sec("THIS WEEK"),
        f"Planned   <b>{planned_vol} km</b>",
        f"Logged    <b>{actual_vol} km</b>  <code>{bar}</code>  {pct}%",
    ]

    # ── Adaptive race predictor ────────────────────────────────────────────
    if prediction is not None:
        try:
            from coach_core.engine.paces import format_prediction
            lines += [
                "",
                _sec("ADAPTIVE RACE PREDICTOR"),
                format_prediction(prediction),
            ]
        except Exception:
            pass

    # ── Two Oceans seeding batch ───────────────────────────────────────────
    if race_distance == "ultra_56" and vdot and vdot != "?":
        batch_info = get_two_oceans_batch(float(vdot))
        if batch_info:
            lines += [
                "",
                _sec("TWO OCEANS SEEDING"),
                f"Predicted HM qualifier:  <b>{batch_info['hm_time']}</b>",
                f"Estimated batch:  <b>Batch {batch_info['batch']}</b>",
            ]

    # ── Streak ─────────────────────────────────────────────────────────────
    streak   = athlete.get("streak_weeks", 0) or 0
    fire_bar = "🔥" * min(streak, 4) + "⬜" * max(0, 4 - streak)

    lines += [
        "",
        _sec("STREAK"),
        f"{fire_bar}  <b>{min(streak, 4)}/4</b> weeks",
    ]

    # ── Rewards ────────────────────────────────────────────────────────────
    from coach_core.engine.billing import loyalty_progress_bar
    weeks_done = min(streak, 4)
    loy_bar    = loyalty_progress_bar(weeks_done)

    lines += ["", _sec("REWARDS")]

    if weeks_done >= 4:
        lines += [
            f"{loy_bar}  4/4 weeks",
            f"🏆 <b>50% off Premium earned!</b>",
            f"<i>R49.50 instead of R99/month.</i>",
        ]
    else:
        wks_left = 4 - weeks_done
        lines += [
            f"{loy_bar}  {weeks_done}/4 weeks",
            f"<i>{wks_left} more week{'s' if wks_left != 1 else ''} to unlock 50% off.</i>",
        ]

    return "\n".join(lines)


def _format_c25k_dashboard(athlete: dict, week: dict, log_summary: dict) -> str:
    name       = athlete.get("name", "Athlete")
    c25k_week  = athlete.get("c25k_week", 1)
    runs       = log_summary.get("runs", [])
    sessions_done = log_summary.get("sessions_logged", 0)

    logged_days = {r["day"] for r in runs}
    today_key   = WEEKDAY_TO_DAY.get(date.today().weekday(), "Mon")
    run_days    = ["Mon", "Wed", "Fri"]

    prog_filled = min(10, round((c25k_week / 12) * 10))
    prog_bar    = "▓" * prog_filled + "░" * (10 - prog_filled)

    day_cells = []
    for d in DAY_ORDER:
        is_run = d in run_days
        if d in logged_days:
            cell = f"✅{d[:2]}"
        elif d == today_key and is_run:
            cell = f"▶{d[:2]}"
        elif not is_run:
            cell = f"·{d[:2]}"
        else:
            cell = f"○{d[:2]}"
        day_cells.append(cell)

    row1 = "  ".join(day_cells[:4])
    row2 = "  ".join(day_cells[4:])

    sessions_remaining = max(0, 3 - sessions_done)

    lines = [
        f"🌱 {name}  ·  C25K Week {c25k_week} of 12  ·  {sessions_done}/3 done",
        "",
        _hdr("DASHBOARD  ·  C25K"),
        f"<b>Week {c25k_week} of 12</b>",
        f"<code>{prog_bar}</code>",
        "",
        _sec("THIS WEEK"),
        f"Sessions done:   <b>{sessions_done} / 3</b>",
        f"Sessions left:   <b>{sessions_remaining}</b>",
        "",
        row1,
        row2,
        "",
        "<i>Complete week 12 to unlock your first race plan!</i>",
    ]
    return "\n".join(lines)


# ── Paces ──────────────────────────────────────────────────────────────────

def format_paces(paces: dict, weather: "dict | None" = None) -> str:
    """
    Training paces view.
    weather: optional TRUEPACE response dict — if provided, shows BASE vs OUTDOOR columns.
    """
    vdot = paces.get("vdot", "?")

    # Determine if we have outdoor-adjusted paces
    adjusted = {}
    has_outdoor = False
    adj_pct     = 0
    if weather and weather.get("available"):
        adjusted    = weather.get("adjusted_paces", {})
        adj_pct     = weather.get("adjustment_pct", 0)
        has_outdoor = bool(adjusted) and adj_pct != 0

    # Zone rows: (short label, full label, paces key)
    zones = [
        ("EASY", "Easy",        "easy"),
        ("MARA", "Marathon",    "marathon"),
        ("THRE", "Threshold",   "threshold"),
        ("INTV", "Interval",    "interval"),
        ("REPS", "Repetition",  "repetition"),
    ]

    lines = [
        _hdr("MY PACES"),
        f"Fitness level:  VDOT <b>{vdot}</b>",
        "",
    ]

    if has_outdoor:
        # Two-column table: BASE | OUTDOOR
        w       = weather.get("weather", {})
        temp    = w.get("temperature")
        temp_str = f"{temp:.0f}°C  · " if temp is not None else ""

        lines.append(f"<i>{temp_str}+{adj_pct}% outdoor adjustment</i>")
        lines.append("")

        # Build table as <pre> block
        hdr_row  = f"{'ZONE':<4}    {'BASE':<9} OUTDOOR"
        sep_row  = f"{'─'*4}    {'─'*9} {'─'*7}"
        table_rows = [hdr_row, sep_row]
        for label, _, key in zones:
            base_pace = paces.get(key, "—")
            out_pace  = adjusted.get(key, base_pace)
            table_rows.append(f"{label:<4}    {base_pace:<9} {out_pace}")

        lines.append("<pre>" + "\n".join(table_rows) + "</pre>")
    else:
        # Single-column table
        hdr_row  = f"{'ZONE':<4}    PACE"
        sep_row  = f"{'─'*4}    {'─'*7}"
        table_rows = [hdr_row, sep_row]
        for label, _, key in zones:
            pace = paces.get(key, "—")
            table_rows.append(f"{label:<4}    {pace}")

        lines.append("<pre>" + "\n".join(table_rows) + "</pre>")

    lines += [
        "",
        "💡 <i>TRUEPACE auto-adjusts for wind &gt; 10 km/h and heat.</i>",
    ]

    return "\n".join(lines)


# ── TRUEPACE ───────────────────────────────────────────────────────────────

def format_truepace(data: dict) -> str:
    """TRUEPACE weather block for Today view. Returns '' if nothing to show."""
    if not data.get("available"):
        reason = data.get("reason", "")
        if "No location" in reason or "Add your location" in reason:
            return f"📍 <i>TRUEPACE: {reason}</i>"
        return ""

    w       = data.get("weather", {})
    adj_pct = data.get("adjustment_pct", 0)
    temp    = w.get("temperature")
    temp_str = f"{temp:.0f}°C" if temp is not None else "?"

    if adj_pct == 0:
        return (
            f"{_DIV}\n"
            f"🌤️  <b>TRUEPACE</b>  ·  {temp_str} — ideal conditions.\n"
            "Run at your planned paces."
        )

    adjusted = data.get("adjusted_paces", {})
    planned  = data.get("planned_paces", {})
    dew      = w.get("dew_point")
    dew_str  = f"  ·  dew {dew:.0f}°C" if dew is not None else ""

    lines = [
        _DIV,
        f"🌡️  <b>TRUEPACE</b>  ·  {temp_str}{dew_str}  ·  <b>+{adj_pct}% slower</b>",
    ]

    # Adjusted pace pills
    zone_labels = [("easy", "E"), ("threshold", "T"), ("interval", "I")]
    pace_parts  = [
        f"{lbl}: <b>{adjusted[z]}</b>"
        for z, lbl in zone_labels
        if z in adjusted
    ]
    if pace_parts:
        lines.append("  " + "   |   ".join(pace_parts))

    # Warning messages
    for msg in data.get("messages", []):
        if "🔴" in msg or "⚡" in msg:
            lines.append(msg)

    return "\n".join(lines)
