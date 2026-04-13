import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram_bot.handlers.reminder import (
    send_daily_reminders,
    cmd_weekreport, cmd_monthreport, cmd_racereport,
)
from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
)

from telegram.ext import PicklePersistence

from telegram_bot.config import TELEGRAM_TOKEN

# ── V2 onboarding (replaces old onboarding.py) ────────────────────────────
from telegram_bot.handlers.onboarding_v2 import (
    cmd_start, handle_web_app_data, cmd_cancel, cmd_reset,
    get_name, get_race, get_custom_dist, get_custom_hills, get_custom_date,
    get_vdot_input,
    get_experience, get_recent_dist, get_recent_time,
    get_beginner_ability, get_weekly_km, get_longest_run, get_plan_type,
    get_long_run_day, get_quality_day, get_easy_days, get_easy_day_2,
    get_anchor_question, get_anchor_km,
    get_location,
    NAME, RACE_SELECT, CUSTOM_DIST, CUSTOM_HILLS, CUSTOM_DATE,
    EXPERIENCE, RECENT_DIST, RECENT_TIME, BEGINNER_ABILITY,
    WEEKLY_KM, LONGEST_RUN, PLAN_TYPE, LOCATION, VDOT_INPUT,
    LONG_RUN_DAY, QUALITY_DAY, EASY_DAYS, EASY_DAY_2,
    ANCHOR_QUESTION, ANCHOR_KM,
)

from telegram_bot.handlers.ui import handle_callback, cmd_menu, cmd_today, cmd_dashboard
from telegram_bot.handlers.mycode import cmd_mycode
from telegram_bot.handlers.coach_chat import (
    cmd_ask, coach_chat_callback, handle_question, coach_chat_cancel,
    COACH_QUESTION,
)
from telegram_bot.handlers.plan_handler import (
    cmd_plan, cmd_paces, cmd_location,
)
from telegram_bot.handlers.log_handler import (
    cmd_log, cmd_progress,
    log_get_day, log_get_distance, log_get_duration, log_get_rpe, log_cancel,
    LOG_DAY, LOG_DISTANCE, LOG_DURATION, LOG_RPE,
    cmd_lograce, race_get_dist, race_get_time, race_confirm, race_cancel,
    RACE_DIST as LR_RACE_DIST, RACE_TIME as LR_RACE_TIME, RACE_CONFIRM as LR_RACE_CONFIRM,
)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

TEXT = filters.TEXT & ~filters.COMMAND


def build_application() -> Application:
    persistence = PicklePersistence(filepath="bot_persistence.pkl")
    app = Application.builder().token(TELEGRAM_TOKEN).persistence(persistence).build()

    # ── V2 Onboarding ConversationHandler ─────────────────────────────────
    onboarding_conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            NAME:             [MessageHandler(TEXT, get_name)],
            RACE_SELECT:      [MessageHandler(TEXT, get_race)],
            CUSTOM_DIST:      [MessageHandler(TEXT, get_custom_dist)],
            CUSTOM_HILLS:     [MessageHandler(TEXT, get_custom_hills)],
            CUSTOM_DATE:      [MessageHandler(TEXT, get_custom_date)],
            EXPERIENCE:       [MessageHandler(TEXT, get_experience)],
            RECENT_DIST:      [MessageHandler(TEXT, get_recent_dist)],
            RECENT_TIME:      [MessageHandler(TEXT, get_recent_time)],
            BEGINNER_ABILITY: [MessageHandler(TEXT, get_beginner_ability)],
            WEEKLY_KM:        [MessageHandler(TEXT, get_weekly_km)],
            LONGEST_RUN:      [MessageHandler(TEXT, get_longest_run)],
            PLAN_TYPE:        [MessageHandler(TEXT, get_plan_type)],
            LONG_RUN_DAY:     [MessageHandler(TEXT, get_long_run_day)],
            QUALITY_DAY:      [MessageHandler(TEXT, get_quality_day)],
            EASY_DAYS:        [MessageHandler(TEXT, get_easy_days)],
            EASY_DAY_2:       [MessageHandler(TEXT, get_easy_day_2)],
            ANCHOR_QUESTION:  [MessageHandler(TEXT, get_anchor_question)],
            ANCHOR_KM:        [MessageHandler(TEXT, get_anchor_km)],
            LOCATION:         [MessageHandler(TEXT, get_location)],
            VDOT_INPUT:       [MessageHandler(TEXT, get_vdot_input)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
        allow_reentry=True,
    )

    # ── Log ConversationHandler ───────────────────────────────────────────
    log_conv = ConversationHandler(
        entry_points=[
            CommandHandler("log", cmd_log),
            CallbackQueryHandler(cmd_log, pattern="^log$"),  # inline "Log run" button
        ],
        states={
            LOG_DAY:      [MessageHandler(TEXT, log_get_day)],
            LOG_DISTANCE: [MessageHandler(TEXT, log_get_distance)],
            LOG_DURATION: [MessageHandler(TEXT, log_get_duration)],
            LOG_RPE:      [MessageHandler(TEXT, log_get_rpe)],
        },
        fallbacks=[CommandHandler("cancel", log_cancel)],
        allow_reentry=True,
    )

    # ── Race logging ConversationHandler ─────────────────────────────────
    lograce_conv = ConversationHandler(
        entry_points=[CommandHandler("lograce", cmd_lograce)],
        states={
            LR_RACE_DIST:    [MessageHandler(TEXT, race_get_dist)],
            LR_RACE_TIME:    [MessageHandler(TEXT, race_get_time)],
            LR_RACE_CONFIRM: [MessageHandler(TEXT, race_confirm)],
        },
        fallbacks=[CommandHandler("cancel", race_cancel)],
    )

    # ── Coach chat ConversationHandler ────────────────────────────────────
    coach_chat_conv = ConversationHandler(
        entry_points=[
            CommandHandler("ask", cmd_ask),
            CallbackQueryHandler(coach_chat_callback, pattern="^coach_chat$"),
        ],
        states={
            COACH_QUESTION: [MessageHandler(TEXT, handle_question)],
        },
        fallbacks=[CommandHandler("cancel", coach_chat_cancel)],
        allow_reentry=True,
    )

    # ── Anchor Runs ConversationHandler ──────────────────────────────────
    from telegram_bot.handlers.anchor import (
        build_anchor_handler, anchor_menu, anchor_add_start, anchor_clear,
        anchor_day_selected, anchor_km_selected, anchor_km_typed,
        ANCHOR_SELECT_DAY, ANCHOR_ENTER_KM,
    )
    anchor_conv = build_anchor_handler()

    # ── Training days change ConversationHandler ──────────────────────────
    from telegram_bot.handlers.training_days import (
        start_change_days, change_long_run_day, change_quality_day,
        change_easy_days, change_easy_day_2, change_days_cancel,
        CHANGE_LONG_RUN_DAY, CHANGE_QUALITY_DAY, CHANGE_EASY_DAYS, CHANGE_EASY_DAY_2,
    )
    training_days_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_change_days, pattern="^change_training_days$"),
        ],
        states={
            CHANGE_LONG_RUN_DAY: [MessageHandler(TEXT, change_long_run_day)],
            CHANGE_QUALITY_DAY:  [MessageHandler(TEXT, change_quality_day)],
            CHANGE_EASY_DAYS:    [MessageHandler(TEXT, change_easy_days)],
            CHANGE_EASY_DAY_2:   [MessageHandler(TEXT, change_easy_day_2)],
        },
        fallbacks=[CommandHandler("cancel", change_days_cancel)],
        allow_reentry=True,
    )

    # ── Registration order: ConversationHandlers BEFORE CallbackQueryHandler
    app.add_handler(onboarding_conv)
    app.add_handler(log_conv)
    app.add_handler(lograce_conv)
    app.add_handler(coach_chat_conv)
    app.add_handler(training_days_conv)
    app.add_handler(anchor_conv)

    # ── Web App data handler
    app.add_handler(MessageHandler(
        filters.StatusUpdate.WEB_APP_DATA,
        handle_web_app_data,
    ))

    # ── Inline button callbacks — registered LAST
    app.add_handler(CallbackQueryHandler(handle_callback))

    # ── Standalone commands
    app.add_handler(CommandHandler("menu",      cmd_menu))
    app.add_handler(CommandHandler("today",     cmd_today))
    app.add_handler(CommandHandler("plan",      cmd_plan))
    app.add_handler(CommandHandler("dashboard", cmd_dashboard))
    app.add_handler(CommandHandler("paces",     cmd_paces))
    app.add_handler(CommandHandler("progress",  cmd_progress))
    # /lograce is handled by lograce_conv entry point above — no standalone handler needed
    app.add_handler(CommandHandler("reset",     cmd_reset))
    app.add_handler(CommandHandler("location",  cmd_location))
    app.add_handler(CommandHandler("mycode",    cmd_mycode))
    # /ask is handled by coach_chat_conv entry point above

    # ── Hidden test commands (not in set_bot_commands menu) ───────────────
    app.add_handler(CommandHandler("weekreport",  cmd_weekreport))
    app.add_handler(CommandHandler("monthreport", cmd_monthreport))
    app.add_handler(CommandHandler("racereport",  cmd_racereport))

    app.add_error_handler(error_handler)
    return app


async def set_bot_commands(app: Application) -> None:
    await app.bot.set_my_commands([
        BotCommand("menu",      "Main menu"),
        BotCommand("today",     "Today's workout"),
        BotCommand("plan",      "This week's training plan"),
        BotCommand("dashboard", "Weekly snapshot & progress"),
        BotCommand("log",       "Log a completed run"),
        BotCommand("paces",     "Your VDOT training paces"),
        BotCommand("location",  "Set city for weather pace adjustment"),
        BotCommand("progress",  "Weekly log summary"),
        BotCommand("lograce",   "Log a race result and update VDOT"),
        BotCommand("ask",       "Ask TR3D about your training plan"),
        BotCommand("mycode",    "Link the Virgin Race mobile app to your plan"),
        BotCommand("reset",     "Delete profile and start over"),
        BotCommand("cancel",    "Cancel current action"),
    ])


async def error_handler(update: object, context) -> None:
    """Log every unhandled exception."""
    import traceback
    logger.error("Unhandled exception:\n" + "".join(traceback.format_exception(
        type(context.error), context.error, context.error.__traceback__
    )))
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Something went wrong. Please try again or use /cancel to reset."
            )
        except Exception:
            pass


def main() -> None:
    app = build_application()

    scheduler = AsyncIOScheduler(timezone="Africa/Johannesburg")
    scheduler.add_job(
        send_daily_reminders,
        trigger="cron",
        minute=0,
        args=[app.bot],
        id="daily_reminders",
        replace_existing=True,
    )

    async def post_init(application: Application) -> None:
        await set_bot_commands(application)
        scheduler.start()
        logger.info("Reminder scheduler started — fires at top of every hour")

    app.post_init = post_init
    logger.info("TR3D Training System starting — Science-Built. Race-Ready.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
