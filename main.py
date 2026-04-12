import os
import logging
import json
import aiohttp
from telegram import Bot
from telegram.ext import ApplicationBuilder, CommandHandler

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# Load environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# /start command
async def start(update, context):
    await update.message.reply_text("Bot is running!")

# Startup message
async def send_startup_message(app):
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="Bot started successfully on Railway!")

# Fetch all live matches from Flashscore
async def get_live_matches():
    url = "https://d.flashscore.com/x/feed/f_1_0_3_en_1"  # all live matches

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

# Fetch stats for a specific match
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

# Overs trigger logic
def qualifies_for_overs(stats):
    return (
        stats["minute"] >= 60 and
        stats["shots_on_target"] >= 4 and
        stats["dangerous_attacks"] >= 55 and
        stats["score"] in ["0-0", "1-0", "0-1"]
    )

# Job: check all matches and send alerts
async def check_matches(context):
    bot = context.bot

    match_ids = await get_live_matches()

    for match_id in match_ids:
        stats = await get_match_stats(match_id)

        if qualifies_for_overs(stats):
            message = (
                f"🔥 Overs Trigger!\n"
                f"{stats['home']} vs {stats['away']}\n"
                f"Minute: {stats['minute']}\n"
                f"Score: {stats['score']}\n"
                f"Shots on Target: {stats['shots_on_target']}\n"
                f"Dangerous Attacks: {stats['dangerous_attacks']}"
            )

            await bot.send_message(chat_id=CHAT_ID, text=message)

def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(send_startup_message)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start))

    # Scheduler: check every 60 seconds
    app.job_queue.run_repeating(check_matches, interval=60, first=10)


    # Start bot
    app.run_polling()

if __name__ == "__main__":
    main()
