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

def fetch_game_data_from_roblox(place_id):
    """Fetch game data using Roblox's proxy server (no auth needed)"""
    
    # Use Roblox's public proxy
    headers = {
        'User-Agent': 'Roblox/WinInet',
        'Referer': 'https://www.roblox.com/'
    }
    
    # Get Universe ID
    try:
        u_res = requests.get(
            f"https://apis.roblox.com/universes/v1/places/{place_id}/universe",
            headers=headers,
            timeout=5
        ).json()
        u_id = u_res.get('universeId')
        
        if not u_id:
            logger.error(f"❌ No universeId for place {place_id}")
            return None
            
    except Exception as e:
        logger.error(f"❌ Universe API error: {e}")
        return None
    
    game_data = {'universeId': u_id}
    
    # Get game details
    try:
        g_res = requests.get(
            f"https://games.roblox.com/v1/games?universeIds={u_id}",
            headers=headers,
            timeout=5
        ).json()
        
        if 'data' in g_res and len(g_res['data']) > 0:
            g_info = g_res['data'][0]
            game_data.update({
                'name': g_info.get('name', 'Unknown'),
                'description': g_info.get('description', ''),
                'playing': g_info.get('playing', 0),
                'visits': g_info.get('visits', 0),
                'favorites': g_info.get('favoritedCount', 0),
                'updated': g_info.get('updated', 'Unknown')
            })
    except Exception as e:
        logger.warning(f"⚠️ Game details error: {e}")
    
    # Get votes
    try:
        v_res = requests.get(
            f"https://games.roblox.com/v1/games/votes?universeIds={u_id}",
            headers=headers,
            timeout=5
        ).json()
        
        if 'data' in v_res and len(v_res['data']) > 0:
            game_data['upvotes'] = v_res['data'][0].get('upVotes', 0)
    except:
        game_data['upvotes'] = 0
    
    # Get icon
    try:
        icon_res = requests.get(
            f"https://thumbnails.roblox.com/v1/games/icons?universeIds={u_id}&size=256x256&format=Png&isCircular=false",
            headers=headers,
            timeout=5
        ).json()
        
        if 'data' in icon_res and len(icon_res['data']) > 0:
            game_data['icon'] = icon_res['data'][0]['imageUrl']
    except:
        game_data['icon'] = 'https://via.placeholder.com/256'
    
    # Get thumbnail
    try:
        thumb_res = requests.get(
            f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails?universeIds={u_id}&size=768x432&format=Png",
            headers=headers,
            timeout=5
        ).json()
        
        if 'data' in thumb_res and len(thumb_res['data']) > 0:
            thumbs = thumb_res['data'][0].get('thumbnails', [])
            if thumbs:
                game_data['thumbnail'] = thumbs[0]['imageUrl']
    except:
        game_data['thumbnail'] = 'https://via.placeholder.com/768x432'
    
    return game_data

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    provided_hash = request.headers.get('X-Luminar-Auth')
    
    place_id = data.get('placeId', 'Unknown')
    job_id = data.get('jobId', 'Unknown')
    player_count = data.get('playerCount', 0)
    max_players = data.get('maxPlayers', 0)
    
    logger.info(f"📥 Request | Place: {place_id}")

    if not verify_luminar_security(provided_hash):
        logger.error("❌ Unauthorized")
        return jsonify({"error": "Unauthorized"}), 401

    try:
        server_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0]
        
        # Fetch all game data from Roblox APIs
        game_data = fetch_game_data_from_roblox(place_id)
        
        if not game_data:
            logger.error(f"❌ Could not fetch data for {place_id}")
            return jsonify({"status": "skipped"}), 200
        
        game_name = game_data.get('name', 'Unknown')
        game_desc = game_data.get('description', '')
        active = game_data.get('playing', 0)
        visits = game_data.get('visits', 0)
        favorites = game_data.get('favorites', 0)
        upvotes = game_data.get('upvotes', 0)
        updated = game_data.get('updated', 'Unknown')
        icon = game_data.get('icon', 'https://via.placeholder.com/256')
        thumb = game_data.get('thumbnail', 'https://via.placeholder.com/768x432')
        
        logger.info(f"✅ {game_name} | {active} active players")
        
        # Tiered webhook routing
        if active >= 500: target = WEBHOOKS["tier4"]
        elif active >= 100: target = WEBHOOKS["tier3"]
        elif active >= 50: target = WEBHOOKS["tier2"]
        else: target = WEBHOOKS["tier1"]

        if not target:
            logger.error("❌ No webhook configured")
            return jsonify({"error": "Webhook not configured"}), 500

        # Build Discord embed
        payload = {
            "embeds": [{
                "author": {"name": "Luminar Project | Intelligence", "icon_url": icon},
                "title": f"🚀 Premium Server Log: {game_name}",
                "url": f"https://www.roblox.com/games/{place_id}",
                "description": game_desc[:200] + "..." if len(game_desc) > 200 else game_desc,
                "color": 0xAC00FF,
                "image": {"url": thumb},
                "thumbnail": {"url": icon},
                "fields": [
                    {"name": "🌐 Server Location", "value": f"**IP:** `{server_ip}`\n{get_location(server_ip)}", "inline": False},
                    {"name": "👥 Population", "value": f"**Total Active:** {active:,}\n**Current Server:** {player_count}/{max_players}", "inline": True},
                    {"name": "📊 Statistics", "value": f"**Visits:** {visits:,}\n**Favorites:** {favorites:,}\n**Upvotes:** {upvotes:,}", "inline": True},
                    {"name": "💻 Executor Join", "value": f"```js\nRoblox.GameLauncher.joinGameInstance({place_id}, '{job_id}');\n```", "inline": False}
                ],
                "footer": {"text": f"Last Updated: {updated[:10]} • JobID: {job_id}"}
            }]
        }

        res = rate_limited_webhook(target, payload)
        
        if res.status_code in [200, 204]:
            logger.info(f"✅ Webhook sent successfully")
        elif res.status_code == 429:
            logger.warning(f"⚠️ Rate limited (429)")
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
