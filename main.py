import os
import logging
import json
import aiohttp
from datetime import time, datetime
from telegram import Bot
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext

# ---------------------------------
# LOGGING
# ---------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ---------------------------------
# ENV VARS
# ---------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# ---------------------------------
# CONFIG
# ---------------------------------
QUIET_START_HOUR = 23
QUIET_END_HOUR = 6
QUIET_END_MINUTE = 30

MIN_O25_PRE = 1.80
MAX_O25_PRE = 2.40

MIN_PRESSURE = 30

# ---------------------------------
# GLOBAL STATE
# ---------------------------------
last_scan_time = None
matches_checked = 0
alerts_sent_today = 0
currently_monitoring = []

already_alerted = set()
last_alert = None

ACTIVE_FEED = None  # NEW — shows which Flashscore feed is working


# ---------------------------------
# HELPERS
# ---------------------------------
def in_quiet_hours(now: datetime) -> bool:
    start = now.replace(hour=QUIET_START_HOUR, minute=0, second=0, microsecond=0)

    if QUIET_END_HOUR < QUIET_START_HOUR or (
        QUIET_END_HOUR == QUIET_START_HOUR and QUIET_END_MINUTE > 0
    ):
        end = now.replace(hour=QUIET_END_HOUR, minute=QUIET_END_MINUTE,
                          second=0, microsecond=0)
        return now >= start or now < end
    else:
        end = now.replace(hour=QUIET_END_HOUR, minute=QUIET_END_MINUTE,
                          second=0, microsecond=0)
        return start <= now < end


def calc_pressure(stats: dict) -> float:
    return stats["shots_on_target"] * 5 + stats["dangerous_attacks"] * 0.5


# ---------------------------------
# COMMANDS
# ---------------------------------
async def start(update, context):
    if update.message:
        await update.message.reply_text("Bot is running!")


async def status(update, context):
    if not update.message:
        return

    global last_scan_time, matches_checked, alerts_sent_today, currently_monitoring, last_alert,
