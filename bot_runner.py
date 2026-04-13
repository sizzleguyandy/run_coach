"""
Bot wrapper that captures exceptions to a log file.
"""
import sys
import traceback
import logging

logging.basicConfig(
    filename="bot_crash.log",
    filemode="a",
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.DEBUG,
)
logger = logging.getLogger(__name__)

def excepthook(type, value, tb):
    tb_str = "".join(traceback.format_exception(type, value, tb))
    logger.critical(f"Uncaught exception:\n{tb_str}")
    print(f"CRASH:\n{tb_str}", file=sys.__stderr__)
    sys.__excepthook__(type, value, tb)

sys.excepthook = excepthook

# Now import and run the bot
sys.path.insert(0, ".")
from telegram_bot.bot import main

if __name__ == "__main__":
    logger.info("Bot starting...")
    print("Bot starting...", file=sys.__stdout__)
    main()
