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
    Try multiple mobile JSON feed variants until one returns live match entries.
    Returns a list of match IDs (strings).
    """
    global ACTIVE_MOBILE_FEED
    headers = {"User-Agent": "Mozilla/5.0 (Mobile; rv:100.0) Gecko/20100101 Firefox/100.0"}
    async with aiohttp.ClientSession(headers=headers) as session:
        for feed in MOBILE_FEEDS:
            url = f"https://m.flashscore.com/x/feed/{feed}{MOBILE_SUFFIX}/"
            logger.info(f"Trying mobile feed: {feed} -> {url}")
            try:
                async with session.get(url, timeout=15) as resp:
                    if resp.status != 200:
                        logger.debug(f"Feed {feed} returned status {resp.status}")
                        continue
                    text = await resp.text()
            except Exception as e:
                logger.debug(f"Network error for feed {feed}: {e}")
                continue

            cleaned = text.replace("])}while(1);</x>", "")
            try:
                data = json.loads(cleaned)
            except Exception as e:
                logger.debug(f"JSON decode failed for feed {feed}: {e}")
                continue

            # Data structure varies; find event entries robustly
            match_ids = []
            for item in data:
                if not isinstance(item, list):
                    continue
                # event entries often start with "event" or have code "event"
                try:
                    if item[0] == "event" and len(item) > 1:
                        match_ids.append(str(item[1]))
                except Exception:
                    continue

            if match_ids:
                ACTIVE_MOBILE_FEED = feed
                logger.info(f"Active mobile feed found: {feed} ({len(match_ids)} matches)")
                return match_ids

    ACTIVE_MOBILE_FEED = "None working"
    logger.warning("No mobile feed returned matches.")
    return []

# ---------------------------------
# MATCH STATS (mobile detail endpoint)
# ---------------------------------
async def get_match_stats(match_id: str) -> dict:
    """
    Fetch detailed match JSON from mobile detail endpoint and extract:
    - minute
    - score (string)
    - shots_on_target (int)
    - dangerous_attacks (int)
    - home, away names
    """
    url = f"https://m.flashscore.com/x/feed/d_{match_id}{MOBILE_SUFFIX}/"
    headers = {"User-Agent": "Mozilla/5.0 (Mobile; rv:100.0) Gecko/20100101 Firefox/100.0"}
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(url, timeout=12) as resp:
                if resp.status != 200:
                    logger.debug(f"Detail {match_id} returned status {resp.status}")
                    return default_stats()
                text = await resp.text()
        except Exception as e:
            logger.debug(f"Network error for match {match_id}: {e}")
            return default_stats()

    cleaned = text.replace("])}while(1);</x>", "")
    try:
        data = json.loads(cleaned)
    except Exception as e:
        logger.debug(f"JSON decode error for match {match_id}: {e}")
        return default_stats()

    stats = default_stats()

    # Iterate and extract robustly
    for item in data:
        if not isinstance(item, list) or not item:
            continue
        code = item[0]

        # event: names
        if code == "event" and len(item) > 3:
            try:
                stats["home"] = item[2]
                stats["away"] = item[3]
            except Exception:
                pass

        # score
        elif code == "score" and len(item) > 1:
            try:
                stats["score"] = item[1]
            except Exception:
                pass

        # time / minute
        elif code == "time" and len(item) > 1:
            try:
                stats["minute"] = int(item[1])
            except Exception:
                # sometimes time is like "HT" or "45+2"
                try:
                    minute_str = str(item[1]).split("+")[0]
                    stats["minute"] = int(minute_str)
                except Exception:
                    stats["minute"] = 0

        # stat entries: label + value
        elif code == "stat" and len(item) > 2:
            label = str(item[1])
            value_raw = item[2]

            # Flexible matching for Shots on Target
            if flexible_stat_label_match(label, ["shot", "target"]) or flexible_stat_label_match(label, ["sot"]):
                stats["shots_on_target"] = extract_stat_value(label, value_raw)

            # Flexible matching for Dangerous Attacks
            elif flexible_stat_label_match(label, ["danger"]) or flexible_stat_label_match(label, ["attack"]):
                stats["dangerous_attacks"] = extract_stat_value(label, value_raw)

            # Some feeds use numeric keys or different ordering; attempt to parse common numeric labels
            else:
                # fallback: if label contains digits or short forms, try to map
                low = label.lower()
                if "on target" in low or "shots on target" in low or "sot" in low:
                    stats["shots_on_target"] = extract_stat_value(label, value_raw)
                elif "dangerous" in low or "danger" in low or "attacks" in low:
                    stats["dangerous_attacks"] = extract_stat_value(label, value_raw)

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

# ---------------------------------
# LIVE ODDS (mobile odds endpoint)
# ---------------------------------
async def get_live_odds(match_id: str) -> dict:
    """
    Fetch odds from mobile odds endpoint. Returns dict with 'over05' and 'over25' where available.
    """
    url = f"https://m.flashscore.com/x/feed/od_{match_id}{MOBILE_SUFFIX}/"
    headers = {"User-Agent": "Mozilla/5.0 (Mobile; rv:100.0) Gecko/20100101 Firefox/100.0"}
    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return {"over05": None, "over25": None}
                text = await resp.text()
        except Exception:
            return {"over05": None, "over25": None}

    cleaned = text.replace("])}while(1);</x>", "")
    try:
        data = json.loads(cleaned)
    except Exception:
        return {"over05": None, "over25": None}

    odds = {"over05": None, "over25": None}
    for item in data:
        if not isinstance(item, list) or len(item) < 3:
            continue
        if item[0] == "odds":
            key = str(item[1])
            val = item[2]
            try:
                valf = float(val)
            except Exception:
                try:
                    valf = float(str(val).replace(",", "."))
                except Exception:
                    valf = None
            if key.upper().startswith("O0.5") or "0.5" in key:
                odds["over05"] = valf
            if key.upper().startswith("O2.5") or "2.5" in key:
                odds["over25"] = valf
    return odds

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
        odds = await get_live_odds(match_id)
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
