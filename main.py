import os
import logging
import json
import aiohttp
from datetime import time, datetime
from telegram import Bot
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackContext

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Load environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# -----------------------------
# GLOBAL TRACKING VARIABLES
# -----------------------------
last_scan_time = None
matches_checked = 0
alerts_sent_today = 0
currently_monitoring = []

# /start command
async def start(update, context):
    await update.message.reply_text("Bot is running!")

# -----------------------------
# /status COMMAND
# -----------------------------
async def status(update, context):
    global last_scan_time, matches_checked, alerts_sent_today, currently_monitoring

    msg = "🤖 *Bot Status*\n\n"

    msg += f"Last scan: {last_scan_time if last_scan_time else 'No scans yet'}\n"
    msg += f"Matches checked: {matches_checked}\n"
    msg += f"Alerts sent today: {alerts_sent_today}\n\n"

    if currently_monitoring:
        msg += "*Currently monitoring:*\n"
        for m in currently_monitoring:
            msg += f"- {m}\n"
    else:
        msg += "No matches currently being monitored."

    await update.message.reply_text(msg, parse_mode="Markdown")

# Startup message
async def send_startup_message(app):
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="Bot started successfully on Railway!")

# -----------------------------
# PLACEHOLDER PRE-MATCH FUNCTIONS
# -----------------------------

def get_todays_fixtures():
    return []  # placeholder

def get_last_five_stats(team):
    return {
        "avg_goals_scored": 0,
        "btts_percent": 0
    }

def get_h2h_stats(home, away):
    return {
        "avg_goals": 0
    }

def get_odds(match_id):
    return {
        "over25": 3.00
    }

# -----------------------------
# MORNING SHORTLIST FUNCTION
# -----------------------------

async def morning_shortlist(context: CallbackContext):
    chat_id = 8434225865  # your ID

    fixtures = get_todays_fixtures()
    shortlist = []

