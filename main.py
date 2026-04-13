import os
import logging
import json
import aiohttp
import asyncio
from datetime import datetime, time
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

# Mobile feed variants to try (bulletproof scanner)
MOBILE_FEEDS = [
    "l_0_0_3", "l_0_1_3", "l_1_0_3", "l_0_2_3", "l_2_0_3"
]

# Suffix used by mobile endpoints
MOBILE_SUFFIX = "_en_1"

# ---------------------------------
# GLOBAL STATE
# ---------------------------------
last_scan_time = None
matches_checked = 0
alerts_sent_today = 0
currently_monitoring = []
already_alerted = set()
last_alert = None
ACTIVE_MOBILE_FEED = None

# ---------------------------------
# HELPERS
# ---------------------------------
def in_quiet_hours(now: datetime) -> bool:
    start = now.replace(hour=QUIET_START_HOUR, minute=0, second=0, microsecond=0)
    end = now.replace(hour=QUIET_END_HOUR, minute=QUIET_END_MINUTE, second=0, microsecond=0)
    if QUIET_END_HOUR < QUIET_START_HOUR or (QUIET_END_HOUR == QUIET_START_HOUR and QUIET_END_MINUTE > 0):
        return now >= start or now < end
    return start <= now < end

def calc_pressure(stats: dict) -> float:
    return stats.get("shots_on_target", 0) * 5 + stats.get("dangerous_attacks", 0) * 0.5

def flexible_stat_label_match(label: str, keywords: list) -> bool:
    """
    Flexible matching: returns True if label contains all keywords (case-insensitive).
    keywords is a list of strings that should appear in label.
    """
    if not label:
        return False
    low = label.lower()
    return all(k.lower() in low for k in keywords)

def extract_stat_value(label: str, value_raw) -> int:
    """
    Try to coerce a stat value to int. If it's not numeric, return 0.
    """
    try:
        return int(value_raw)
    except Exception:
        try:
            return int(float(value_raw))
        except Exception:
            return 0

# ---------------------------------
# TELEGRAM COMMANDS
# ---------------------------------
async def start(update, context):
    if update.message:
        await update.message.reply_text("Bot is running!")

async def status(update, context):
    if not update.message:
        return
    global last_scan_time, matches_checked, alerts_sent_today, currently_monitoring, last_alert, ACTIVE_MOBILE_FEED
    feed_display = ACTIVE_MOBILE_FEED if ACTIVE_MOBILE_FEED else "None working"
    msg = "🤖 *Bot Status*\n\n"
    msg += f"Active mobile feed: {feed_display}\n"
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
    global last_scan_time, matches_checked, alerts_sent_today, currently_monitoring, already_alerted, last_alert
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
    try:
        await bot.send_message(chat_id=CHAT_ID, text="Bot started successfully (mobile JSON scanner).")
    except Exception as e:
        logger.warning(f"Startup message failed: {e}")

# ---------------------------------
# PRE-MATCH PLACEHOLDERS (unchanged)
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
# MOBILE JSON LIVE MATCH LIST SCANNER (bulletproof)
# ---------------------------------
async def get_live_matches():
    """
    Fetch all live football events from Sofascore.
    Returns a list of match IDs (integers).
    """
    url = "https://api.sofascore.com/api/v1/sport/football/events/live"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
        except Exception:
            return []

    events = data.get("events", [])
    match_ids = [e["id"] for e in events if "id" in e]
    return match_ids


# ---------------------------------
# MATCH STATS (mobile detail endpoint)
# ---------------------------------
asynasync def get_match_stats(match_id: int) -> dict:
    """
    Fetch match details from Sofascore.
    Extracts:
    - minute
    - score
    - shots_on_target
    - dangerous_attacks
    - home, away names
    """
    url = f"https://api.sofascore.com/api/v1/event/{match_id}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return default_stats()
                data = await resp.json()
        except Exception:
            return default_stats()

    event = data.get("event", {})
    home = event.get("homeTeam", {}).get("name", "")
    away = event.get("awayTeam", {}).get("name", "")
    status = event.get("status", {})
    minute = status.get("minute", 0)

    # Score
    home_score = event.get("homeScore", {}).get("current", 0)
    away_score = event.get("awayScore", {}).get("current", 0)
    score = f"{home_score}-{away_score}"

    # Statistics endpoint
    stats_url = f"https://api.sofascore.com/api/v1/event/{match_id}/statistics"

    shots_on_target = 0
    dangerous_attacks = 0

    try:
        async with session.get(stats_url, timeout=10) as resp:
            if resp.status == 200:
                stats_data = await resp.json()
                for group in stats_data.get("statistics", []):
                    for item in group.get("statisticsItems", []):
                        label = item.get("name", "").lower()
                        home_val = item.get("home", 0)
                        away_val = item.get("away", 0)

                        if "shots on target" in label:
                            shots_on_target = home_val + away_val
                        if "dangerous attacks" in label:
                            dangerous_attacks = home_val + away_val
    except Exception:
        pass

    return {
        "minute": minute,
        "score": score,
        "shots_on_target": shots_on_target,
        "dangerous_attacks": dangerous_attacks,
        "home": home,
        "away": away
    }


# ---------------------------------
# LIVE ODDS (mobile odds endpoint)
# ---------------------------------
async def get_live_odds(match_name: str) -> dict:
    """
    Fetch odds from LiveOdds API using match name search.
    Returns dict with 'over05' and 'over25'.
    """
    url = f"https://api.liveodds.io/football/search?query={match_name}"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return {"over05": None, "over25": None}
                data = await resp.json()
        except Exception:
            return {"over05": None, "over25": None}

    if not data:
        return {"over05": None, "over25": None}

    event = data[0]  # best match
    markets = event.get("markets", {})

    return {
        "over05": markets.get("over_0_5"),
        "over25": markets.get("over_2_5")
    }


# ---------------------------------
# TRIGGERS
# ---------------------------------
def qualifies_for_overs(stats: dict) -> bool:
    pressure = calc_pressure(stats)
    return (
        stats.get("minute", 0) >= 60 and
        stats.get("shots_on_target", 0) >= 3 and
        stats.get("dangerous_attacks", 0) >= 50 and
        stats.get("score", "") in ["0-0", "1-0", "0-1"] and
        pressure >= MIN_PRESSURE
    )

def qualifies_for_first_half_goal(stats: dict, odds: dict) -> bool:
    over05 = odds.get("over05")
    if over05 is None:
        return False
    pressure = calc_pressure(stats)
    return (
        stats.get("minute", 0) <= 45 and
        stats.get("score", "") == "0-0" and
        stats.get("shots_on_target", 0) >= 2 and
        stats.get("dangerous_attacks", 0) >= 30 and
        pressure >= 20 and
        1.90 <= over05 <= 2.10
    )

# ---------------------------------
# JOB: CHECK MATCHES
# ---------------------------------
async def check_matches(context: CallbackContext):
    global last_scan_time, matches_checked, alerts_sent_today, currently_monitoring, already_alerted, last_alert
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
            logger.debug(f"Error fetching stats for {match_id}: {e}")
            continue

        match_name = f"{stats.get('home','').strip()} vs {stats.get('away','').strip()}".strip()
        if not match_name or match_name == "vs":
            match_name = f"Match {match_id}"

        currently_monitoring.append(match_name)
        matches_checked += 1

        # FIRST-HALF GOAL TRIGGER
    odds = await get_live_odds(match_name)
     if qualifies_for_first_half_goal(stats, odds) and match_id not in already_alerted:
            already_alerted.add(match_id)
            message = (
                f"⚡ First-Half Goal Trigger!\n"
                f"{match_name}\n"
                f"Minute: {stats.get('minute')}\n"
                f"Score: {stats.get('score')}\n"
                f"Shots on Target: {stats.get('shots_on_target')}\n"
                f"Dangerous Attacks: {stats.get('dangerous_attacks')}\n"
                f"Pressure: {calc_pressure(stats)}\n"
                f"Live O0.5 Odds: {odds.get('over05')}\n"
            )
            now = datetime.now()
            if not in_quiet_hours(now):
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=message)
                except Exception as e:
                    logger.debug(f"Failed to send first-half alert: {e}")
            else:
                logger.info(f"Quiet hours – first-half alert suppressed for {match_name}")

        # OVERS TRIGGER
        if qualifies_for_overs(stats) and match_id not in already_alerted:
            already_alerted.add(match_id)
            pressure = calc_pressure(stats)
            now = datetime.now()
            last_alert = {
                "match": match_name,
                "time": now.strftime("%H:%M:%S"),
                "minute": stats.get("minute"),
                "score": stats.get("score"),
                "shots_on_target": stats.get("shots_on_target"),
                "dangerous_attacks": stats.get("dangerous_attacks"),
                "pressure": pressure
            }
            alerts_sent_today += 1
            message = (
                f"🔥 Overs Trigger!\n"
                f"{match_name}\n"
                f"Minute: {stats.get('minute')}\n"
                f"Score: {stats.get('score')}\n"
                f"Shots on Target: {stats.get('shots_on_target')}\n"
                f"Dangerous Attacks: {stats.get('dangerous_attacks')}\n"
                f"Pressure: {pressure}\n"
            )
            if not in_quiet_hours(now):
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=message)
                except Exception as e:
                    logger.debug(f"Failed to send overs alert: {e}")
            else:
                logger.info(f"Quiet hours – overs alert suppressed for {match_name}")

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

    # Scan every 60 seconds
    app.job_queue.run_repeating(check_matches, interval=60, first=10)

    # Morning shortlist at 09:00
    app.job_queue.run_daily(morning_shortlist, time=time(9, 0), name="morning_shortlist")

    app.run_polling()

if __name__ == "__main__":
    main()
