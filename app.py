import os, hashlib, time, requests, logging
from flask import Flask, request, jsonify
from collections import deque
from threading import Lock

app = Flask(__name__)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LuminarProject")

AUTH_SECRET = os.environ.get('AUTH_SECRET')
WEBHOOKS = {
    "tier1": os.environ.get('WEBHOOK_0_50'),
    "tier2": os.environ.get('WEBHOOK_50_100'),
    "tier3": os.environ.get('WEBHOOK_100_500'),
    "tier4": os.environ.get('WEBHOOK_INFINITY')
}

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
        universe_res.raise_for_status()
        universe_id = universe_res.json().get('universeId')
        
        if not universe_id:
            logger.error(f"❌ No universeId for {place_id}")
            return jsonify({"status": "skipped"}), 200
        
        # Step 2: Get game details
        game_res = requests.get(f"https://games.roblox.com/v1/games?universeIds={universe_id}", timeout=5)
        game_res.raise_for_status()
        game_data = game_res.json()['data'][0]
        
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
            votes_res.raise_for_status()
            upvotes = votes_res.json()['data'][0].get('upVotes', 0)
        except:
            pass
        
        # Step 4: Get thumbnail
        thumbnail_url = "https://via.placeholder.com/768x432"
        try:
            thumb_res = requests.get(f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails?universeIds={universe_id}&countPerUniverse=1&defaults=true&size=768x432&format=Png&isCircular=false", timeout=5)
            thumb_res.raise_for_status()
            thumbnail_url = thumb_res.json()['data'][0]['thumbnails'][0]['imageUrl']
        except:
            pass
        
        # Step 5: Get icon
        icon_url = "https://via.placeholder.com/256"
        try:
            icon_res = requests.get(f"https://thumbnails.roblox.com/v1/games/icons?universeIds={universe_id}&size=256x256&format=Png&isCircular=false", timeout=5)
            icon_res.raise_for_status()
            icon_url = icon_res.json()['data'][0]['imageUrl']
        except:
            pass
        
        # Tiered webhook
        if playing >= 500: target = WEBHOOKS["tier4"]
        elif playing >= 100: target = WEBHOOKS["tier3"]
        elif playing >= 50: target = WEBHOOKS["tier2"]
        else: target = WEBHOOKS["tier1"]

        if not target:
            return jsonify({"error": "No webhook configured"}), 500

        # Build embed
        js_code = f"```js\nRoblox.GameLauncher.joinGameInstance({place_id}, \"{job_id}\");\n```"
        
        desc_text = game_desc[:200] + "..." if len(game_desc) > 200 else game_desc
        
        payload = {
            "embeds": [{
                "author": {
                    "name": "Luminar Project | Intelligence",
                    "icon_url": icon_url
                },
                "title": f"🚀 Premium Server Log: {game_name}",
                "url": f"https://www.roblox.com/games/{place_id}",
                "description": desc_text,
                "color": 0xAC00FF,
                "thumbnail": {"url": icon_url},
                "image": {"url": thumbnail_url},
                "fields": [
                    {"name": "🌐 Server Location", "value": f"**IP:** `{server_ip}`\n{get_location(server_ip)}", "inline": False},
                    {"name": "👥 Population", "value": f"**Total Active:** {format_number(playing)}\n**Current Server:** {player_count}/{max_players}", "inline": True},
                    {"name": "📊 Statistics", "value": f"**Visits:** {format_number(visits)}\n**Favorites:** {format_number(favorites)}\n**Upvotes:** {format_number(upvotes)}", "inline": True},
                    {"name": "💻 Executor Join", "value": js_code, "inline": False}
                ],
                "footer": {"text": f"Last Updated: {updated[:10]} • JobID: {job_id}"}
            }]
        }

        res = rate_limited_webhook(target, payload)
        
        if res.status_code in [200, 204]:
            logger.info(f"✅ Sent to Discord")
        elif res.status_code == 429:
            logger.warning(f"⚠️ Rate limited")
        else:
            logger.error(f"❌ Failed: {res.status_code}")
        
        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
