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

    for match in fixtures:
        home = match["home"]
        away = match["away"]

        home_stats = get_last_five_stats(home)
        away_stats = get_last_five_stats(away)
        h2h_stats = get_h2h_stats(home, away)
        odds = get_odds(match["id"])

        # Apply your criteria
        if home_stats["avg_goals_scored"] < 1.5:
            continue

        if away_stats["btts_percent"] < 60:
            continue

        if h2h_stats["avg_goals"] < 3.0:
            continue

        # Decide recommended bet
        if odds["over25"] <= 2.20:
            recommended = "Over 2.5 Goals"
        elif away_stats["btts_percent"] >= 70:
            recommended = "BTTS & Over 2.5"
        else:
            recommended = "Over 1.5 First Half"

        shortlist.append({
            "time": match["time"],
            "home": home,
            "away": away,
            "home_avg": home_stats["avg_goals_scored"],
            "away_btts": away_stats["btts_percent"],
            "h2h": h2h_stats["avg_goals"],
            "odds": odds["over25"],
            "recommended": recommended
        })

    # Format message
    if not shortlist:
        await context.bot.send_message(chat_id, "No strong Overs fixtures today.")
        return

    msg = "🔥 *Today's Overs Shortlist*\n"
    msg += "_Based on last-5 scoring form, BTTS %, and fixture history._\n\n"

    for m in shortlist:
        msg += (
            f"*{m['time']} – {m['home']} vs {m['away']}*\n"
            f"Home avg goals (L5): {m['home_avg']}\n"
            f"Away BTTS: {m['away_btts']}%\n"
            f"H2H avg goals: {m['h2h']}\n"
            f"O2.5 odds: {m['odds']}\n"
            f"*Suggested bet:* {m['recommended']}\n\n"
        )

    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")

# -----------------------------
# LIVE MATCH FUNCTIONS
# -----------------------------

async def get_live_matches():
    url = "https://d.flashscore.com/x/feed/f_1_0_3_en_1"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
            text = await response.text()

            cleaned = text.replace("])}while(1);</x>", "")
            data = json.loads(cleaned)

            match_ids = []

            for item in data:
                if item[0] == "event":
                    match_ids.append(item[1])

            return match_ids

async def get_match_stats(match_id):
    url = f"https://d.flashscore.com/x/feed/d_{match_id}_en_1"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
            text = await response.text()
            cleaned = text.replace("])}while(1);</x>", "")
            data = json.loads(cleaned)

            stats = {
                "minute": 0,
                "score": "0-0",
                "shots_on_target": 0,
                "dangerous_attacks": 0,
                "home": "",
                "away": ""
            }

            for item in data:
                code = item[0]

                if code == "event":
                    stats["home"] = item[2]
                    stats["away"] = item[3]

                if code == "score":
                    stats["score"] = item[1]

                if code == "time":
                    stats["minute"] = int(item[1])

                if code == "stat":
                    label = item[1]
                    value = int(item[2])

                    if label == "Shots on Target":
                        stats["shots_on_target"] = value

                    if label == "Dangerous Attacks":
                        stats["dangerous_attacks"] = value

            return stats

# -----------------------------
# UPDATED OVERS TRIGGER LOGIC
# -----------------------------

def qualifies_for_overs(stats):
    return (
        stats["minute"] >= 60 and
        stats["shots_on_target"] >= 3 and
        stats["dangerous_attacks"] >= 50 and
        stats["score"] in ["0-0", "1-0", "0-1"]
    )

# -----------------------------
# JOB: CHECK MATCHES
# -----------------------------

async def check_matches(context):
    global last_scan_time, matches_checked, alerts_sent_today, currently_monitoring

    bot = context.bot

    match_ids = await get_live_matches()
    currently_monitoring = []  # reset list each cycle

    for match_id in match_ids:
        stats = await get_match_stats(match_id)

        # Track monitoring list
        currently_monitoring.append(f"{stats['home']} vs {stats['away']}")

        matches_checked += 1

        if qualifies_for_overs(stats):
            alerts_sent_today += 1

            message = (
                f"🔥 Overs Trigger!\n"
                f"{stats['home']} vs {stats['away']}\n"
                f"Minute: {stats['minute']}\n"
               
