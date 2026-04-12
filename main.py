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
# Quiet hours: no alerts between 23:00 and 06:30
QUIET_START_HOUR = 23
QUIET_END_HOUR = 6
QUIET_END_MINUTE = 30

# Odds filters (placeholders – wire to real odds source later)
MIN_O25_PRE = 1.80
MAX_O25_PRE = 2.40

# Pressure index config
MIN_PRESSURE = 30  # tweak as you like

# ---------------------------------
# GLOBAL STATE
# ---------------------------------
last_scan_time = None
matches_checked = 0
alerts_sent_today = 0
currently_monitoring = []

already_alerted = set()  # match_ids already alerted
last_alert = None        # dict with last alert info


# ---------------------------------
# HELPERS
# ---------------------------------
def in_quiet_hours(now: datetime) -> bool:
    """Return True if current time is within quiet hours."""
    start = now.replace(hour=QUIET_START_HOUR, minute=0, second=0, microsecond=0)

    # Quiet end is next day if end hour < start hour
    if QUIET_END_HOUR < QUIET_START_HOUR or (
        QUIET_END_HOUR == QUIET_START_HOUR and QUIET_END_MINUTE > 0
    ):
        # crosses midnight
        end = now.replace(hour=QUIET_END_HOUR, minute=QUIET_END_MINUTE,
                          second=0, microsecond=0)
        if now >= start or now < end:
            return True
    else:
        # same-day window
        end = now.replace(hour=QUIET_END_HOUR, minute=QUIET_END_MINUTE,
                          second=0, microsecond=0)
        if start <= now < end:
            return True

    return False


def calc_pressure(stats: dict) -> float:
    """Simple pressure index based on shots on target and dangerous attacks."""
    return stats["shots_on_target"] * 5 + stats["dangerous_attacks"] * 0.5


# ---------------------------------
# COMMANDS
# ---------------------------------
async def start(update, context):
    if not update.message:
        return
    await update.message.reply_text("Bot is running!")


async def status(update, context):
    if not update.message:
        return

    global last_scan_time, matches_checked, alerts_sent_today, currently_monitoring, last_alert

    msg = "🤖 *Bot Status*\n\n"
    msg += f"Last scan: {last_scan_time if last_scan_time else 'No scans yet'}\n"
    msg += f"Matches checked: {matches_checked}\n"
    msg += f"Alerts sent today: {alerts_sent_today}\n\n"

    if currently_monitoring:
        msg += "*Currently monitoring:*\n"
        for m in currently_monitoring:
            msg += f"- {m}\n"
    else:
        msg += "No matches currently being monitored.\n"

    if last_alert:
        msg += "\n*Last alert:*\n"
        msg += f"{last_alert['match']}\n"
        msg += f"Time: {last_alert['time']}\n"
        msg += f"Minute: {last_alert['minute']}\n"
        msg += f"Score: {last_alert['score']}\n"
        msg += f"Shots on Target: {last_alert['shots_on_target']}\n"
        msg += f"Dangerous Attacks: {last_alert['dangerous_attacks']}\n"
        msg += f"Pressure: {last_alert['pressure']}\n"
    else:
        msg += "\nNo alerts have been sent yet."

    await update.message.reply_text(msg, parse_mode="Markdown")


async def resetstats(update, context):
    if not update.message:
        return

    global last_scan_time, matches_checked, alerts_sent_today
    global currently_monitoring, already_alerted, last_alert

    last_scan_time = None
    matches_checked = 0
    alerts_sent_today = 0
    currently_monitoring = []
    already_alerted = set()
    last_alert = None

    await update.message.reply_text("Stats reset.")


async def lastalert_cmd(update, context):
    if not update.message:
        return

    global last_alert

    if not last_alert:
        await update.message.reply_text("No alerts have been sent yet.")
        return

    msg = "*Last Alert Details:*\n\n"
    msg += f"{last_alert['match']}\n"
    msg += f"Time: {last_alert['time']}\n"
    msg += f"Minute: {last_alert['minute']}\n"
    msg += f"Score: {last_alert['score']}\n"
    msg += f"Shots on Target: {last_alert['shots_on_target']}\n"
    msg += f"Dangerous Attacks: {last_alert['dangerous_attacks']}\n"
    msg += f"Pressure: {last_alert['pressure']}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


# ---------------------------------
# STARTUP MESSAGE
# ---------------------------------
async def send_startup_message(app):
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="Bot started successfully on Railway!")


# ---------------------------------
# PRE-MATCH (STILL PLACEHOLDER DATA)
# ---------------------------------
def get_todays_fixtures():
    """
    Placeholder – wire this to a real data source later.
    Expected structure:
    [
        {
            "id": "match_id",
            "time": "14:00",
            "home": "Team A",
            "away": "Team B",
            "o25_odds": 2.05
        },
        ...
    ]
    """
    return []


def get_last_five_stats(team):
    # Placeholder – replace with real stats
    return {
        "avg_goals_scored": 0,
        "btts_percent": 0
    }


def get_h2h_stats(home, away):
    # Placeholder – replace with real stats
    return {
        "avg_goals": 0
    }


def get_odds(match_id):
    # Placeholder – replace with real odds
    return {
        "over25": 3.00
    }


async def morning_shortlist(context: CallbackContext):
    chat_id = int(CHAT_ID)

    fixtures = get_todays_fixtures()
    shortlist = []

    for match in fixtures:
        home = match["home"]
        away = match["away"]

        home_stats = get_last_five_stats(home)
        away_stats = get_last_five_stats(away)
        h2h_stats = get_h2h_stats(home, away)
        odds = get_odds(match["id"])

        o25 = odds["over25"]

        # Odds filter
        if not (MIN_O25_PRE <= o25 <= MAX_O25_PRE):
            continue

        # Example criteria – tweak when real data is wired
        if home_stats["avg_goals_scored"] < 1.5:
            continue

        if away_stats["btts_percent"] < 60:
            continue

        if h2h_stats["avg_goals"] < 3.0:
            continue

        if o25 <= 2.20:
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
            "odds": o25,
            "recommended": recommended
        })

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


# ---------------------------------
# LIVE MATCH FUNCTIONS
# ---------------------------------
async def get_live_matches():
    url = "https://d.flashscore.com/x/feed/f_1_0_3_en_1"

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
            text = await response.text()
            cleaned = text.replace("])}while(1);</x>", "")

            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding live matches JSON: {e}")
                return []

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

            try:
                data = json.loads(cleaned)
            except json.JSONDecodeError as e:
                logger.error(f"Error decoding match stats JSON for {match_id}: {e}")
                return {
                    "minute": 0,
                    "score": "0-0",
                    "shots_on_target": 0,
                    "dangerous_attacks": 0,
                    "home": "",
                    "away": ""
                }

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
                    try:
                        stats["minute"] = int(item[1])
                    except ValueError:
                        stats["minute"] = 0

                if code == "stat":
                    label = item[1]
                    try:
                        value = int(item[2])
                    except ValueError:
                        value = 0

                    if label == "Shots on Target":
                        stats["shots_on_target"] = value

                    if label == "Dangerous Attacks":
                        stats["dangerous_attacks"] = value

            return stats


def qualifies_for_overs(stats):
    pressure = calc_pressure(stats)

    return (
        stats["minute"] >= 60 and
        stats["shots_on_target"] >= 3 and
        stats["dangerous_attacks"] >= 50 and
        stats["score"] in ["0-0", "1-0", "0-1"] and
        pressure >= MIN_PRESSURE
    )


# ---------------------------------
# JOB: CHECK MATCHES (CRASH-PROOF, DUPLICATE-SAFE, QUIET HOURS)
# ---------------------------------
async def check_matches(context: CallbackContext):
    global last_scan_time, matches_checked, alerts_sent_today
    global currently_monitoring, already_alerted, last_alert

    bot = context.bot
    currently_monitoring = []  # reset each cycle

    try:
        match_ids = await get_live_matches()
    except Exception as e:
        logger.error(f"Error fetching live matches: {e}")
        last_scan_time = datetime.now().strftime("%H:%M:%S")
        return

    if not match_ids:
        last_scan_time = datetime.now().strftime("%H:%M:%S")
        return

    for match_id in match_ids:
        try:
            stats = await get_match_stats(match_id)
        except Exception as e:
            logger.error(f"Error fetching stats for {match_id}: {e}")
            continue

        match_name = f"{stats['home']} vs {stats['away']}"
        currently_monitoring.append(match_name)
        matches_checked += 1

        if not qualifies_for_overs(stats):
            continue

        # Duplicate alert protection
        if match_id in already_alerted:
            continue

        already_alerted.add(match_id)

        # Build alert info
        pressure = calc_pressure(stats)
        now = datetime.now()

        last_alert = {
            "match": match_name,
            "time": now.strftime("%H:%M:%S"),
            "minute": stats["minute"],
            "score": stats["score"],
            "shots_on_target": stats["shots_on_target"],
            "dangerous_attacks": stats["dangerous_attacks"],
            "pressure": pressure
        }

        alerts_sent_today += 1

        message = (
            f"🔥 Overs Trigger!\n"
            f"{match_name}\n"
            f"Minute: {stats['minute']}\n"
            f"Score: {stats['score']}\n"
            f"Shots on Target: {stats['shots_on_target']}\n"
            f"Dangerous Attacks: {stats['dangerous_attacks']}\n"
            f"Pressure: {pressure}\n"
        )

        # Respect quiet hours
        if in_quiet_hours(now):
            logger.info(f"Quiet hours – alert suppressed for {match_name}")
        else:
            await bot.send_message(chat_id=CHAT_ID, text=message)

    last_scan_time = datetime.now().strftime("%H:%M:%S")


# ---------------------------------
# MAIN
# ---------------------------------
def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(send_startup_message)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("resetstats", resetstats))
    app.add_handler(CommandHandler("lastalert", lastalert_cmd))

    # Live scanner every 60 seconds
    app.job_queue.run_repeating(check_matches, interval=60, first=10)

    # Morning shortlist at 09:00
    app.job_queue.run_daily(
        morning_shortlist,
        time=time(9, 0),
        name="morning_shortlist"
    )

    app.run_polling()


if __name__ == "__main__":
    main()
hi
