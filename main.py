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
async def get_last_10_o25_rate(team_id: int) -> float:
    """
    Fetch last 10 matches for a team from Sofascore and calculate % Over 2.5.
    Returns a number between 0 and 100.
    """
    url = f"https://api.sofascore.com/api/v1/team/{team_id}/events/last/10"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    return 0
                data = await resp.json()
        except Exception:
            return 0

    events = data.get("events", [])
    if not events:
        return 0

    o25_count = 0
    for ev in events:
        home = ev.get("homeScore", {}).get("current", 0)
        away = ev.get("awayScore", {}).get("current", 0)
        if home + away >= 3:
            o25_count += 1

    return (o25_count / len(events)) * 100

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
async def acca_cmd(update, context):
    await daily_acca(context)
async def fixtures_cmd(update, context):
    fixtures = await get_todays_fixtures()


    if not fixtures:
        await update.message.reply_text("No fixtures available for today.")
        return

    msg = "📅 *Today's Fixtures*\n\n"
    for m in fixtures:
        msg += f"{m.get('time', 'TBD')} – {m['home']} vs {m['away']}\n"

    await update.message.reply_text(msg, parse_mode="Markdown")
async def debug_fixtures_cmd(update, context):
    chat_id = update.effective_chat.id
    today = datetime.now().strftime("%Y-%m-%d")

    msg = "🛠 *Fixtures Debug Report*\n"
    msg += f"Date: {today}\n\n"

    async with aiohttp.ClientSession() as session:
async def debugfixtures_new(update, context):
    chat_id = update.effective_chat.id
    today = datetime.now().strftime("%Y-%m-%d")

    url = f"https://api.sofascore.com/api/v1/sport/football/events/{today}"

    msg = f"🆕 *New Fixtures Endpoint Debug*\nDate: {today}\n\n"
    msg += f"URL: {url}\n\n"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                msg += f"HTTP Status: {resp.status}\n"
                if resp.status != 200:
                    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")
                    return

                data = await resp.json()
                events = data.get("events", [])

                msg += f"Events returned: {len(events)}\n\n"

                if events:
                    e = events[0]
                    msg += f"Sample: {e.get('homeTeam', {}).get('name')} vs {e.get('awayTeam', {}).get('name')}\n"
                else:
                    msg += "No events in response.\n"

        except Exception as e:
            msg += f"Exception: {e}\n"

    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")

        # 1️⃣ MOBILE FEED
        mobile_url = f"https://api.sofascore.com/mobile/v4/sport/football/scheduled-events/{today}"
        try:
            async with session.get(mobile_url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    events = data.get("events", [])
                    msg += f"📱 Mobile feed: {len(events)} events\n"
                    if events:
                        msg += f"Sample: {events[0].get('homeTeam', {}).get('name')} vs {events[0].get('awayTeam', {}).get('name')}\n\n"
                    else:
                        msg += "Sample: None\n\n"
                else:
                    msg += f"📱 Mobile feed ERROR: HTTP {resp.status}\n\n"
        except Exception as e:
            msg += f"📱 Mobile feed EXCEPTION: {e}\n\n"

        # 2️⃣ OLD API FEED
        old_url = f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{today}"
        try:
            async with session.get(old_url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    events = data.get("events", [])
                    msg += f"🖥 Old API feed: {len(events)} events\n"
                    if events:
                        msg += f"Sample: {events[0].get('homeTeam', {}).get('name')} vs {events[0].get('awayTeam', {}).get('name')}\n\n"
                    else:
                        msg += "Sample: None\n\n"
                else:
                    msg += f"🖥 Old API feed ERROR: HTTP {resp.status}\n\n"
        except Exception as e:
            msg += f"🖥 Old API feed EXCEPTION: {e}\n\n"

        # 3️⃣ TOURNAMENT SCAN
        tournaments_url = "https://api.sofascore.com/api/v1/sport/football/tournaments"
        total_events = 0
        try:
            async with session.get(tournaments_url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tournaments = data.get("tournaments", [])
                    for t in tournaments[:20]:
                        tid = t.get("id")
                        if not tid:
                            continue
                        t_url = f"https://api.sofascore.com/api/v1/tournament/{tid}/events/{today}"
                        try:
                            async with session.get(t_url, timeout=10) as t_resp:
                                if t_resp.status == 200:
                                    t_data = await t_resp.json()
                                    evs = t_data.get("events", [])
                                    total_events += len(evs)
                        except:
                            pass
                msg += f"🏆 Tournament scan: {total_events} events\n"
        except Exception as e:
            msg += f"🏆 Tournament scan EXCEPTION: {e}\n"

    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")

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
async def get_todays_fixtures():
    """
    NEW Sofascore fixtures loader (working 2026).
    Uses the updated endpoint:
    /api/v1/sport/football/events/{date}
    """

    today = datetime.now().strftime("%Y-%m-%d")
    url = f"https://api.sofascore.com/api/v1/sport/football/events/{today}"

    logger.info(f"[Fixtures] Using NEW endpoint: {url}")

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(url, timeout=10) as resp:
                if resp.status != 200:
                    logger.warning(f"[Fixtures] New API returned HTTP {resp.status}")
                    return []

                data = await resp.json()
                events = data.get("events", [])

                logger.info(f"[Fixtures] New API returned {len(events)} events")

                if not events:
                    return []

                return _parse_fixtures(events)

        except Exception as e:
            logger.error(f"[Fixtures] New API error: {e}")
            return []





def get_last_five_stats(team):
    return {"avg_goals_scored": 0, "btts_percent": 0}

def get_h2h_stats(home, away):
    return {"avg_goals": 0}

def get_odds(match_id):
    return {"over25": 3.00}

async def morning_shortlist(context: CallbackContext):
    chat_id = int(CHAT_ID)
    fixtures = await get_todays_fixtures()
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
async def daily_acca(context: CallbackContext):
    chat_id = int(CHAT_ID)

    # Fetch today's fixtures
    fixtures = await get_todays_fixtures()
    if not fixtures:
        await context.bot.send_message(chat_id, "No fixtures available for today's ACCA.")
        return

    acca_list = []

    for match in fixtures:
        home = match["home"]
        away = match["away"]
        home_id = match.get("home_id")
        away_id = match.get("away_id")

        if not home_id or not away_id:
            continue

        # Get last-10 O2.5 rates
        home_o25 = await get_last_10_o25_rate(home_id)
        away_o25 = await get_last_10_o25_rate(away_id)
        combined = (home_o25 + away_o25) / 2

        # Only keep strong O2.5 candidates
        if combined >= 70:
            odds = get_odds(match["id"])
            o25 = odds.get("over25", None)
            if o25:
                acca_list.append({
                    "home": home,
                    "away": away,
                    "home_o25": round(home_o25),
                    "away_o25": round(away_o25),
                    "combined": round(combined),
                    "odds": o25
                })

    if len(acca_list) < 3:
        await context.bot.send_message(chat_id, "No suitable 3-leg O2.5 ACCA found today.")
        return

    # Sort by combined O2.5 %
    acca_list.sort(key=lambda x: x["combined"], reverse=True)

    # Take top 3
    picks = acca_list[:3]

    # Calculate combined odds
    acca_price = round(picks[0]["odds"] * picks[1]["odds"] * picks[2]["odds"], 2)

    # Build message
    msg = "🎯 *Daily O2.5 ACCA (Stats-Based)*\n"
    msg += "_Teams with 70%+ Over 2.5 in their last 10 matches._\n\n"

    for p in picks:
        msg += (
            f"*{p['home']} vs {p['away']}*\n"
            f"Home O2.5: {p['home_o25']}%\n"
            f"Away O2.5: {p['away_o25']}%\n"
            f"Combined: {p['combined']}%\n"
            f"O2.5 Odds: {p['odds']}\n\n"
        )

    msg += f"*Combined ACCA Odds:* {acca_price}"

    await context.bot.send_message(chat_id, msg, parse_mode="Markdown")

      

# ---------------------------------
# MOBILE JSON LIVE MATCH LIST SCANNER (bulletproof)
# ---------------------------------
async def get_live_matches():
    """
    Fetch live matches using mobile feed first.
    Fallback to old API if mobile feed returns nothing.
    Returns a list of match IDs.
    """

    # 1️⃣ Try MOBILE live feed (most reliable)
    mobile_url = "https://api.sofascore.com/mobile/v4/sport/football/events/live"

    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(mobile_url, timeout=10) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    events = data.get("events", [])
                    if events:
                        return [e["id"] for e in events if "id" in e]
        except Exception:
            pass  # ignore and fallback

    # 2️⃣ Fallback: OLD API live feed
    fallback_url = "https://api.sofascore.com/api/v1/sport/football/events/live"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(fallback_url, timeout=10) as resp:
                if resp.status != 200:
                    return []
                data = await resp.json()
                events = data.get("events", [])
                return [e["id"] for e in events if "id" in e]
    except Exception:
        return []


    events = data.get("events", [])
    match_ids = [e["id"] for e in events if "id" in e]
    return match_ids


# ---------------------------------
# MATCH STATS (mobile detail endpoint)
# --------------------------------
async def get_match_stats(match_id: int) -> dict:
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

        # -----------------------------------------
        # 🔥 FIRST-HALF GOAL TRIGGER (HIGH INTENSITY)
        # -----------------------------------------
        odds = await get_live_odds(match_name)

        if qualifies_for_first_half_goal(stats, odds) and match_id not in already_alerted:
            already_alerted.add(match_id)

            message = (
                f"🔥 FIRST HALF GOAL ALERT — HIGH INTENSITY\n"
                f"Match: {match_name}\n"
                f"Minute: {stats.get('minute')}'\n"
                f"Score: {stats.get('score')}\n"
                f"SOT: {stats.get('shots_on_target')}\n"
                f"Dangerous Attacks: {stats.get('dangerous_attacks')}\n"
                f"Pressure: {calc_pressure(stats)}\n"
                f"Odds O0.5 FH: {odds.get('over05')}\n"
            )

            now = datetime.now()
            if not in_quiet_hours(now):
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=message)
                except Exception as e:
                    logger.debug(f"Failed to send high-intensity FH alert: {e}")
            else:
                logger.info(f"Quiet hours – high-intensity FH alert suppressed for {match_name}")

        # -----------------------------------------
        # 🎯 PROBABILITY-MODEL FIRST HALF GOAL ALERT
        # -----------------------------------------
        minute = stats.get("minute", 0)
        score = stats.get("score", "")
        over05 = odds.get("over05")

        if (
            match_id not in already_alerted and
            score == "0-0" and
            33 <= minute <= 45 and
            over05 is not None and over05 >= 2.0
        ):
            already_alerted.add(match_id)

            message = (
                f"🎯 FIRST HALF GOAL ALERT — PROBABILITY MODEL\n"
                f"Match: {match_name}\n"
                f"Minute: {minute}'\n"
                f"Score: {score}\n"
                f"Odds O0.5 FH: {over05}\n"
                f"Pre-match xG: High\n"
                f"FH Goal History: Strong\n"
            )

            now = datetime.now()
            if not in_quiet_hours(now):
                try:
                    await bot.send_message(chat_id=CHAT_ID, text=message)
                except Exception as e:
                    logger.debug(f"Failed to send probability-model FH alert: {e}")
            else:
                logger.info(f"Quiet hours – probability-model FH alert suppressed for {match_name}")

        # -----------------------------------------
        # 🔥 OVERS TRIGGER
        # -----------------------------------------
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
    app.add_handler(CommandHandler("acca", acca_cmd))
    app.add_handler(CommandHandler("fixtures", fixtures_cmd))
    app.add_handler(CommandHandler("debugfixtures", debug_fixtures_cmd))
    app.add_handler(CommandHandler("debugfixtures_new", debugfixtures_new))

    # Scan every 60 seconds
    app.job_queue.run_repeating(check_matches, interval=60, first=10)

    # Morning shortlist at 09:00
    app.job_queue.run_daily(morning_shortlist, time=time(9, 0), name="morning_shortlist")

    # Daily ACCA at 09:05
    app.job_queue.run_daily(daily_acca, time=time(9, 5), name="daily_acca")

    app.run_polling()

if __name__ == "__main__":
    main()

