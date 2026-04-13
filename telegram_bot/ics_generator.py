"""
ICS (iCalendar) generator for TR3D weekly training plans.

Generates a .ics file from a week plan dict that can be imported into any
phone calendar (iOS Calendar, Google Calendar, Outlook, etc.).

Each non-rest day becomes one VEVENT:
  - All-day event on the correct weekday
  - SUMMARY: session name + km
  - DESCRIPTION: full workout notes, training paces, session player URL
  - Alarm reminder 1 hour before 6:30 AM (i.e. at 5:30 AM)
"""
from __future__ import annotations

import hashlib
from datetime import date, timedelta, datetime, timezone

# ── Day name → offset from Monday ─────────────────────────────────────────
_DAY_OFFSET = {
    "Mon": 0, "Tue": 1, "Wed": 2, "Thu": 3,
    "Fri": 4, "Sat": 5, "Sun": 6,
}
_DAY_ORDER = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

_REST_SESSIONS = {"Rest", "Cross-Train / Rest", ""}

# ── Effort-type → bg colour (for calendar colour tagging, cosmetic only) ──
_SESSION_COLOUR = {
    "easy":      "CYAN",
    "long":      "GREEN",
    "tempo":     "ORANGE",
    "threshold": "ORANGE",
    "interval":  "RED",
    "rep":       "RED",
    "stride":    "RED",
    "hill":      "RED",
    "warmup":    "BLUE",
}


def _classify(session_name: str) -> str:
    n = session_name.lower()
    if any(k in n for k in ("tempo", "threshold", "cruise")): return "tempo"
    if any(k in n for k in ("interval",)):                    return "interval"
    if any(k in n for k in ("rep", "repetition")):            return "rep"
    if any(k in n for k in ("stride",)):                      return "stride"
    if any(k in n for k in ("hill",)):                        return "hill"
    if "long" in n:                                            return "long"
    if any(k in n for k in ("warm", "cool")):                 return "warmup"
    return "easy"


def _parse_pace_str(pace_str: str) -> float:
    """Parse a pace string like '6:14' into decimal min/km. Returns 0.0 on failure."""
    try:
        parts = pace_str.strip().split(":")
        if len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 60
    except Exception:
        pass
    return 0.0


def _estimate_duration_mins(
    km: float,
    session_name: str,
    paces_dict: "dict | None" = None,
) -> int:
    """Session duration estimate using athlete's actual paces when available."""
    cls = _classify(session_name)

    # Use the athlete's actual pace from paces_dict when present
    if paces_dict:
        pace_key = {
            "tempo":    "threshold",
            "interval": "interval",
            "rep":      "rep",
        }.get(cls, "easy")
        raw = paces_dict.get(pace_key) or paces_dict.get("easy", "")
        parsed = _parse_pace_str(raw) if raw else 0.0
        if parsed > 0:
            # Quality sessions: add ~20% overhead for rest/recovery between reps
            overhead = 1.20 if cls in ("interval", "rep") else 1.05
            mins = int(km * parsed * overhead) + 5
            return max(30, min(180, mins))

    # Fallback to generic paces when no paces_dict
    pace = {
        "tempo":    5.5,   # min/km
        "interval": 5.5,
        "rep":      5.5,
        "stride":   5.5,
        "hill":     5.5,
        "long":     6.5,
        "warmup":   6.5,
        "easy":     6.5,
    }.get(cls, 6.5)
    mins = int(km * pace) + 5   # +5 min buffer
    return max(30, min(180, mins))


def _ics_escape(text: str) -> str:
    """Escape text per RFC 5545 §3.3.11."""
    return (
        text
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _ics_fold(line: str) -> str:
    """Fold lines >75 octets per RFC 5545 §3.1."""
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line

    result_lines: list[str] = []
    current = ""
    current_bytes = 0

    for char in line:
        char_bytes = len(char.encode("utf-8"))
        if current_bytes + char_bytes > 75:
            result_lines.append(current)
            current = " " + char
            current_bytes = 1 + char_bytes
        else:
            current += char
            current_bytes += char_bytes

    if current:
        result_lines.append(current)

    return "\r\n".join(result_lines)


def _fmt_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def _fmt_datetime_utc(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def _uid(telegram_id: str, day_date: date) -> str:
    raw = f"tr3d-{telegram_id}-{day_date.isoformat()}"
    return hashlib.md5(raw.encode()).hexdigest() + "@tr3d.run"


def _build_description(
    day: str,
    session: dict,
    paces_dict: "dict | None",
    session_url: "str | None",
    phase_name: str,
    week_num: int,
) -> str:
    """Build the plain-text DESCRIPTION field for one calendar event."""
    name    = session.get("session", "")
    km      = session.get("km", 0)
    notes   = session.get("notes", "").strip()
    cls     = _classify(name)
    est_min = _estimate_duration_mins(km, name, paces_dict)
    est_h   = est_min // 60
    est_m   = est_min % 60
    dur_str = f"{est_h}h {est_m}min" if est_h else f"{est_m} min"

    lines: list[str] = [
        f"TR3D RUNNING COACH",
        f"Week {week_num} · {phase_name} · {day}",
        "─" * 30,
        f"SESSION: {name}",
        f"DISTANCE: {km} km",
        f"EST. DURATION: {dur_str}",
    ]

    if notes:
        lines += ["", "WORKOUT DETAILS:", notes]

    # Training paces block
    if paces_dict and cls not in ("easy", "warmup", "long"):
        lines += ["", "TRAINING PACES:"]
        pace_labels = [
            ("Easy",      "easy"),
            ("Threshold", "threshold"),
            ("Interval",  "interval"),
            ("Rep",       "rep"),
        ]
        for label, key in pace_labels:
            val = paces_dict.get(key)
            if val:
                lines.append(f"  {label:<10} {val}/km")
    elif paces_dict:
        # Easy/long — just show easy pace
        easy = paces_dict.get("easy")
        if easy:
            lines += ["", f"EASY PACE: {easy}/km"]

    # Session player link
    if session_url:
        lines += ["", "▶ START SESSION:", session_url]

    lines += ["", "─" * 30, "Generated by TR3D Running Coach"]
    return "\n".join(lines)


def generate_week_ics(
    week: dict,
    athlete: dict,
    paces_dict: "dict | None" = None,
    session_url_builder: "callable | None" = None,
) -> bytes:
    """
    Generate ICS bytes for the full training week.

    Parameters
    ----------
    week : dict
        The week plan from the API (has week_start, days, week_number, phase, …)
    athlete : dict
        The athlete profile (name, telegram_id, …)
    paces_dict : dict | None
        {"easy": "6:14", "threshold": "5:12", "interval": "4:47", "rep": "4:20"}
    session_url_builder : callable | None
        Called as session_url_builder(session_dict) → str | None
        to produce the session player URL for quality sessions.

    Returns
    -------
    bytes  — UTF-8 encoded ICS file content with CRLF line endings
    """
    from telegram_bot.formatting import PHASE_NAMES

    telegram_id = str(athlete.get("telegram_id", "unknown"))
    athlete_name = athlete.get("name", "Runner")
    week_num    = week.get("week_number", 1)
    phase_num   = week.get("phase", 1)
    phase_name  = PHASE_NAMES.get(phase_num, "Training")
    week_start_str = week.get("week_start", "")
    all_days    = week.get("days", {})

    # Parse week start date
    try:
        week_start_date = date.fromisoformat(week_start_str)
    except (ValueError, TypeError):
        week_start_date = date.today()
        # Back-calculate to Monday
        week_start_date -= timedelta(days=week_start_date.weekday())

    dtstamp = _fmt_datetime_utc(datetime.now(timezone.utc))

    # ── Build calendar header ──────────────────────────────────────────────
    cal_name = f"TR3D · Week {week_num} · {phase_name}"
    cal_desc = f"{athlete_name}'s TR3D training plan — Week {week_num}"

    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//TR3D Running Coach//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(cal_name)}",
        f"X-WR-CALDESC:{_ics_escape(cal_desc)}",
        "X-WR-TIMEZONE:UTC",
    ]

    # ── One VEVENT per training day ────────────────────────────────────────
    for day in _DAY_ORDER:
        session = all_days.get(day, {})
        name    = session.get("session", "Rest")
        km      = session.get("km", 0)

        # Skip rest days
        if name in _REST_SESSIONS or km == 0:
            continue

        day_offset  = _DAY_OFFSET.get(day, 0)
        event_date  = week_start_date + timedelta(days=day_offset)
        event_date_next = event_date + timedelta(days=1)

        # Session player URL (quality sessions only)
        session_url = None
        if session_url_builder:
            try:
                session_url = session_url_builder(session)
            except Exception:
                pass

        # Summary: "🏃 Tempo · 12 km"
        emoji = _session_emoji(name)
        summary = f"{emoji} {name} · {km} km"

        # Full description
        description = _build_description(
            day, session, paces_dict, session_url, phase_name, week_num
        )

        uid = _uid(telegram_id, event_date)

        event_lines: list[str] = [
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART;VALUE=DATE:{_fmt_date(event_date)}",
            f"DTEND;VALUE=DATE:{_fmt_date(event_date_next)}",
            f"SUMMARY:{_ics_escape(summary)}",
            f"DESCRIPTION:{_ics_escape(description)}",
        ]

        # Colour category (cosmetic, honoured by some clients)
        cls = _classify(name)
        color_map = {
            "tempo":    "ORANGE",
            "interval": "RED",
            "rep":      "RED",
            "stride":   "RED",
            "hill":     "RED",
            "long":     "GREEN",
            "warmup":   "BLUE",
            "easy":     "CYAN",
        }
        color = color_map.get(cls, "CYAN")
        event_lines.append(f"COLOR:{color}")

        # 1-hour reminder alarm at 5:30 AM (1h before default 6:30 run)
        event_lines += [
            "BEGIN:VALARM",
            "TRIGGER;RELATED=START:PT0S",
            "ACTION:DISPLAY",
            f"DESCRIPTION:{_ics_escape(summary)}",
            "END:VALARM",
        ]

        event_lines.append("END:VEVENT")
        lines.extend(event_lines)

    lines.append("END:VCALENDAR")

    # Fold long lines and join with CRLF
    folded = "\r\n".join(_ics_fold(ln) for ln in lines) + "\r\n"
    return folded.encode("utf-8")


def _session_emoji(session_name: str) -> str:
    """Pick a single leading emoji based on session type."""
    n = session_name.lower()
    if any(k in n for k in ("tempo", "threshold", "cruise", "interval", "rep", "stride")):
        return "⚡"
    if "long" in n:
        return "🔵"
    if "hill" in n:
        return "⛰"
    return "🏃"
