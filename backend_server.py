"""
Pikina OS — Backend API Server (Phase 1.5)
Bridges the Python Phase 1 core to the Electron frontend.
Runs on localhost:5001 only — never exposed externally.
"""
import os
import sys
import json
import requests
from pathlib import Path
from datetime import datetime, timezone

from flask import Flask, jsonify, request
from flask_cors import CORS

# Ensure project root is on the path
sys.path.insert(0, str(Path(__file__).parent))

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from core.eventbus.bus import EventBus
from core.eventbus.replay import ReplayStore
from core.governor.telemetry import get_telemetry
from core.governor.profiles import get_profile, set_profile, PROFILE_WEIGHTS
from core.registry.loader import CapabilityRegistry
from core.router.tier1_win32 import Tier1Router
from core.mcm.orchestrator import Orchestrator
from core.daemons.file_indexer import FileIndexerDaemon
import atexit

# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app, origins=["http://localhost:*", "null", "file://*"])

bus      = EventBus()
registry = CapabilityRegistry()
router   = Tier1Router(registry=registry)
mcm      = Orchestrator(router=router)

OPENWEATHER_KEY  = os.getenv("OPENWEATHER_API_KEY", "")
OPENWEATHER_CITY = os.getenv("OPENWEATHER_CITY", "Mumbai")
PORT             = int(os.getenv("BACKEND_PORT", 5001))

# Start File Indexer Daemon
indexer_daemon = FileIndexerDaemon()
indexer_daemon.start()

def cleanup_daemons():
    indexer_daemon.stop()

atexit.register(cleanup_daemons)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/api/status")
def status():
    return jsonify({
        "status":    "online",
        "version":   "0.1.0-phase1",
        "profile":   get_profile(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })


@app.route("/api/telemetry")
def telemetry():
    return jsonify(get_telemetry())


@app.route("/api/events")
def events():
    since  = int(request.args.get("since", 30))
    topic  = request.args.get("topic") or None
    store  = ReplayStore()
    data   = store.query(since_minutes=since, topic=topic)
    return jsonify({"events": data, "count": len(data)})


@app.route("/api/command", methods=["POST"])
def command():
    body   = request.get_json(force=True, silent=True) or {}
    text   = (body.get("text") or "").strip()
    source = body.get("source", "user_typed")

    if not text:
        return jsonify({"status": "error", "reason": "Empty command"}), 400

    result = mcm.receive(text, source=source)
    return jsonify(result)


@app.route("/api/tools")
def tools():
    return jsonify({"tools": registry.list_tools()})


@app.route("/api/profile", methods=["GET"])
def get_profile_route():
    return jsonify({
        "profile":   get_profile(),
        "available": list(PROFILE_WEIGHTS.keys()),
    })


@app.route("/api/profile", methods=["POST"])
def set_profile_route():
    body    = request.get_json(force=True, silent=True) or {}
    profile = body.get("profile", "productivity")
    try:
        set_profile(profile)
        return jsonify({"status": "ok", "profile": profile})
    except ValueError as exc:
        return jsonify({"status": "error", "reason": str(exc)}), 400


@app.route("/api/weather")
def weather():
    if not OPENWEATHER_KEY:
        return jsonify({
            "error": "OPENWEATHER_API_KEY not configured",
            "hint":  "Add OPENWEATHER_API_KEY=<key> to your .env file",
        }), 503

    city = request.args.get("city", OPENWEATHER_CITY)
    try:
        resp = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": city, "appid": OPENWEATHER_KEY, "units": "metric"},
            timeout=5,
        )
        resp.raise_for_status()
        d = resp.json()
        return jsonify({
            "city":        d["name"],
            "country":     d["sys"]["country"],
            "temp_c":      round(d["main"]["temp"]),
            "feels_like":  round(d["main"]["feels_like"]),
            "humidity":    d["main"]["humidity"],
            "description": d["weather"][0]["description"].title(),
            "icon_code":   d["weather"][0]["icon"],
            "wind_kmh":    round(d["wind"]["speed"] * 3.6),
        })
    except requests.exceptions.Timeout:
        return jsonify({"error": "Weather API timed out"}), 504
    except Exception as exc:
        return jsonify({"error": str(exc)}), 502


@app.route("/api/deadlines", methods=["GET"])
def get_deadlines():
    dl_file = Path(__file__).parent / "data" / "deadlines.json"
    if dl_file.exists():
        return jsonify(json.loads(dl_file.read_text(encoding="utf-8")))
    return jsonify({"deadlines": []})

from core.memory.graph_cache import GraphCache
graph_cache = GraphCache()

@app.route("/api/deadlines", methods=["POST"])
def save_deadlines():
    body    = request.get_json(force=True, silent=True) or {}
    dl_file = Path(__file__).parent / "data" / "deadlines.json"
    dl_file.parent.mkdir(exist_ok=True)
    dl_file.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return jsonify({"status": "ok"})

@app.route("/api/cache/path", methods=["POST"])
def cache_path():
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path")
    is_dir = data.get("is_dir", False)
    if path:
        graph_cache.add_path(path, is_dir)
    return jsonify({"status": "ok"})

@app.route("/api/suggest", methods=["GET"])
def suggest():
    q = request.args.get("q", "").strip().lower()
    if not q:
        return jsonify({"suggestions": []})

    suggestions = []
    
    # Phase 1: Tier 1 commands
    commands = ["open vs code", "open chrome", "open notepad", "open calc", "lock screen", "find file "]
    for cmd in commands:
        if cmd.startswith(q) or q in cmd:
            suggestions.append({"text": cmd, "type": "command"})
            
    # Phase 2: Graph Cache Search
    if "find file" in q or "open" in q or not suggestions:
        file_q = q.replace("find file", "").replace("open", "").strip()
        if file_q:
            cached = graph_cache.search(file_q, limit=5)
            for c in cached:
                cmd_text = f"open {c['basename']}" if c['type'] == 'dir' else f"find file {c['basename']}"
                suggestions.append({"text": cmd_text, "type": c["type"], "desc": c["path"]})
                
    # Phase 3: Fallback deep scan
    if len(suggestions) < 3 and ("find file " in q):
        file_q = q.replace("find file", "").strip()
        if len(file_q) > 2:
            from core.registry.capabilities.find_file import run as find_file_impl
            res = find_file_impl({"name": f"*{file_q}*", "max_depth": 4, "max_results": 3})
            for r in res.get("results", [])[:3]:
                if not any(s.get("desc") == r for s in suggestions):
                    basename = Path(r).name
                    suggestions.append({"text": f"find file {basename}", "type": "file", "desc": r})

    return jsonify({"suggestions": suggestions[:6]})
@app.route("/api/settings", methods=["GET"])
def get_settings():
    st_file = Path(__file__).parent / "data" / "settings.json"
    if st_file.exists():
        return jsonify(json.loads(st_file.read_text(encoding="utf-8")))
    return jsonify({})

@app.route("/api/settings", methods=["POST"])
def save_settings():
    body = request.get_json(force=True, silent=True) or {}
    st_file = Path(__file__).parent / "data" / "settings.json"
    st_file.parent.mkdir(exist_ok=True)
    current = {}
    if st_file.exists():
        try:
            current = json.loads(st_file.read_text(encoding="utf-8"))
        except:
            pass
    current.update(body)
    st_file.write_text(json.dumps(current, indent=2), encoding="utf-8")
        
    return jsonify({"status": "ok", "settings": current})

@app.route("/api/wallpaper/random", methods=["POST"])
def get_random_wallpaper():
    import urllib.request
    try:
        url = "https://wallhaven.cc/api/v1/search?q=anime+modern&purity=100&sorting=random"
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
            if data.get("data"):
                image_url = data["data"][0]["path"]
                st_file = Path(__file__).parent / "data" / "settings.json"
                st_file.parent.mkdir(exist_ok=True)
                current = {}
                if st_file.exists():
                    try:
                        current = json.loads(st_file.read_text(encoding="utf-8"))
                    except:
                        pass
                current["wallpaperUrl"] = image_url
                st_file.write_text(json.dumps(current, indent=2), encoding="utf-8")
                
                return jsonify({"status": "ok", "wallpaperUrl": image_url})
    except Exception as e:
        print("Failed to fetch wallpaper from Wallhaven:", e)
    return jsonify({"error": "Failed to fetch wallpaper"}), 500


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print(f"\n{'='*55}")
    print(f"  Pikina OS Backend  —  http://localhost:{PORT}")
    print(f"  Profile : {get_profile()}")
    print(f"  Weather : {OPENWEATHER_CITY}")
    print(f"{'='*55}\n")
    app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
