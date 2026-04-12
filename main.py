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

ACTIVE_FEED = None  # will show which feed is currently working


# ---------------------------------
# HELPERS
# ---------------------------------
def in_quiet_hours(now: datetime) -> bool:
    start = now.replace(hour=QUIET_START_HOUR, minute=0, second=0, microsecond=0)

    # Quiet hours cross midnight (e.g. 23:00–06:30)
    if QUIET_END_HOUR < QUIET_START_HOUR or (
        QUIET_END_HOUR == QUIET_START_HOUR and QUIET_END_MINUTE > 0
    ):
        end = now.replace(
            hour=QUIET_END_HOUR,
            minute=QUIET_END_MINUTE,
            second=0,
            microsecond=0
        )
        return now >= start or now < end
    else:
        # Quiet hours do not cross midnight
        end = now.replace(
            hour=QUIET_END_HOUR,
            minute=QUIET_END_MINUTE,
            second=0,
            microsecond=0
        )
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

    global last_scan_time, matches_checked, alerts_sent_today
    global currently_monitoring, last_alert, ACTIVE_FEED

    feed_display = ACTIVE_FEED if ACTIVE_FEED else "None"

    msg = "🤖 *Bot Status*\n\n"
    msg += f"Active feed: {feed_display}\n"
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
        for k, v in last_alert.items():
            msg += f"{k.capitalize()}: {v}\n"
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
    for k, v in last_alert.items():
        msg += f"{k.capitalize()}: {v}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")


# ---------------------------------
# STARTUP MESSAGE
# ---------------------------------
async def send_startup_message(app):
    bot = Bot(token=BOT_TOKEN)
    await bot.send_message(chat_id=CHAT_ID, text="Bot started successfully on Railway!")


# ---------------------------------
# PRE-MATCH PLACEHOLDERS
# ---------------------------------
def get_todays_fixtures():
    return []


def get_last_five_stats(team):
    return {"avg_goals_scored": 0, "btts_percent": 0}


def get_h2h_stats(home, away):
    return {"avg_goals": 0}


def get_odds(match_id):
    return {"over25": 3.00}


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

        if not (MIN_O25_PRE <= o25 <= MAX_O25_PRE):
            continue

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
# LIVE MATCH FUNCTIONS (WORLDWIDE)
# ---------------------------------
async def get_live_matches():
    """
    Fetch live match IDs from Flashscore worldwide feed rotation.
    """
    global ACTIVE_FEED

    # Bases and language suffixes used to build full feed URLs
    FEED_BASES = [
        "f_1_0_3",
        "f_1_0_2",
        "f_1_0_1",
        "f_1_0_4",
        "f_1_0_0",
        "f_1_0_5",
        "f_1_0_6"
    ]
    LANG_SUFFIXES = ["en_1", "en_2", "en_3"]

    FEEDS = []
    for base in FEED_BASES:
        for lang in LANG_SUFFIXES:
            FEEDS.append(f"{base}_{lang}")

    for feed in FEEDS:
        url = f"https://d.flashscore.com/x/feed/{feed}"
        logger.info(f"Trying feed: {feed} ({url})")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
                    text = await response.text()
                    cleaned = text.replace("])}while(1);</x>", "")

                    try:
                        data = json.loads(cleaned)
                    except Exception as e:
                        logger.warning(f"JSON error on feed {feed}: {e}")
                        continue

                    match_ids = [
                        item[1] for item in data
                        if isinstance(item, list) and len(item) > 1 and item[0] == "event"
                    ]

                    if match_ids:
                        ACTIVE_FEED = feed
                        logger.info(f"Active feed set to: {feed} with {len(match_ids)} matches")
                        return match_ids

        except Exception as e:
            logger.error(f"Feed error ({feed}): {e}")
            continue

    ACTIVE_FEED = "None working"
    logger.warning("No working feed found from the configured list.")
    return []


async def get_match_stats(match_id):
    """
    Fetch stats for a specific match.
    """
    url = f"https://d.flashscore.com/x/feed/d_{match_id}_en_1"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
                text = await response.text()
        except Exception as e:
            logger.error(f"Network error for match {match_id}: {e}")
            return default_stats()

    cleaned = text.replace("])}while(1);</x>", "")

    try:
        data = json.loads(cleaned)
    except Exception as e:
        logger.error(f"JSON decode error for match {match_id}: {e}")
        return default_stats()

    stats = default_stats()

    for item in data:
        if not isinstance(item, list):
            continue

        code = item[0]

        if code == "event":
            if len(item) > 3:
                stats["home"] = item[2]
                stats["away"] = item[3]

        elif code == "score":
            if len(item) > 1:
                stats["score"] = item[1]

        elif code == "time":
            if len(item) > 1:
                try:
                    stats["minute"] = int(item[1])
                except Exception:
                    stats["minute"] = 0

        elif code == "stat":
            if len(item) > 2:
                label = item[1]
                try:
                    value = int(item[2])
                except Exception:
                    value = 0

                if label == "Shots on Target":
                    stats["shots_on_target"] = value
                elif label == "Dangerous Attacks":
                    stats["dangerous_attacks"] = value

    return stats


def default_stats():
    return {
        "minute": 0,
        "score": "0-0",
        "shots_on_target": 0,
        "dangerous_attacks": 0,
        "home": "",
        "away": ""
    }


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
# LIVE O0.5 ODDS FETCHER
# ---------------------------------
async def get_live_odds(match_id):
    url = f"https://d.flashscore.com/x/feed/od_{match_id}_en_1"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
                text = await response.text()
        except:
            return {"over05": None}

    cleaned = text.replace("])}while(1);</x>", "")

    try:
        data = json.loads(cleaned)
    except:
        return {"over05": None}

    odds = {"over05": None}

    for item in data:
        if not isinstance(item, list):
            continue

        if item[0] == "odds" and len(item) > 2:
            if item[1] == "O0.5":
                try:
                    odds["over05"] = float(item[2])
                except:
                    odds["over05"] = None

    return odds


# ---------------------------------
# FIRST-HALF GOAL FILTER
# ---------------------------------
def qualifies_for_first_half_goal(stats, odds):
    if odds["over05"] is None:
        return False

    pressure = calc_pressure(stats)

    return (
        stats["minute"] <= 45 and
        stats["score"] == "0-0" and
        stats["shots_on_target"] >= 2 and
        stats["dangerous_attacks"] >= 30 and
        pressure >= 20 and
        1.90 <= odds["over05"] <= 2.10
    )


# ---------------------------------
# JOB: CHECK MATCHES
# ---------------------------------
async def check_matches(context: CallbackContext):
    global last_scan_time, matches_checked, alerts_sent_today
    global currently_monitoring, already_alerted, last_alert

    bot = context.bot
    currently_monitoring = []

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

        match_name = f"{stats['home']} vs {stats['away']}".strip()
        if not match_name.strip():
            match_name = f"Match {match_id}"

        currently_monitoring.append(match_name)
        matches_checked += 1

        # FIRST-HALF GOAL TRIGGER
        odds = await get_live_odds(match_id)

        if qualifies_for_first_half_goal(stats, odds) and match_id not in already_alerted:
            already_alerted.add(match_id)

            message = (
                f"⚡ First-Half Goal Trigger!\n"
                f"{match_name}\n"
                f"Minute: {stats['minute']}\n"
                f"Score: {stats['score']}\n"
                f"Shots on Target: {stats['shots_on_target']}\n"
                f"Dangerous Attacks: {stats['dangerous_attacks']}\n"
                f"Pressure: {calc_pressure(stats)}\n"
                f"Live O0.5 Odds: {odds['over05']}\n"
            )

            now = datetime.now()
            if not in_quiet_hours(now):
                await bot.send_message(chat_id=CHAT_ID, text=message)
            else:
                logger.info(f"Quiet hours – first-half alert suppressed for {match_name}")

        # EXISTING OVERS TRIGGER
        if not qualifies_for_overs(stats):
            continue

        if match_id in already_alerted:
            continue

        already_alerted.add(match_id)

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

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("resetstats", resetstats))
    app.add_handler(CommandHandler("lastalert", lastalert_cmd))

    # Instant scanning: every 60 seconds
    app.job_queue.run_repeating(check_matches, interval=60, first=10)

    app.job_queue.run_daily(
        morning_shortlist,
        time=time(9, 0),
        name="morning_shortlist"
    )

    app.run_polling()


if __name__ == "__main__":
    main()
