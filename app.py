import os, hashlib, time, requests, logging
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- CONFIGURE LOGGING ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LuminarProject")

# --- LUMINAR CONFIGURATION ---
AUTH_SECRET = os.environ.get('AUTH_SECRET')
WEBHOOKS = {
    "tier1": os.environ.get('WEBHOOK_0_50'),
    "tier2": os.environ.get('WEBHOOK_50_100'),
    "tier3": os.environ.get('WEBHOOK_100_500'),
    "tier4": os.environ.get('WEBHOOK_INFINITY')
}

# Check if Env Vars are loaded correctly on startup
if not AUTH_SECRET:
    logger.error("❌ CRITICAL: AUTH_SECRET is not set in Render Environment Variables!")

def get_location(ip):
    try:
        res = requests.get(f"http://ip-api.com/json/{ip}").json()
        if res['status'] == 'success':
            return f"📍 {res['city']}, {res['country']} ({res['isp']})"
    except: pass
    return "Unknown Location"

def verify_luminar_security(provided_hash):
    """Checks the hash and logs the comparison for debugging."""
    if not provided_hash:
        logger.warning("⚠️ No hash provided in request headers (X-Luminar-Auth is missing)")
        return False
        
    current_min = time.gmtime().tm_min
    # Checking current minute and 2 minutes prior
    minutes_to_check = [current_min, (current_min - 1) % 60, (current_min - 2) % 60]
    
    logger.info(f"--- Hash Verification Debug ---")
    logger.info(f"Received Hash: {provided_hash}")
    
    for m in minutes_to_check:
        raw_string = f"{AUTH_SECRET}:{m}"
        expected = hashlib.sha256(raw_string.encode()).hexdigest()
        logger.info(f"Minute {m} | Expected: {expected}")
        
        if provided_hash == expected:
            logger.info(f"✅ Hash Match Found for minute {m}!")
            return True
            
    logger.error("❌ Hash Mismatch! None of the calculated hashes matched the provided hash.")
    return False

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    provided_hash = request.headers.get('X-Luminar-Auth')
    
    # Log the basic request info
    place_id = data.get('placeId', 'Unknown')
    job_id = data.get('jobId', 'Studio/No-Job-Id')
    logger.info(f"Incoming Request | Place: {place_id} | JobId: {job_id}")

    # 1. Security Check with Logging
    if not verify_luminar_security(provided_hash):
        return jsonify({"error": "Unauthorized Security Breach", "debug": "Check server logs for hash comparison"}), 401

    try:
        server_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0]
        
        # 2. Fetch Deep Info
        u_req = requests.get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe").json()
        u_id = u_req.get('universeId')
        
        g_info = requests.get(f"https://games.roblox.com/v1/games?universeIds={u_id}").json()['data'][0]
        v_info = requests.get(f"https://games.roblox.com/v1/games/votes?universeIds={u_id}").json()['data'][0]
        icon = requests.get(f"https://thumbnails.roblox.com/v1/games/icons?universeIds={u_id}&size=256x256&format=Png&isCircular=false").json()['data'][0]['imageUrl']
        thumb_data = requests.get(f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails?universeIds={u_id}&size=768x432&format=Png").json()
        thumb = thumb_data['data'][0]['thumbnails'][0]['imageUrl']

        # 3. Tiered Webhook Routing
        active = g_info.get('playing', 0)
        if active >= 500: target = WEBHOOKS["tier4"]
        elif active >= 100: target = WEBHOOKS["tier3"]
        elif active >= 50: target = WEBHOOKS["tier2"]
        else: target = WEBHOOKS["tier1"]

        if not target: 
            logger.error(f"❌ No Webhook URL found for active player count: {active}")
            return jsonify({"error": "Webhook not set for this tier"}), 500

        # 4. Construct Luminar Embed
        payload = {
            "embeds": [{
                "author": {"name": "Luminar Project | Intelligence", "icon_url": icon},
                "title": f"🚀 Premium Server Log: {g_info['name']}",
                "url": f"https://www.roblox.com/games/{place_id}",
                "color": 0xAC00FF,
                "image": {"url": thumb},
                "thumbnail": {"url": icon},
                "fields": [
                    {"name": "🌐 Server Location", "value": f"**IP:** `{server_ip}`\n{get_location(server_ip)}", "inline": False},
                    {"name": "👥 Population", "value": f"**Total Active:** {active:,}\n**Current Server:** {data['playerCount']}/{data['maxPlayers']}", "inline": True},
                    {"name": "📊 Stats", "value": f"👍 {v_info['upVotes']:,} | ⭐ {g_info['favoritedCount']:,}", "inline": True},
                    {"name": "💻 Executor Join", "value": f"```js\nRoblox.GameLauncher.joinGameInstance({place_id}, '{job_id}');\n```", "inline": False}
                ],
                "footer": {"text": f"Luminar Security Active • JobID: {job_id}"}
            }]
        }

        res = requests.post(target, json=payload)
        logger.info(f"✅ Webhook sent to Discord. Status: {res.status_code}")
        return jsonify({"status": "Luminar Log Dispatched"}), 200

    except Exception as e:
        logger.error(f"❌ Error processing webhook: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
