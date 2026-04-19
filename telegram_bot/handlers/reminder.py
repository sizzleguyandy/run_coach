"""
Daily reminder scheduler + Mini App triggers.

Runs inside the bot process using APScheduler.
Every hour checks which athletes have a run_hour matching the current
SA hour and sends them their session for the day.

Additionally:
  Sunday evening (20:00 SA) — sends the crossing game to all athletes
  This is called externally from log.py when VO2X increases (level-up screen)
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta, date
from urllib.parse import urlencode, quote

import httpx
from telegram import Bot, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.constants import ParseMode

from telegram_bot.config import API_BASE_URL
from telegram_bot.formatting import WEEKDAY_TO_DAY

logger = logging.getLogger(__name__)

SA_TZ = timezone(timedelta(hours=2))   # UTC+2 — South Africa has no DST

# ── n8n Report Coach webhook ───────────────────────────────────────────────────
# Set N8N_REPORT_COACH_URL in .env to enable weekly + monthly AI feedback reports.
N8N_REPORT_COACH_URL = os.getenv("N8N_REPORT_COACH_URL", "")

# GitHub Pages base URL — set in .env as MINI_APP_BASE_URL
MINI_APP_BASE = os.getenv(
    "MINI_APP_BASE_URL",
    "https://sizzleguyandy.github.io/run-coach-apps"
).rstrip("/")

REST_MESSAGES = [
    "Rest day today. Recovery is where adaptation happens — eat well, sleep, stay off your feet.",
    "Rest day. Your muscles are rebuilding. Enjoy the break — you earned it.",
    "Rest day today. A good coach would say do nothing. So: do nothing.",
    "Rest day. Hydrate, eat, sleep. Your next session will feel better for it.",
    "Rest day. The gains happen during recovery, not the run. Trust the process.",
]

# ── Race Prep milestone days-to-race (based on days remaining) ────────────────
# Tuple of (days_to_race, label, send_hour)
# send_hour: hour (SA time) when the message fires. None = athlete's run_hour.
RACE_PREP_MILESTONES: list[tuple[int, str, int | None]] = [
    (84, "12_weeks",    None),  # 12 weeks to go — fire at athlete's run_hour
    (56, "8_weeks",     None),  # 8 weeks to go
    (42, "6_weeks",     None),  # 6 weeks to go
    (28, "4_weeks",     None),  # 4 weeks to go
    (14, "2_weeks",     None),  # 2 weeks to go — taper starts soon
    (3,  "3_days",      None),  # 3 days out
    (0,  "race_morning", 5),    # Race day — fire at 5 AM
]


# ── API helpers ───────────────────────────────────────────────────────────────

async def _fetch_all_athletes() -> list[dict]:
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{API_BASE_URL}/athletes/all")
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return []


async def _fetch_today_plan(telegram_id: str) -> dict | None:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(f"{API_BASE_URL}/plan/{telegram_id}/current")
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return None


async def _fetch_week_summary(telegram_id: str, week_number: int) -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(f"{API_BASE_URL}/log/{telegram_id}/week/{week_number}/summary")
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return {}


# ── Mini App URL builders ─────────────────────────────────────────────────────

def crossing_url(
    weeks: int,
    lives: int,
    cur_week: int,
    total_weeks: int,
    race_name: str,
) -> str:
    params = urlencode({
        "weeks":       max(1, weeks),
        "lives":       max(1, min(5, lives)),
        "week":        cur_week,
        "total_weeks": total_weeks,
        "race":        race_name,
    })
    return f"{MINI_APP_BASE}/crossing.html?{params}"


def levelup_url(vo2x_old: float, vo2x_new: float, name: str, source: str) -> str:
    params = urlencode({
        "from":   vo2x_old,
        "to":     vo2x_new,
        "name":   name,
        "source": source,
    })
    return f"{MINI_APP_BASE}/levelup.html?{params}"


# ── Message builders ─────────────────────────────────────────────────────────

def _build_reminder_message(athlete: dict, week: dict, today_key: str) -> str:
    name      = athlete.get("name", "Runner")
    days      = week.get("days", {})
    plan_type = week.get("plan_type", "full")
    session   = days.get(today_key, {})
    session_name = session.get("session", "Rest")
    km    = session.get("km", 0)
    notes = session.get("notes", "")
    is_rest = km == 0 or "Rest" in session_name

    now_sa = datetime.now(SA_TZ)
    if now_sa.hour < 10:   greeting = "Good morning"
    elif now_sa.hour < 14: greeting = "Morning"
    else:                  greeting = "Hey"

    if is_rest:
        import random
        return f"😴 {today_key}: Rest day — recovery is where adaptation happens.\n\n{random.choice(REST_MESSAGES)}"

    if plan_type == "c25k":
        total_min = week.get("total_minutes", 30)
        return (
            f"🌱 {today_key}: C25K run day — ~{total_min} min\n\n"
            f"{notes}\n\n"
            "Tap /today for full details."
        )

    lines = [
        f"🏃 {today_key}: {session_name} {km}km — don't skip this one",
        "",
        f"{greeting} <b>{name}</b>!",
        "",
        f"<b>{session_name}</b>  ·  <i>{km} km</i>",
    ]
    if notes:
        if "|" in notes:
            lines.append("")
            for part in [p.strip() for p in notes.split("|")]:
                lines.append(f"  {part}")
        else:
            short = notes[:150] + "..." if len(notes) > 150 else notes
            lines += ["", short]
    lines += ["", "Tap /today for paces and weather adjustment."]
    return "\n".join(lines)


def _build_sunday_game_message(
    athlete: dict,
    week: dict,
    runs_this_week: int,
    weeks_to_race: int,
    actual_km: float = 0,
) -> tuple[str, InlineKeyboardMarkup]:
    """Build the Sunday evening weekly recap + game button."""
    name       = athlete.get("name", "Runner")
    week_num   = week.get("week_number", 1)
    total_wks  = week.get("total_weeks") or 18
    # Use preset race name if available
    race_label = quote(athlete.get("race_name") or "Race Day")

    # Compliance summary
    days       = week.get("days", {})
    total_days = sum(1 for d in days.values() if (d.get("km") or 0) > 0)

    lives   = max(1, min(5, runs_this_week))
    roads   = max(1, weeks_to_race)

    km_str = f" · {actual_km:.0f}km logged" if actual_km > 0 else ""

    # Loyalty progress line
    from coach_core.engine.billing import calculate_loyalty_discount, loyalty_progress_bar
    streak  = athlete.get("streak_weeks", 0) or 0
    loyalty = calculate_loyalty_discount(streak)
    l_bar   = loyalty_progress_bar(loyalty["weeks"])
    if loyalty["weeks"] >= 4:
        loyalty_line = f"💳 {l_bar} 4/4 — <b>50% off Premium earned!</b>"
    elif loyalty["weeks"] > 0:
        remaining = 4 - loyalty["weeks"]
        loyalty_line = (
            f"💳 {l_bar} {loyalty['weeks']}/4 weeks"
            + (f" — {loyalty['pct']}% off so far" if loyalty["pct"] > 0 else
               f" — {remaining} more to unlock 50% off")
        )
    else:
        loyalty_line = "💳 ░░░░ 0/4 — log all runs this month for 50% off Premium"

    lines = [
        f"✅ Week {week_num} done — {runs_this_week}/{total_days} runs{km_str}",
        "",
        f"<b>Week {week_num} complete, {name}!</b>",
        f"Weeks to race: <b>{roads}</b>",
        "",
        loyalty_line,
        "",
        "Your weekly challenge is ready 🕹️",
        f"<b>{lives} {'life' if lives==1 else 'lives'}</b> — one for each run you logged.",
        f"<b>{roads} roads</b> to cross — one for each week remaining.",
    ]

    msg = "\n".join(lines)

    url = crossing_url(
        weeks=roads,
        lives=lives,
        cur_week=week_num,
        total_weeks=total_wks,
        race_name=athlete.get("race_name") or "Race Day",
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🕹️ Play — {roads} roads · {lives} lives",
            web_app={"url": url},
        )
    ]])

    return msg, keyboard


# ── VO2X level-up trigger (called from log.py) ────────────────────────────────

async def send_levelup_notification(
    bot: Bot,
    telegram_id: str,
    name: str,
    vo2x_old: float,
    vo2x_new: float,
    source: str = "adjusted",
) -> None:
    """
    Send the level-up Mini App button when VO2X crosses an integer boundary.
    e.g. 39.8 → 40.1 triggers it; 39.1 → 39.8 does not.
    """
    if int(vo2x_new) <= int(vo2x_old):
        return   # no integer boundary crossed — no notification

    url = levelup_url(vo2x_old, vo2x_new, name, source)

    source_label = "your race result" if source == "race" else "your training consistency"
    msg = (
        f"⚡ VO2X {int(vo2x_old)} → {int(vo2x_new)} — you're getting faster, {name}!\n\n"
        f"Based on {source_label}.\n"
        "Your training paces have been updated."
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"⚡ VO2X {int(vo2x_old)} → {int(vo2x_new)} — See your upgrade",
            web_app={"url": url},
        )
    ]])

    try:
        await bot.send_message(
            chat_id=telegram_id,
            text=msg,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard,
        )
        logger.info(f"Level-up notification sent to {telegram_id}: {vo2x_old} → {vo2x_new}")
    except Exception as e:
        logger.warning(f"Level-up notification failed for {telegram_id}: {e}")


# ── Race Prep message builder ─────────────────────────────────────────────────

def _build_race_prep_message(
    athlete: dict,
    milestone_key: str,
    days_to_race: int,
    vo2x: float | None,
    preset_race_id: str | None,
    race_date_str: str | None,
) -> str:
    """
    Build a proactive RACE PREP push message for a countdown milestone.
    Uses race knowledge RAG to inject course-specific tips.
    """
    name       = athlete.get("name", "Runner")
    race_label = athlete.get("race_name") or "your race"
    weeks_left = days_to_race // 7

    # Load race intelligence for contextual tips
    race_tips = ""
    checkpoint_text = ""
    try:
        from coach_core.engine.race_knowledge import get_race_context
        ctx = get_race_context(
            preset_race_id=preset_race_id,
            vo2x=vo2x,
            race_date_str=race_date_str,
        )
        checkpoint_text = ctx.get("checkpoint_summary", "")
        # Extract a relevant section from the knowledge doc
        knowledge = ctx.get("knowledge_text", "")
        if knowledge:
            race_tips = _extract_prep_tips(knowledge, milestone_key)
    except Exception:
        pass

    # ── Message by milestone ──────────────────────────────────────────────────

    if milestone_key == "12_weeks":
        lines = [
            f"🗓️ <b>12 weeks to {race_label}!</b>",
            "",
            f"The serious prep begins, {name}. The next 12 weeks will define your race.",
            "",
            "This is the time to:",
            "  • Lock in your long run day and protect it every week",
            "  • Build mileage conservatively — 10% max per week",
            "  • Start practising your race-day nutrition strategy on long runs",
        ]
        if race_tips:
            lines += ["", race_tips]

    elif milestone_key == "8_weeks":
        lines = [
            f"📍 <b>8 weeks to {race_label}!</b>",
            "",
            f"Two months out, {name}. This is when real race fitness is built.",
            "",
            "Focus this block on:",
            "  • Completing your longest training runs for this cycle",
            "  • Testing your race-day shoes and nutrition — nothing new on race day",
            "  • Keeping easy runs genuinely easy to absorb the harder sessions",
        ]
        if race_tips:
            lines += ["", race_tips]

    elif milestone_key == "6_weeks":
        lines = [
            f"⏱️ <b>6 weeks to {race_label}!</b>",
            "",
            f"Six weeks is your last window for big training stimulus, {name}.",
            "After this, your body needs time to absorb what you've built.",
            "",
            "Priorities now:",
            "  • Your last big long run is in the next 2–3 weeks",
            "  • Start visualising the race course and your pacing strategy",
            "  • Sort logistics: accommodation, transport, race expo dates",
        ]
        if race_tips:
            lines += ["", race_tips]

    elif milestone_key == "4_weeks":
        lines = [
            f"🎯 <b>4 weeks to {race_label}!</b>",
            "",
            f"One month to go, {name}. Taper is coming — but not yet.",
            "",
            "  • Complete your last quality sessions this week or next",
            "  • Your longest run of the plan should be done or happening soon",
            "  • Confirm your race-day kit, nutrition, and logistics",
        ]
        if checkpoint_text:
            lines += ["", "<b>Your personalised race targets:</b>", checkpoint_text]
        elif race_tips:
            lines += ["", race_tips]

    elif milestone_key == "2_weeks":
        lines = [
            f"🏁 <b>2 weeks to {race_label}!</b>",
            "",
            f"Taper time, {name}. The hay is in the barn — trust your training.",
            "",
            "Taper rules:",
            "  • Volume drops but keep the intensity in short, sharp sessions",
            "  • Sleep more than usual — recovery peaks during taper",
            "  • Don't try anything new: food, kit, or supplements",
            "  • A 'taper tantrum' is normal — restlessness and doubt are signs it's working",
        ]
        if checkpoint_text:
            lines += ["", "<b>Your race targets:</b>", checkpoint_text]
        if race_tips:
            lines += ["", race_tips]

    elif milestone_key == "3_days":
        lines = [
            f"🚨 <b>3 days to {race_label}!</b>",
            "",
            f"Race week, {name}. Taper is nearly done.",
            "",
            "Checklist:",
            "  • Eat a high-carbohydrate diet for the next 48 hours",
            "  • Hydrate well — aim for pale yellow urine",
            "  • Lay out all your race kit tonight and check it twice",
            "  • Plan your pre-race meal and morning routine",
            "  • Get to bed early tonight and tomorrow night",
        ]
        if race_tips:
            lines += ["", "<b>Race day logistics:</b>", race_tips]
        if checkpoint_text:
            lines += ["", "<b>Your targets:</b>", checkpoint_text]

    elif milestone_key == "race_morning":
        lines = [
            f"🏆 <b>Today is race day!</b>",
            "",
            f"This is what you trained for, {name}. Go run your race.",
            "",
            "Final reminders:",
            "  • Eat your pre-race meal 2–3 hours before the gun",
            "  • Start conservatively — the crowd will want to pull you out fast",
            "  • Stick to your nutrition plan: fuel before you need it",
            "  • Trust the training — your body knows what to do",
        ]
        if checkpoint_text:
            lines += ["", "<b>Your checkpoint targets:</b>", checkpoint_text]

    else:
        return ""

    return "\n".join(lines)


def _extract_prep_tips(knowledge_text: str, milestone_key: str) -> str:
    """
    Extract the most relevant race knowledge snippet for this milestone stage.
    Maps milestone to the section most useful at that point in prep.
    """
    section_map = {
        "12_weeks": "## Pacing Strategy",
        "8_weeks":  "## Nutrition",
        "6_weeks":  "## Key Features",
        "4_weeks":  "## Common Mistakes",
        "2_weeks":  "## Common Mistakes",
        "3_days":   "## Logistics",
        "race_morning": "## Pacing Strategy",
    }

    target_section = section_map.get(milestone_key)
    if not target_section:
        return ""

    lines = knowledge_text.split("\n")
    capturing = False
    result = []

    for line in lines:
        if line.strip() == target_section:
            capturing = True
            result.append(f"<b>From the race guide:</b>")
            continue
        if capturing:
            if line.startswith("## ") and line.strip() != target_section:
                break
            if line.strip():
                result.append(line)
            if len(result) > 8:  # cap at ~8 lines of tips
                break

    return "\n".join(result).strip()


async def _send_race_prep_if_due(
    bot: Bot,
    athlete: dict,
    cur_hour: int,
) -> bool:
    """
    Check if any RACE PREP milestone is due today for this athlete.
    Returns True if a message was sent.
    """
    from datetime import date as _date
    race_date_str = athlete.get("race_date")
    if not race_date_str:
        return False

    try:
        race_date = _date.fromisoformat(race_date_str)
    except Exception:
        return False

    days_to_race = (race_date - _date.today()).days
    if days_to_race < 0:
        return False   # race has passed

    athlete_run_hour = athlete.get("run_hour") or 7

    # Check each milestone
    for milestone_days, milestone_key, milestone_hour in RACE_PREP_MILESTONES:
        if days_to_race != milestone_days:
            continue

        # Check if this is the right hour to fire
        fire_hour = milestone_hour if milestone_hour is not None else athlete_run_hour
        if cur_hour != fire_hour:
            continue

        # We have a match — build and send the message
        tid = athlete.get("telegram_id")
        vo2x = athlete.get("vo2x")
        preset_race_id = athlete.get("preset_race_id")

        msg = _build_race_prep_message(
            athlete=athlete,
            milestone_key=milestone_key,
            days_to_race=days_to_race,
            vo2x=vo2x,
            preset_race_id=preset_race_id,
            race_date_str=race_date_str,
        )
        if not msg:
            return False

        keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Ask coach",  callback_data="coach_chat"),
            InlineKeyboardButton("📋 View plan",  callback_data="today"),
        ]])

        try:
            await bot.send_message(
                chat_id=tid,
                text=msg,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
            )
            logger.info(
                f"Race prep [{milestone_key}] sent to {tid} "
                f"({days_to_race}d to race)"
            )
            return True
        except Exception as e:
            logger.warning(f"Race prep send failed for {tid}: {e}")
            return False

    return False


# ── Report Coach helpers ──────────────────────────────────────────────────────

def _is_last_sunday_of_month(d) -> bool:
    """Return True if d is the last Sunday of its calendar month."""
    from datetime import timedelta
    return d.weekday() == 6 and (d + timedelta(days=7)).month != d.month


def _runs_summary_str(runs: list[dict]) -> str:
    """Format logged runs as a compact pipe-separated string for the AI."""
    parts = []
    for r in sorted(runs, key=lambda x: x.get("day", "")):
        day  = r.get("day", "?")
        sess = r.get("session", "Run")
        km   = r.get("km", 0)
        rpe  = r.get("rpe")
        rpe_str = f" RPE {rpe}" if rpe else ""
        parts.append(f"{day}: {sess} {km}km{rpe_str}")
    return " | ".join(parts) if parts else "No sessions logged"


async def _call_report_coach(payload: dict) -> str:
    """POST payload to the report-coach n8n webhook; return the AI feedback text."""
    if not N8N_REPORT_COACH_URL:
        return ""
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            r = await client.post(N8N_REPORT_COACH_URL, json=payload)
            if r.status_code == 200:
                data = r.json()
                reply = data.get("output") or data.get("coached_message") or data.get("text", "")
                if reply:
                    return reply.strip()
    except Exception as e:
        logger.warning(f"report_coach webhook failed: {type(e).__name__}: {e}")
    return ""


async def _send_weekly_reports(bot: Bot, athletes: list[dict]) -> None:
    """
    Send AI-generated weekly feedback to every athlete who has logged at least
    one session this week.  Fires on Sunday at 19:00 SA.
    """
    if not N8N_REPORT_COACH_URL:
        logger.info("Weekly AI report skipped — N8N_REPORT_COACH_URL not configured")
        return

    from datetime import date
    sent = failed = 0

    for athlete in athletes:
        tid = athlete.get("telegram_id")
        if not tid:
            continue
        try:
            # Fetch plan + week summary concurrently
            async with httpx.AsyncClient(timeout=12) as client:
                week_r    = await client.get(f"{API_BASE_URL}/plan/{tid}/current")
                if week_r.status_code != 200:
                    continue
                week      = week_r.json()
                week_num  = week.get("week_number", 1)
                sum_r     = await client.get(f"{API_BASE_URL}/log/{tid}/week/{week_num}/summary")
                summary   = sum_r.json() if sum_r.status_code == 200 else {}

            sessions_logged = summary.get("sessions_logged", 0)
            if sessions_logged == 0:
                continue   # nothing to report on

            actual_km   = summary.get("actual_volume_km", 0.0)
            planned_km  = week.get("planned_volume_km") or week.get("total_km") or 0.0
            avg_rpe     = summary.get("avg_rpe")
            runs        = summary.get("runs", [])
            total_weeks = week.get("total_weeks", 18)

            compliance_pct = round((actual_km / max(planned_km, 0.1)) * 100) if planned_km else 0

            race_date_str = athlete.get("race_date")
            try:
                weeks_to_race = max(0, round((date.fromisoformat(race_date_str) - date.today()).days / 7)) if race_date_str else max(0, total_weeks - week_num)
            except Exception:
                weeks_to_race = max(0, total_weeks - week_num)

            payload = {
                "report_type":       "weekly",
                "athlete_name":      athlete.get("name", "Runner"),
                "race_name":         athlete.get("race_name") or "your race",
                "week_number":       week_num,
                "total_weeks":       total_weeks,
                "weeks_to_race":     weeks_to_race,
                "vo2x":              athlete.get("vo2x"),
                "planned_volume_km": round(planned_km, 1),
                "actual_volume_km":  round(actual_km, 1),
                "compliance_pct":    compliance_pct,
                "sessions_logged":   sessions_logged,
                "avg_rpe":           avg_rpe,
                "runs_summary":      _runs_summary_str(runs),
                "streak_weeks":      athlete.get("streak_weeks", 0) or 0,
                "training_profile":  athlete.get("training_profile", "balanced"),
            }

            feedback = await _call_report_coach(payload)
            if not feedback:
                continue

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("💬 Ask coach", callback_data="coach_chat"),
                InlineKeyboardButton("📋 Plan",      callback_data="today"),
            ]])

            header = f"📊 <b>Week {week_num} Training Report</b>\n{'─' * 22}\n\n"

            await bot.send_message(
                chat_id=tid,
                text=header + feedback,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            sent += 1
            await asyncio.sleep(0.06)

        except Exception as e:
            failed += 1
            logger.warning(f"Weekly AI report failed for {tid}: {e}")

    logger.info(f"Weekly AI reports: {sent} sent, {failed} failed")


async def _send_monthly_reports(bot: Bot, athletes: list[dict]) -> None:
    """
    Send AI-generated monthly feedback to every athlete with data for the past
    1–4 weeks.  Fires on the last Sunday of each month at 08:00 SA.
    """
    if not N8N_REPORT_COACH_URL:
        logger.info("Monthly AI report skipped — N8N_REPORT_COACH_URL not configured")
        return

    from datetime import date
    sent = failed = 0

    for athlete in athletes:
        tid = athlete.get("telegram_id")
        if not tid:
            continue
        try:
            async with httpx.AsyncClient(timeout=12) as client:
                week_r = await client.get(f"{API_BASE_URL}/plan/{tid}/current")
                if week_r.status_code != 200:
                    continue
                week = week_r.json()

            week_num    = week.get("week_number", 1)
            total_weeks = week.get("total_weeks", 18)
            planned_km_cur = week.get("planned_volume_km") or 0.0

            # Fetch up to 4 most recent weeks of logged data
            weeks_to_fetch = min(4, week_num)
            week_summaries: list[dict] = []

            async with httpx.AsyncClient(timeout=15) as client:
                for wn in range(week_num - weeks_to_fetch + 1, week_num + 1):
                    if wn < 1:
                        continue
                    r = await client.get(f"{API_BASE_URL}/log/{tid}/week/{wn}/summary")
                    if r.status_code == 200:
                        s = r.json()
                        if s.get("sessions_logged", 0) > 0:
                            week_summaries.append(s)

            if not week_summaries:
                continue   # athlete hasn't logged enough data

            # Aggregate
            total_actual   = sum(w.get("actual_volume_km", 0) for w in week_summaries)
            total_sessions = sum(w.get("sessions_logged", 0) for w in week_summaries)
            rpe_vals       = [w["avg_rpe"] for w in week_summaries if w.get("avg_rpe")]
            avg_rpe        = round(sum(rpe_vals) / len(rpe_vals), 1) if rpe_vals else None
            total_planned  = planned_km_cur * len(week_summaries)
            avg_compliance = round((total_actual / max(total_planned, 0.1)) * 100) if total_planned else None

            # Format the week-by-week summary string
            month_lines = []
            for ws in week_summaries:
                wn_  = ws.get("week_number", "?")
                akm  = ws.get("actual_volume_km", 0)
                sess = ws.get("sessions_logged", 0)
                rpe_ = ws.get("avg_rpe", "—")
                month_lines.append(f"Week {wn_}: {akm}km · {sess} sessions · RPE {rpe_}")
            month_summary = "\n".join(month_lines)

            race_date_str = athlete.get("race_date")
            try:
                weeks_to_race = max(0, round((date.fromisoformat(race_date_str) - date.today()).days / 7)) if race_date_str else max(0, total_weeks - week_num)
            except Exception:
                weeks_to_race = max(0, total_weeks - week_num)

            payload = {
                "report_type":        "monthly",
                "athlete_name":       athlete.get("name", "Runner"),
                "race_name":          athlete.get("race_name") or "your race",
                "current_week":       week_num,
                "total_weeks":        total_weeks,
                "weeks_to_race":      weeks_to_race,
                "vo2x":               athlete.get("vo2x"),
                "month_summary":      month_summary,
                "total_actual_km":    round(total_actual, 1),
                "total_planned_km":   round(total_planned, 1) if total_planned else None,
                "avg_compliance_pct": avg_compliance,
                "avg_rpe":            avg_rpe,
                "total_sessions":     total_sessions,
                "weeks_in_report":    len(week_summaries),
                "streak_weeks":       athlete.get("streak_weeks", 0) or 0,
                "training_profile":   athlete.get("training_profile", "balanced"),
            }

            feedback = await _call_report_coach(payload)
            if not feedback:
                continue

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("💬 Ask coach", callback_data="coach_chat"),
                InlineKeyboardButton("📋 Plan",      callback_data="today"),
            ]])

            month_label = date.today().strftime("%B")
            header      = f"📅 <b>{month_label} Training Report</b>\n{'─' * 22}\n\n"

            await bot.send_message(
                chat_id=tid,
                text=header + feedback,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            sent += 1
            await asyncio.sleep(0.06)

        except Exception as e:
            failed += 1
            logger.warning(f"Monthly AI report failed for {tid}: {e}")

    logger.info(f"Monthly AI reports: {sent} sent, {failed} failed")


# ── Race Eve report ───────────────────────────────────────────────────────────

async def _fetch_race_day_weather(
    lat: float,
    lon: float,
    race_hour: int = 6,
) -> dict | None:
    """
    Fetch tomorrow's weather forecast at the given hour from Open-Meteo.
    Returns dict with temperature, dew_point, wind_kph, precip_pct, condition_str, heat_warning.
    """
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        "&hourly=temperature_2m,dew_point_2m,wind_speed_10m,precipitation_probability"
        "&timezone=auto"
        "&forecast_days=2"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url)
            if r.status_code != 200:
                return None
            data = r.json()

        times  = data["hourly"]["time"]
        temps  = data["hourly"]["temperature_2m"]
        dews   = data["hourly"]["dew_point_2m"]
        winds  = data["hourly"]["wind_speed_10m"]
        precip = data["hourly"]["precipitation_probability"]

        # Find tomorrow's entry at the target race_hour
        from datetime import date, timedelta
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        target   = f"{tomorrow}T{race_hour:02d}:00"

        best_idx = 0
        for i, t in enumerate(times):
            if t == target:
                best_idx = i
                break
            # fallback: pick first entry that starts with tomorrow
            if t.startswith(tomorrow):
                best_idx = i

        temp       = temps[best_idx]
        dew        = dews[best_idx]
        wind_kph   = winds[best_idx]
        precip_pct = precip[best_idx]

        # Build a human-readable condition string
        if temp >= 28:
            heat_label = "hot"
        elif temp >= 22:
            heat_label = "warm"
        elif temp >= 15:
            heat_label = "mild"
        else:
            heat_label = "cool"

        if dew >= 16:
            humid_label = "very humid"
        elif dew >= 12:
            humid_label = "humid"
        elif dew >= 8:
            humid_label = "moderate humidity"
        else:
            humid_label = "dry"

        wind_label = "strong wind" if wind_kph > 25 else ("moderate wind" if wind_kph > 15 else "light wind")
        condition  = f"{heat_label}, {humid_label}, {wind_label}"

        from coach_core.engine.truepace import compute_adjustment
        adj = compute_adjustment(temp, dew)

        return {
            "temperature":     temp,
            "dew_point":       dew,
            "wind_kph":        round(wind_kph, 1),
            "precip_pct":      precip_pct,
            "condition_str":   condition,
            "adjustment_pct":  adj.adjustment_pct,
            "heat_warning":    adj.high_heat_warning,
        }
    except Exception as e:
        logger.warning(f"_fetch_race_day_weather failed: {e}")
        return None


async def _send_race_eve_reports(bot, athletes: list[dict]) -> None:
    """
    Send the pre-race briefing to every athlete whose race is tomorrow.
    Fires daily at 15:00 SA — only hits athletes with race_date == tomorrow.
    Does NOT conflict with RACE_PREP_MILESTONES (nearest is 3 days out and race morning).
    """
    if not N8N_REPORT_COACH_URL:
        logger.info("Race eve report skipped — N8N_REPORT_COACH_URL not configured")
        return

    from datetime import date, timedelta
    tomorrow = date.today() + timedelta(days=1)

    sent = failed = 0
    for athlete in athletes:
        race_date_str = athlete.get("race_date")
        if not race_date_str:
            continue
        try:
            if date.fromisoformat(race_date_str) != tomorrow:
                continue
        except Exception:
            continue

        tid = athlete.get("telegram_id")
        if not tid:
            continue

        try:
            # ── Fetch plan + current week summary ────────────────────────
            async with httpx.AsyncClient(timeout=12) as client:
                week_r = await client.get(f"{API_BASE_URL}/plan/{tid}/current")
                week   = week_r.json() if week_r.status_code == 200 else {}
                week_num = week.get("week_number", 1)
                total_weeks = week.get("total_weeks", 18)

            # ── Fetch last 4 weeks for training summary ───────────────────
            weeks_to_fetch = min(4, week_num)
            week_summaries: list[dict] = []
            async with httpx.AsyncClient(timeout=15) as client:
                for wn in range(week_num - weeks_to_fetch + 1, week_num + 1):
                    if wn < 1:
                        continue
                    r = await client.get(f"{API_BASE_URL}/log/{tid}/week/{wn}/summary")
                    if r.status_code == 200:
                        s = r.json()
                        if s.get("sessions_logged", 0) > 0:
                            week_summaries.append(s)

            # ── Race prediction ───────────────────────────────────────────
            predicted_low = predicted_high = None
            try:
                from coach_core.engine.predictor import (
                    predict, PredictionInput, PRESET_HILL_FACTORS,
                )
                from coach_core.engine.race_presets import RACE_PRESETS
                preset_id   = athlete.get("preset_race_id")
                race_dist_str = athlete.get("race_distance", "marathon")
                hilliness   = athlete.get("race_hilliness", "low")

                hill_factor = PRESET_HILL_FACTORS.get(preset_id) if preset_id else {
                    "flat": 0.02, "low": 0.045, "rolling": 0.06,
                    "medium": 0.08, "hilly": 0.10, "high": 0.115, "mountain": 0.15,
                }.get(hilliness, 0.045)

                # Get distance_km from preset or fall back to athlete field
                if preset_id and preset_id in RACE_PRESETS:
                    dist_km = RACE_PRESETS[preset_id]["exact_distance_km"]
                else:
                    dist_km = {
                        "5k": 5.0, "10k": 10.0, "half": 21.0975,
                        "marathon": 42.195, "ultra_56": 56.0, "ultra_90": 90.0,
                    }.get(race_dist_str, 42.195)

                vo2x = athlete.get("vo2x") or 35.0
                weekly_km  = athlete.get("current_weekly_mileage") or 30.0
                longest_km = weekly_km * 0.4

                result = predict(PredictionInput(
                    race_name        = athlete.get("race_name") or race_dist_str,
                    race_distance_km = dist_km,
                    hill_factor      = hill_factor,
                    race_date        = date.today(),  # race is tomorrow — use today as proxy
                    direct_vo2x      = vo2x,
                    weekly_mileage_km = weekly_km,
                    longest_run_km   = longest_km,
                    plan_type        = athlete.get("training_profile") or "conservative",
                ))
                predicted_low  = result.low_fmt()
                predicted_high = result.high_fmt()
            except Exception as pred_err:
                logger.warning(f"Race eve prediction failed for {tid}: {pred_err}")

            # ── Race-day weather ──────────────────────────────────────────
            weather = None
            try:
                from coach_core.engine.race_presets import RACE_COORDS
                preset_id = athlete.get("preset_race_id")
                if preset_id and preset_id in RACE_COORDS:
                    lat, lon = RACE_COORDS[preset_id]
                elif athlete.get("latitude") and athlete.get("longitude"):
                    lat, lon = athlete["latitude"], athlete["longitude"]
                else:
                    lat = lon = None

                if lat and lon:
                    weather = await _fetch_race_day_weather(lat, lon, race_hour=6)
            except Exception as w_err:
                logger.warning(f"Race eve weather failed for {tid}: {w_err}")

            # ── Race knowledge (RAG) ──────────────────────────────────────
            knowledge_text = checkpoint_summary = ""
            try:
                from coach_core.engine.race_knowledge import get_race_context
                ctx = get_race_context(
                    preset_race_id = athlete.get("preset_race_id"),
                    vo2x           = athlete.get("vo2x"),
                    race_date_str  = race_date_str,
                )
                knowledge_text    = ctx.get("knowledge_text", "")
                checkpoint_summary = ctx.get("checkpoint_summary", "")
            except Exception:
                pass

            # ── Training summary string ───────────────────────────────────
            month_lines = []
            for ws in week_summaries:
                wn_  = ws.get("week_number", "?")
                akm  = ws.get("actual_volume_km", 0)
                sess = ws.get("sessions_logged", 0)
                rpe_ = ws.get("avg_rpe", "—")
                month_lines.append(f"Week {wn_}: {akm}km · {sess} sessions · RPE {rpe_}")
            training_summary = "\n".join(month_lines) if month_lines else "No sessions logged in recent weeks"

            total_actual   = sum(w.get("actual_volume_km", 0) for w in week_summaries)
            total_sessions = sum(w.get("sessions_logged", 0) for w in week_summaries)
            rpe_vals       = [w["avg_rpe"] for w in week_summaries if w.get("avg_rpe")]
            avg_rpe        = round(sum(rpe_vals) / len(rpe_vals), 1) if rpe_vals else None

            # ── Build payload ─────────────────────────────────────────────
            payload: dict = {
                "report_type":       "race_eve",
                "athlete_name":      athlete.get("name", "Runner"),
                "race_name":         athlete.get("race_name") or "your race",
                "race_date":         race_date_str,
                "vo2x":              athlete.get("vo2x"),
                "predicted_low":     predicted_low,
                "predicted_high":    predicted_high,
                "race_distance_km":  dist_km if 'dist_km' in dir() else None,
                "race_hilliness":    athlete.get("race_hilliness", "low"),
                "training_summary":  training_summary,
                "total_km_4wk":      round(total_actual, 1),
                "total_sessions_4wk": total_sessions,
                "avg_rpe_4wk":       avg_rpe,
                "streak_weeks":      athlete.get("streak_weeks", 0) or 0,
                "training_profile":  athlete.get("training_profile", "balanced"),
                "weeks_trained":     total_weeks,
                "race_knowledge_text": knowledge_text,
                "checkpoint_summary":  checkpoint_summary,
            }

            # Merge weather if available
            if weather:
                payload.update({
                    "weather_temp_c":    weather["temperature"],
                    "weather_dew_c":     weather["dew_point"],
                    "weather_wind_kph":  weather["wind_kph"],
                    "weather_precip_pct": weather["precip_pct"],
                    "weather_condition": weather["condition_str"],
                    "weather_adj_pct":   weather["adjustment_pct"],
                    "weather_heat_warning": weather["heat_warning"],
                })
            else:
                payload["weather_condition"] = "unavailable"

            feedback = await _call_report_coach(payload)
            if not feedback:
                continue

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("💬 Ask coach", callback_data="coach_chat"),
                InlineKeyboardButton("📋 Plan",      callback_data="today"),
            ]])

            header = f"🏁 <b>Race Eve — {athlete.get('race_name') or 'Race Day'} Tomorrow</b>\n{'─' * 26}\n\n"

            await bot.send_message(
                chat_id=tid,
                text=header + feedback,
                parse_mode=ParseMode.HTML,
                reply_markup=kb,
            )
            sent += 1
            await asyncio.sleep(0.06)

        except Exception as e:
            failed += 1
            logger.warning(f"Race eve report failed for {tid}: {e}")

    logger.info(f"Race eve reports: {sent} sent, {failed} failed")


# ── Hidden test commands (/weekreport, /monthreport) ─────────────────────────

async def cmd_weekreport(update, context) -> None:
    """
    Hidden command: /weekreport
    Immediately sends the weekly AI report for the requesting user.
    Not listed in set_bot_commands — for testing only.
    """
    from datetime import date
    telegram_id = str(update.effective_user.id)
    msg = await update.effective_message.reply_text("⏳ Generating weekly report…")

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            ath_r  = await client.get(f"{API_BASE_URL}/athlete/{telegram_id}")
            week_r = await client.get(f"{API_BASE_URL}/plan/{telegram_id}/current")

        if ath_r.status_code != 200:
            await msg.edit_text("❌ No athlete profile found. Complete onboarding first.")
            return
        if week_r.status_code != 200:
            await msg.edit_text("❌ No training plan found.")
            return

        athlete  = ath_r.json()
        week     = week_r.json()
        week_num = week.get("week_number", 1)

        async with httpx.AsyncClient(timeout=10) as client:
            sum_r   = await client.get(f"{API_BASE_URL}/log/{telegram_id}/week/{week_num}/summary")
            summary = sum_r.json() if sum_r.status_code == 200 else {}

        actual_km        = summary.get("actual_volume_km", 0.0)
        planned_km       = week.get("planned_volume_km") or week.get("total_km") or 0.0
        sessions_logged  = summary.get("sessions_logged", 0)
        avg_rpe          = summary.get("avg_rpe")
        runs             = summary.get("runs", [])
        total_weeks      = week.get("total_weeks", 18)
        compliance_pct   = round((actual_km / max(planned_km, 0.1)) * 100) if planned_km else 0

        race_date_str = athlete.get("race_date")
        try:
            weeks_to_race = max(0, round((date.fromisoformat(race_date_str) - date.today()).days / 7)) if race_date_str else max(0, total_weeks - week_num)
        except Exception:
            weeks_to_race = max(0, total_weeks - week_num)

        payload = {
            "report_type":       "weekly",
            "athlete_name":      athlete.get("name", "Runner"),
            "race_name":         athlete.get("race_name") or "your race",
            "week_number":       week_num,
            "total_weeks":       total_weeks,
            "weeks_to_race":     weeks_to_race,
            "vo2x":              athlete.get("vo2x"),
            "planned_volume_km": round(planned_km, 1),
            "actual_volume_km":  round(actual_km, 1),
            "compliance_pct":    compliance_pct,
            "sessions_logged":   sessions_logged,
            "avg_rpe":           avg_rpe,
            "runs_summary":      _runs_summary_str(runs),
            "streak_weeks":      athlete.get("streak_weeks", 0) or 0,
            "training_profile":  athlete.get("training_profile", "balanced"),
        }

        feedback = await _call_report_coach(payload)

        if not feedback:
            await msg.edit_text(
                "⚠️ Report coach not reachable.\n\n"
                f"<code>N8N_REPORT_COACH_URL = {N8N_REPORT_COACH_URL or '(not set)'}</code>\n\n"
                "Check that the n8n workflow is active and the webhook path is correct.",
                parse_mode="HTML",
            )
            return

        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Ask coach", callback_data="coach_chat"),
            InlineKeyboardButton("📋 Plan",      callback_data="today"),
        ]])
        await msg.delete()
        header = f"📊 <b>Week {week_num} Training Report</b>  <i>(test)</i>\n{'─' * 22}\n\n"
        await update.effective_message.reply_text(
            header + feedback,
            parse_mode="HTML",
            reply_markup=kb,
        )

    except Exception as e:
        logger.exception(f"cmd_weekreport failed for {telegram_id}: {e}")
        await msg.edit_text(f"❌ Error generating report: <code>{e}</code>", parse_mode="HTML")


async def cmd_monthreport(update, context) -> None:
    """
    Hidden command: /monthreport
    Immediately sends the monthly AI report for the requesting user.
    Not listed in set_bot_commands — for testing only.
    """
    from datetime import date
    telegram_id = str(update.effective_user.id)
    msg = await update.effective_message.reply_text("⏳ Generating monthly report…")

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            ath_r  = await client.get(f"{API_BASE_URL}/athlete/{telegram_id}")
            week_r = await client.get(f"{API_BASE_URL}/plan/{telegram_id}/current")

        if ath_r.status_code != 200:
            await msg.edit_text("❌ No athlete profile found. Complete onboarding first.")
            return
        if week_r.status_code != 200:
            await msg.edit_text("❌ No training plan found.")
            return

        athlete     = ath_r.json()
        week        = week_r.json()
        week_num    = week.get("week_number", 1)
        total_weeks = week.get("total_weeks", 18)
        planned_km_cur = week.get("planned_volume_km") or 0.0

        # Fetch up to 4 most recent weeks (or whatever is available)
        weeks_to_fetch = min(4, week_num)
        week_summaries: list[dict] = []

        async with httpx.AsyncClient(timeout=15) as client:
            for wn in range(week_num - weeks_to_fetch + 1, week_num + 1):
                if wn < 1:
                    continue
                r = await client.get(f"{API_BASE_URL}/log/{telegram_id}/week/{wn}/summary")
                if r.status_code == 200:
                    s = r.json()
                    if s.get("sessions_logged", 0) > 0:
                        week_summaries.append(s)

        if not week_summaries:
            await msg.edit_text(
                "⚠️ No logged sessions found in the last 4 weeks.\n\n"
                "Log some runs first with /log, then try again."
            )
            return

        total_actual   = sum(w.get("actual_volume_km", 0) for w in week_summaries)
        total_sessions = sum(w.get("sessions_logged", 0) for w in week_summaries)
        rpe_vals       = [w["avg_rpe"] for w in week_summaries if w.get("avg_rpe")]
        avg_rpe        = round(sum(rpe_vals) / len(rpe_vals), 1) if rpe_vals else None
        total_planned  = planned_km_cur * len(week_summaries)
        avg_compliance = round((total_actual / max(total_planned, 0.1)) * 100) if total_planned else None

        month_lines = []
        for ws in week_summaries:
            wn_  = ws.get("week_number", "?")
            akm  = ws.get("actual_volume_km", 0)
            sess = ws.get("sessions_logged", 0)
            rpe_ = ws.get("avg_rpe", "—")
            month_lines.append(f"Week {wn_}: {akm}km · {sess} sessions · RPE {rpe_}")
        month_summary = "\n".join(month_lines)

        race_date_str = athlete.get("race_date")
        try:
            weeks_to_race = max(0, round((date.fromisoformat(race_date_str) - date.today()).days / 7)) if race_date_str else max(0, total_weeks - week_num)
        except Exception:
            weeks_to_race = max(0, total_weeks - week_num)

        payload = {
            "report_type":        "monthly",
            "athlete_name":       athlete.get("name", "Runner"),
            "race_name":          athlete.get("race_name") or "your race",
            "current_week":       week_num,
            "total_weeks":        total_weeks,
            "weeks_to_race":      weeks_to_race,
            "vo2x":               athlete.get("vo2x"),
            "month_summary":      month_summary,
            "total_actual_km":    round(total_actual, 1),
            "total_planned_km":   round(total_planned, 1) if total_planned else None,
            "avg_compliance_pct": avg_compliance,
            "avg_rpe":            avg_rpe,
            "total_sessions":     total_sessions,
            "weeks_in_report":    len(week_summaries),
            "streak_weeks":       athlete.get("streak_weeks", 0) or 0,
            "training_profile":   athlete.get("training_profile", "balanced"),
        }

        feedback = await _call_report_coach(payload)

        if not feedback:
            await msg.edit_text(
                "⚠️ Report coach not reachable.\n\n"
                f"<code>N8N_REPORT_COACH_URL = {N8N_REPORT_COACH_URL or '(not set)'}</code>\n\n"
                "Check that the n8n workflow is active and the webhook path is correct.",
                parse_mode="HTML",
            )
            return

        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Ask coach", callback_data="coach_chat"),
            InlineKeyboardButton("📋 Plan",      callback_data="today"),
        ]])
        await msg.delete()
        month_label = date.today().strftime("%B")
        header = f"📅 <b>{month_label} Training Report</b>  <i>(test)</i>\n{'─' * 22}\n\n"
        await update.effective_message.reply_text(
            header + feedback,
            parse_mode="HTML",
            reply_markup=kb,
        )

    except Exception as e:
        logger.exception(f"cmd_monthreport failed for {telegram_id}: {e}")
        await msg.edit_text(f"❌ Error generating report: <code>{e}</code>", parse_mode="HTML")


async def cmd_racereport(update, context) -> None:
    """
    Hidden command: /racereport
    Immediately sends the race eve briefing for the requesting user,
    regardless of whether their race is actually tomorrow.
    Not listed in set_bot_commands — for testing only.
    """
    from datetime import date
    telegram_id = str(update.effective_user.id)
    msg = await update.effective_message.reply_text("⏳ Generating race eve briefing…")

    try:
        async with httpx.AsyncClient(timeout=12) as client:
            ath_r  = await client.get(f"{API_BASE_URL}/athlete/{telegram_id}")
            week_r = await client.get(f"{API_BASE_URL}/plan/{telegram_id}/current")

        if ath_r.status_code != 200:
            await msg.edit_text("❌ No athlete profile found. Complete onboarding first.")
            return
        if week_r.status_code != 200:
            await msg.edit_text("❌ No training plan found.")
            return

        athlete     = ath_r.json()
        week        = week_r.json()
        week_num    = week.get("week_number", 1)
        total_weeks = week.get("total_weeks", 18)
        race_date_str = athlete.get("race_date") or date.today().isoformat()

        # Fetch last 4 weeks
        weeks_to_fetch = min(4, week_num)
        week_summaries: list[dict] = []
        async with httpx.AsyncClient(timeout=15) as client:
            for wn in range(week_num - weeks_to_fetch + 1, week_num + 1):
                if wn < 1:
                    continue
                r = await client.get(f"{API_BASE_URL}/log/{telegram_id}/week/{wn}/summary")
                if r.status_code == 200:
                    s = r.json()
                    if s.get("sessions_logged", 0) > 0:
                        week_summaries.append(s)

        # Race prediction
        predicted_low = predicted_high = None
        dist_km = 42.195
        try:
            from coach_core.engine.predictor import predict, PredictionInput, PRESET_HILL_FACTORS
            from coach_core.engine.race_presets import RACE_PRESETS
            preset_id     = athlete.get("preset_race_id")
            race_dist_str = athlete.get("race_distance", "marathon")
            hilliness     = athlete.get("race_hilliness", "low")
            hill_factor   = PRESET_HILL_FACTORS.get(preset_id) if preset_id else {
                "flat": 0.02, "low": 0.045, "rolling": 0.06,
                "medium": 0.08, "hilly": 0.10, "high": 0.115, "mountain": 0.15,
            }.get(hilliness, 0.045)
            if preset_id and preset_id in RACE_PRESETS:
                dist_km = RACE_PRESETS[preset_id]["exact_distance_km"]
            else:
                dist_km = {"5k":5.0,"10k":10.0,"half":21.0975,"marathon":42.195,"ultra_56":56.0,"ultra_90":90.0}.get(race_dist_str, 42.195)
            vo2x       = athlete.get("vo2x") or 35.0
            weekly_km  = athlete.get("current_weekly_mileage") or 30.0
            from datetime import date as _date
            result = predict(PredictionInput(
                race_name        = athlete.get("race_name") or race_dist_str,
                race_distance_km = dist_km,
                hill_factor      = hill_factor,
                race_date        = _date.today(),
                direct_vo2x      = vo2x,
                weekly_mileage_km = weekly_km,
                longest_run_km   = weekly_km * 0.4,
                plan_type        = athlete.get("training_profile") or "conservative",
            ))
            predicted_low  = result.low_fmt()
            predicted_high = result.high_fmt()
        except Exception as pred_err:
            logger.warning(f"cmd_racereport prediction failed: {pred_err}")

        # Weather — use race coords if available, else athlete location
        weather = None
        try:
            from coach_core.engine.race_presets import RACE_COORDS
            preset_id = athlete.get("preset_race_id")
            if preset_id and preset_id in RACE_COORDS:
                lat, lon = RACE_COORDS[preset_id]
            elif athlete.get("latitude") and athlete.get("longitude"):
                lat, lon = athlete["latitude"], athlete["longitude"]
            else:
                lat = lon = None
            if lat and lon:
                weather = await _fetch_race_day_weather(lat, lon, race_hour=6)
        except Exception as w_err:
            logger.warning(f"cmd_racereport weather failed: {w_err}")

        # Race knowledge
        knowledge_text = checkpoint_summary = ""
        try:
            from coach_core.engine.race_knowledge import get_race_context
            ctx = get_race_context(
                preset_race_id=athlete.get("preset_race_id"),
                vo2x=athlete.get("vo2x"),
                race_date_str=race_date_str,
            )
            knowledge_text    = ctx.get("knowledge_text", "")
            checkpoint_summary = ctx.get("checkpoint_summary", "")
        except Exception:
            pass

        # Training summary
        month_lines = [
            f"Week {ws.get('week_number','?')}: {ws.get('actual_volume_km',0)}km · "
            f"{ws.get('sessions_logged',0)} sessions · RPE {ws.get('avg_rpe','—')}"
            for ws in week_summaries
        ]
        training_summary  = "\n".join(month_lines) if month_lines else "No sessions logged"
        total_actual      = sum(w.get("actual_volume_km", 0) for w in week_summaries)
        total_sessions    = sum(w.get("sessions_logged", 0) for w in week_summaries)
        rpe_vals          = [w["avg_rpe"] for w in week_summaries if w.get("avg_rpe")]
        avg_rpe           = round(sum(rpe_vals) / len(rpe_vals), 1) if rpe_vals else None

        payload: dict = {
            "report_type":         "race_eve",
            "athlete_name":        athlete.get("name", "Runner"),
            "race_name":           athlete.get("race_name") or "your race",
            "race_date":           race_date_str,
            "vo2x":                athlete.get("vo2x"),
            "predicted_low":       predicted_low,
            "predicted_high":      predicted_high,
            "race_distance_km":    dist_km,
            "race_hilliness":      athlete.get("race_hilliness", "low"),
            "training_summary":    training_summary,
            "total_km_4wk":        round(total_actual, 1),
            "total_sessions_4wk":  total_sessions,
            "avg_rpe_4wk":         avg_rpe,
            "streak_weeks":        athlete.get("streak_weeks", 0) or 0,
            "training_profile":    athlete.get("training_profile", "balanced"),
            "weeks_trained":       total_weeks,
            "race_knowledge_text": knowledge_text,
            "checkpoint_summary":  checkpoint_summary,
        }
        if weather:
            payload.update({
                "weather_temp_c":      weather["temperature"],
                "weather_dew_c":       weather["dew_point"],
                "weather_wind_kph":    weather["wind_kph"],
                "weather_precip_pct":  weather["precip_pct"],
                "weather_condition":   weather["condition_str"],
                "weather_adj_pct":     weather["adjustment_pct"],
                "weather_heat_warning": weather["heat_warning"],
            })
        else:
            payload["weather_condition"] = "unavailable"

        feedback = await _call_report_coach(payload)
        if not feedback:
            await msg.edit_text(
                "⚠️ Report coach not reachable.\n\n"
                f"<code>N8N_REPORT_COACH_URL = {N8N_REPORT_COACH_URL or '(not set)'}</code>",
                parse_mode="HTML",
            )
            return

        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("💬 Ask coach", callback_data="coach_chat"),
            InlineKeyboardButton("📋 Plan",      callback_data="today"),
        ]])
        await msg.delete()
        header = f"🏁 <b>{athlete.get('race_name') or 'Race Day'} — Eve Briefing</b>  <i>(test)</i>\n{'─' * 26}\n\n"
        await update.effective_message.reply_text(
            header + feedback,
            parse_mode="HTML",
            reply_markup=kb,
        )

    except Exception as e:
        logger.exception(f"cmd_racereport failed for {telegram_id}: {e}")
        await msg.edit_text(f"❌ Error generating report: <code>{e}</code>", parse_mode="HTML")


# ── Strength reminders ───────────────────────────────────────────────────────

async def _send_strength_reminders(
    bot: Bot,
    athletes: list[dict],
    cur_hour: int,
    today_key: str,
) -> None:
    """
    Sends strength day reminders 2 hours before each athlete's preferred run hour.
    Only fires if today is in the athlete's strength_days list.
    Also sends a nudge if the athlete has missed 3+ consecutive scheduled strength days.
    """
    sent = nudged = 0
    for athlete in athletes:
        tid = athlete.get("telegram_id")
        if not tid:
            continue

        strength_days_raw = athlete.get("strength_days") or ""
        if not strength_days_raw:
            continue

        strength_days = [d.strip() for d in strength_days_raw.split(",") if d.strip()]
        if today_key not in strength_days:
            continue

        run_hour = athlete.get("run_hour") or 7
        reminder_hour = (run_hour - 2) % 24
        if cur_hour != reminder_hour:
            continue

        # Determine current phase for template recommendation
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(f"{API_BASE_URL}/plan/{tid}/current")
                phase_name = r.json().get("phase_name", "Base") if r.status_code == 200 else "Base"
        except Exception:
            phase_name = "Base"

        level = athlete.get("strength_level", "beginner").capitalize()
        msg = (
            f"💪 <b>Strength day!</b>\n\n"
            f"Your {phase_name} phase session is ready — {level} level.\n"
            f"Open the strength app below before your run."
        )
        try:
            await bot.send_message(chat_id=tid, text=msg, parse_mode=ParseMode.HTML)
            sent += 1
            await asyncio.sleep(0.04)
        except Exception as e:
            logger.warning(f"Strength reminder failed for {tid}: {e}")

    if sent:
        logger.info(f"Strength reminders: {sent} sent, {nudged} nudge(s)")


# ── VO2X pace-gap check ───────────────────────────────────────────────────────

async def _run_vo2x_pace_gap_check(bot: Bot, athletes: list[dict]) -> None:
    """
    Daily 06:00 SA check — calls POST /strength/pace-gap-check for each full-plan athlete.
    Sends a bot message if VO2X was adjusted.
    """
    adjusted = skipped = 0
    for athlete in athletes:
        tid = athlete.get("telegram_id")
        if not tid or athlete.get("plan_type") != "full":
            skipped += 1
            continue
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{API_BASE_URL}/strength/pace-gap-check",
                    params={"telegram_id": tid},
                )
            if r.status_code != 200:
                continue
            data = r.json()
            if data.get("triggered"):
                message = data.get("message", "")
                if message:
                    await bot.send_message(
                        chat_id=tid,
                        text=message,
                        parse_mode=ParseMode.HTML,
                    )
                    await asyncio.sleep(0.04)
                adjusted += 1
                logger.info(
                    f"Pace-gap adjustment: {tid} VO2X {data.get('old_vo2x')} → {data.get('new_vo2x')}"
                )
        except Exception as e:
            logger.warning(f"Pace-gap check failed for {tid}: {e}")

    logger.info(f"VO2X pace-gap check: {adjusted} adjusted, {skipped} skipped")


# ── Main scheduler job ────────────────────────────────────────────────────────

async def send_daily_reminders(bot: Bot) -> None:
    """
    Called every hour by APScheduler.

    Hourly: sends session reminder to athletes whose run_hour matches now.
    Sunday 20:00 SA: sends weekly game to ALL athletes (regardless of run_hour).
    """
    now_sa    = datetime.now(SA_TZ)
    cur_hour  = now_sa.hour
    today_key = WEEKDAY_TO_DAY.get(now_sa.weekday(), "Mon")
    is_sunday = now_sa.weekday() == 6   # Sunday = 6

    logger.info(f"Reminder check: SA {now_sa.strftime('%A %H:%M')} ({today_key})")

    athletes = await _fetch_all_athletes()
    if not athletes:
        logger.info("No athletes found or API unreachable")
        return

    sent = failed = 0

    # ── Sunday 20:00 — weekly game blast to everyone ──────────────────────
    if is_sunday and cur_hour == 20:
        logger.info(f"Sunday game blast — {len(athletes)} athletes")

        for athlete in athletes:
            tid = athlete.get("telegram_id")
            if not tid:
                continue
            try:
                week = await _fetch_today_plan(tid)
                if not week:
                    continue

                week_num = week.get("week_number", 1)

                # Count runs logged this week
                summary = await _fetch_week_summary(tid, week_num)
                runs_logged = summary.get("sessions_completed", 0)
                actual_km   = summary.get("actual_volume_km", 0)

                # Weeks to race
                race_date_str = athlete.get("race_date")
                if race_date_str:
                    from datetime import date
                    try:
                        rd = date.fromisoformat(race_date_str)
                        weeks_left = max(1, round((rd - date.today()).days / 7))
                    except Exception:
                        weeks_left = max(1, (week.get("total_weeks", 18) - week_num))
                else:
                    weeks_left = max(1, (week.get("total_weeks", 18) - week_num))

                msg, keyboard = _build_sunday_game_message(
                    athlete, week, runs_logged, weeks_left, actual_km
                )

                await bot.send_message(
                    chat_id=tid,
                    text=msg,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
                sent += 1
                await asyncio.sleep(0.04)

            except Exception as e:
                failed += 1
                logger.warning(f"Sunday game send failed for {tid}: {e}")

        logger.info(f"Sunday game: {sent} sent, {failed} failed")
        return   # don't also send daily reminder on Sunday evening

    # ── 06:00 daily — VO2X pace-gap check for all full-plan athletes ────────
    if cur_hour == 6:
        await _run_vo2x_pace_gap_check(bot, athletes)

    # ── Strength day reminders — fires 2h before each athlete's run_hour ──
    await _send_strength_reminders(bot, athletes, cur_hour, today_key)

    # ── 15:00 daily — race eve briefing for athletes racing tomorrow ─────────
    if cur_hour == 15:
        await _send_race_eve_reports(bot, athletes)
        # Don't return — still send daily reminders to athletes at this hour

    # ── Sunday 19:00 — weekly AI training report ──────────────────────────
    if is_sunday and cur_hour == 19:
        logger.info("Sunday 19:00 — sending weekly AI reports")
        await _send_weekly_reports(bot, athletes)
        # Don't return — still send daily reminders to athletes scheduled at hour 19

    # ── Monthly AI report — last Sunday of the month at 08:00 ────────────
    if is_sunday and cur_hour == 8 and _is_last_sunday_of_month(now_sa.date()):
        logger.info("Last Sunday of month — sending monthly AI reports")
        await _send_monthly_reports(bot, athletes)
        # Don't return — still send daily reminders to athletes scheduled at hour 8

    # ── Race Prep milestone check — runs for ALL athletes at this hour ────
    # Also catches race_morning at hour 5 (outside the eligible filter below)
    prep_sent = prep_failed = 0
    for athlete in athletes:
        tid = athlete.get("telegram_id")
        if not tid:
            continue
        try:
            prep_fired = await _send_race_prep_if_due(bot, athlete, cur_hour)
            if prep_fired:
                prep_sent += 1
                await asyncio.sleep(0.04)
        except Exception as e:
            prep_failed += 1
            logger.warning(f"Race prep check failed for {tid}: {e}")

    if prep_sent or prep_failed:
        logger.info(f"Race prep: {prep_sent} sent, {prep_failed} failed")

    # ── Hourly daily reminders ────────────────────────────────────────────
    eligible = [a for a in athletes if (a.get("run_hour") or 7) == cur_hour]

    if not eligible:
        logger.info(f"No athletes scheduled for hour {cur_hour}")
        return

    logger.info(f"Sending reminders to {len(eligible)} athletes")

    for athlete in eligible:
        tid = athlete.get("telegram_id")
        if not tid:
            continue
        try:
            week = await _fetch_today_plan(tid)
            if not week:
                continue

            msg = _build_reminder_message(athlete, week, today_key)

            await bot.send_message(
                chat_id=tid,
                text=msg,
                parse_mode=ParseMode.HTML,
            )
            sent += 1
            await asyncio.sleep(0.04)

        except Exception as e:
            failed += 1
            logger.warning(f"Reminder failed for {tid}: {e}")

    logger.info(f"Reminders: {sent} sent, {failed} failed")
