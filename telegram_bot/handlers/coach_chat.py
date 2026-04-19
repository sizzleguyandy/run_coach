"""
TR3D Chat — question interface into the TR3D training system.

Flow:
  1. User taps "💬 TR3D Chat" in the inline menu (callback_data="coach_chat")
     OR sends /ask command.
  2. Bot replies asking them to type their question.
  3. User's question is bundled with their full TR3D athlete context:
       - Calculated paces (VO2X), current phase, weeks to race
       - Precomputed taper timing (start week, date, length)
       - Race-specific knowledge and personalised checkpoint splits
  4. Enriched payload is posted to the N8N_CHAT_WEBHOOK.
  5. Response is returned to the user. Conversation ends.

A single-state ConversationHandler keeps this isolated from other flows.
Set N8N_CHAT_WEBHOOK in your .env to enable. If unset, the feature
degrades gracefully with a friendly message.
"""
from __future__ import annotations

import logging
import os

import httpx
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from telegram_bot.config import API_BASE_URL

_log = logging.getLogger(__name__)

N8N_CHAT_WEBHOOK = os.getenv("N8N_CHAT_WEBHOOK", "")

# Single conversation state
COACH_QUESTION = 100


# ── Entry points ──────────────────────────────────────────────────────────────

async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via /ask command."""
    return await _prompt_for_question(update, context)


async def coach_chat_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry via 💬 Coach chat inline button."""
    await update.callback_query.answer()
    return await _prompt_for_question(update, context)


async def _prompt_for_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cancel_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✕ Cancel", callback_data="menu"),
    ]])
    text = (
        "💬 <b>TR3D Chat</b>\n\n"
        "Ask anything about your training — paces, phases, taper timing, "
        "race prep, or how today's session fits the system.\n\n"
        "Type your question:"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=cancel_kb, parse_mode="HTML")
    else:
        await update.effective_message.reply_text(text, reply_markup=cancel_kb, parse_mode="HTML")
    return COACH_QUESTION


# ── Question handler ──────────────────────────────────────────────────────────

async def handle_question(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive the user's question, enrich with context, call n8n, return answer."""
    question = (update.message.text or "").strip()
    if not question:
        await update.message.reply_text("I didn't catch that — try typing your question again.")
        return COACH_QUESTION

    telegram_id = str(update.effective_user.id)

    # Let the user know we're thinking — n8n LLM pipeline can take 20–40 s
    thinking_msg = await update.message.reply_text("⏳ Analysing your plan — this may take up to 30 seconds…")

    # Build the payload
    payload = await _build_payload(telegram_id, question)

    # Call n8n
    reply = await _call_chatbot(payload)

    # Delete the "thinking" placeholder
    try:
        await thinking_msg.delete()
    except Exception:
        pass

    back_kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Ask again", callback_data="coach_chat"),
            InlineKeyboardButton("← Menu",       callback_data="menu"),
        ]
    ])

    await update.message.reply_text(reply, reply_markup=back_kb, parse_mode="HTML")
    return ConversationHandler.END


# ── Cancel ────────────────────────────────────────────────────────────────────

async def coach_chat_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Cancelled. Use /menu whenever you want to chat.")
    return ConversationHandler.END


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _build_payload(telegram_id: str, question: str) -> dict:
    """Fetch athlete + plan context and merge with the user's question."""
    payload: dict = {
        "user_question": question,
        # Grounding instruction — included in every request so the AI node
        # cannot drift to generic plan knowledge when athlete data is present.
        "grounding_instruction": (
            "IMPORTANT: Answer using ONLY the athlete data fields provided in this payload. "
            "Do NOT fall back to generic training plan descriptions. "
            "Use week_num, phase_name, taper_start_week, taper_starts_in_weeks, "
            "taper_start_date and today_date to give precise, personalised answers. "
            "If a field is missing, say so rather than guessing."
        ),
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            athlete_r, week_r = await _gather_safe(
                client.get(f"{API_BASE_URL}/athlete/{telegram_id}"),
                client.get(f"{API_BASE_URL}/plan/{telegram_id}/current"),
            )

        if athlete_r and athlete_r.status_code == 200:
            athlete = athlete_r.json()
            payload["athlete_name"]     = athlete.get("name", "Runner")
            payload["race_name"]        = athlete.get("race_name") or "your race"
            payload["training_profile"] = athlete.get("training_profile", "conservative")
            payload["race_date"]        = str(athlete.get("race_date") or "")

            # Paces from VO2X
            vo2x = athlete.get("vo2x")
            if vo2x:
                try:
                    from coach_core.engine.paces import calculate_paces, format_pace
                    p = calculate_paces(vo2x)
                    payload["vo2x"]           = vo2x
                    payload["easy_pace"]      = format_pace(p.easy_min_per_km)
                    payload["marathon_pace"]  = format_pace(p.marathon_min_per_km)
                    payload["threshold_pace"] = format_pace(p.threshold_min_per_km)
                    payload["interval_pace"]  = format_pace(p.interval_min_per_km)
                except Exception:
                    pass

            # ── RAG: inject race knowledge + personalised checkpoints ──────
            preset_race_id = athlete.get("preset_race_id")
            if preset_race_id or vo2x:
                try:
                    from coach_core.engine.race_knowledge import get_race_context
                    race_ctx = get_race_context(
                        preset_race_id=preset_race_id,
                        vo2x=vo2x,
                        race_date_str=str(athlete.get("race_date") or ""),
                    )
                    payload["race_knowledge_text"]   = race_ctx["knowledge_text"]
                    payload["checkpoint_summary"]    = race_ctx["checkpoint_summary"]
                    payload["race_display_name"]     = race_ctx["race_display_name"]
                    # Update race_name with the display name if we have one
                    if race_ctx["race_display_name"] != "your race":
                        payload["race_name"] = race_ctx["race_display_name"]
                except Exception as rag_err:
                    _log.warning(f"coach_chat: RAG context failed — {rag_err}")
                    payload["race_knowledge_text"] = ""
                    payload["checkpoint_summary"]  = ""

        if week_r and week_r.status_code == 200:
            week = week_r.json()
            total   = week.get("total_weeks")   # None if missing — never default to 18
            current = week.get("week_number", 1)
            payload["phase_num"]      = week.get("phase", 1)
            payload["phase_name"]     = _phase_name(week.get("phase", 1))
            payload["week_num"]       = current
            payload["total_weeks"]    = total
            payload["weeks_to_race"]  = max(0, total - current) if total else None
            payload["total_vol_km"]   = week.get("planned_volume_km", 0)

            # ── Precomputed taper fields (prevents AI drift on taper questions) ──
            try:
                from datetime import date, timedelta
                from coach_core.engine.phases import get_phases
                from coach_core.engine.volume import get_taper_weeks

                if not total:
                    raise ValueError("total_weeks missing from plan response")

                race_dist   = athlete.get("race_distance", "marathon") if athlete_r and athlete_r.status_code == 200 else "marathon"
                phases      = get_phases(total)
                taper_len   = get_taper_weeks(race_dist)
                weeks_to_peak = phases.phase_I + phases.phase_II + phases.phase_III

                # 1-based week at which volume reduction begins (matches volume.py logic)
                taper_start_week      = weeks_to_peak + phases.phase_IV - taper_len + 1
                taper_starts_in_weeks = max(0, taper_start_week - current)

                today = date.today()
                taper_start_date = today + timedelta(weeks=taper_starts_in_weeks)

                payload["taper_length_weeks"]    = taper_len
                payload["taper_start_week"]      = taper_start_week
                payload["taper_starts_in_weeks"] = taper_starts_in_weeks
                payload["taper_start_date"]      = taper_start_date.strftime("%d %b %Y")
                payload["today_date"]            = today.strftime("%d %b %Y")
                payload["in_taper"]              = current >= taper_start_week
            except Exception as taper_err:
                _log.warning(f"coach_chat: taper field computation failed — {taper_err}")

    except Exception as e:
        _log.warning(f"coach_chat: context fetch failed — {e}")

    return payload


async def _gather_safe(*coros):
    """Run coroutines concurrently; return None for any that raise."""
    import asyncio
    results = await asyncio.gather(*coros, return_exceptions=True)
    return [None if isinstance(r, Exception) else r for r in results]


def _phase_name(phase: int) -> str:
    return {
        1: "Base (aerobic foundation)",
        2: "Repetitions (speed economy)",
        3: "Intervals (VO2max)",
        4: "Threshold (race-pace stamina)",
    }.get(phase, "Training")


async def _call_chatbot(payload: dict) -> str:
    """POST payload to n8n chatbot webhook, return the AI's reply text."""
    if not N8N_CHAT_WEBHOOK:
        return (
            "Coach chat is not configured yet. "
            "Set N8N_CHAT_WEBHOOK in your environment and restart the bot."
        )

    # n8n runs an LLM pipeline that can take 30–60 s — use a generous read
    # timeout so we don't bail out before the response arrives.
    # connect=10 still fails fast if n8n is genuinely unreachable.
    _n8n_timeout = httpx.Timeout(connect=10.0, read=90.0, write=10.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=_n8n_timeout) as client:
            r = await client.post(N8N_CHAT_WEBHOOK, json=payload)
            if r.status_code == 200:
                data = r.json()
                _log.info(f"coach_chat n8n response keys: {list(data.keys())}")
                reply = data.get("output") or data.get("coached_message") or data.get("text", "")
                if reply:
                    return reply
    except httpx.TimeoutException as e:
        _log.warning(f"coach_chat webhook timed out after 90 s: {e}")
    except Exception as e:
        _log.warning(f"coach_chat webhook call failed: {type(e).__name__}: {e}")

    return (
        "I couldn't reach the coaching service right now. "
        "Please try again in a moment."
    )
