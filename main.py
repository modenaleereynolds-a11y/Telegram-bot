# ---------------------------------
# LIVE MATCH FUNCTIONS (UPDATED WITH FEED ROTATION)
# ---------------------------------

ACTIVE_FEED = None  # shows which feed is currently working

async def get_live_matches():
    global ACTIVE_FEED

    FEEDS = [
        "f_1_0_3_en_1",
        "f_1_0_2_en_1",
        "f_1_0_1_en_1",
        "f_1_0_4_en_1",
        "f_1_0__en_1"
    ]

    for feed in FEEDS:
        url = f"https://d.flashscore.com/x/feed/{feed}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as response:
                    text = await response.text()
                    cleaned = text.replace("])}while(1);</x>", "")

                    try:
                        data = json.loads(cleaned)
                    except:
                        continue  # try next feed

                    match_ids = []

                    for item in data:
                        if item[0] == "event":
                            match_ids.append(item[1])

                    # If this feed returned matches, lock onto it
                    if match_ids:
                        ACTIVE_FEED = feed
                        return match_ids

        except Exception:
            continue  # try next feed

    # If no feed worked
    ACTIVE_FEED = "None working"
    return []
