"""
Pikina OS — Backend API Server (Phase 1.5)
Bridges the Python Phase 1 core to the Electron frontend.
Runs on localhost:5001 only — never exposed externally.
"""
import socket
import sys

# Prevent multiple backend server instances from running
try:
    _instance_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    _instance_lock.bind(("127.0.0.1", 5052))
except OSError:
    print("[Backend] Another instance is already running. Exiting immediately.")
    sys.exit(0)

import os
import sys
import json
import secrets
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
from core.router.tier2_ollama import Tier2Router
from core.mcm.orchestrator import Orchestrator
from core.mcm import killswitch
from core.mcm.provenance import TRUSTED
from core.registry.executor import execute_action
from core.daemons.file_indexer import FileIndexerDaemon
from core.daemons.clipboard import ClipboardDaemon
from core.daemons.file_watcher import FileWatcherDaemon
from core.memory.trust_engine import TrustEngine
from core.memory.forgetting_engine import ForgettingEngine
import atexit

# ---------------------------------------------------------------------------
# App init
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app, origins=["http://localhost:*", "null", "file://*"])

PROJECT_ROOT = Path(__file__).parent


def _load_api_token() -> str:
    """
    Shared secret between this server and the Electron app (which reads the same
    file and attaches it to every request). Without it, any web page open in a
    browser could POST to localhost:5001 and run capabilities.
    """
    token_file = Path(os.getenv("PIKINA_API_TOKEN_FILE") or PROJECT_ROOT / "data" / ".api_token")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    if token_file.exists():
        token = token_file.read_text(encoding="utf-8").strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    token_file.write_text(token, encoding="utf-8")
    try:
        os.chmod(token_file, 0o600)
    except OSError:
        pass
    return token


API_TOKEN = _load_api_token()


@app.before_request
def _require_token():
    if request.method == "OPTIONS" or not request.path.startswith("/api/"):
        return None
    supplied = request.headers.get("X-Pikina-Token", "")
    if not secrets.compare_digest(supplied, API_TOKEN):
        return jsonify({"status": "error", "reason": "Missing or invalid X-Pikina-Token."}), 401
    return None


bus      = EventBus()
registry = CapabilityRegistry()
router   = Tier1Router(registry=registry)

# Tier 2 (local Ollama) handles anything Tier 1 regexes don't match.
# Set PIKINA_TIER2=0 in .env to run fully deterministic.
TIER2_ENABLED = os.getenv("PIKINA_TIER2", "1") != "0"
tier2 = Tier2Router(registry=registry) if TIER2_ENABLED else None
mcm   = Orchestrator(router=router, tier2_router=tier2)
if tier2:
    tier2.warm_up_async()

OPENWEATHER_KEY  = os.getenv("OPENWEATHER_API_KEY", "")
OPENWEATHER_CITY = os.getenv("OPENWEATHER_CITY", "Mumbai")
PORT             = int(os.getenv("BACKEND_PORT", 5001))

# Start Background Daemons (set PIKINA_NO_DAEMONS=1 to skip, e.g. for tests or headless use)
DAEMONS_ENABLED = os.getenv("PIKINA_NO_DAEMONS", "0") != "1"

forgetting_engine = ForgettingEngine(sweep_interval_sec=3600, max_age_hours=24, promotion_threshold=3)
if DAEMONS_ENABLED:
    forgetting_engine.start()

indexer_daemon   = FileIndexerDaemon()
clipboard_daemon = ClipboardDaemon(bus=bus)
watcher_daemon   = FileWatcherDaemon(bus=bus)
if DAEMONS_ENABLED:
    print("[Backend] Initializing Sensory Daemons...")
    indexer_daemon.start()
    clipboard_daemon.start()
    watcher_daemon.start()

trust_engine = None

def _bg_init():
    global trust_engine
    print("[Backend] Initializing Memory Systems (TrustEngine) in background...")
    try:
        # Pre-loading PyTorch/SentenceTransformers in a non-daemon background thread
        # prevents startup blocking of the HTTP server.
        trust_engine = TrustEngine(bus=bus)
        print("[Backend] TrustEngine loaded and active.")
    except Exception as e:
        print(f"[Backend] Failed to initialize TrustEngine: {e}")

    try:
        from core.calendar.holiday_sync import sync_year
        from datetime import datetime
        sync_res = sync_year(datetime.now().year)
        print(f"[Backend] Public holidays synced: {sync_res}")
    except Exception as e:
        print(f"[Backend] Failed to sync public holidays: {e}")

import threading
if DAEMONS_ENABLED:
    threading.Thread(target=_bg_init, daemon=False).start()

_daemons_stopped = False


def cleanup_daemons():
    global _daemons_stopped
    if _daemons_stopped:
        return
    _daemons_stopped = True
    print("\n[Backend] Shutting down daemons...")
    for daemon in (indexer_daemon, clipboard_daemon, watcher_daemon, forgetting_engine):
        try:
            daemon.stop()
        except Exception as exc:
            print(f"[Backend] Failed to stop {type(daemon).__name__}: {exc}")

atexit.register(cleanup_daemons)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/api/status")
def status():
    return jsonify({
        "status":    "online",
        "version":   "0.4.0",
        "profile":   get_profile(),
        "tier2":     {"enabled": TIER2_ENABLED, "model": tier2.model_name if tier2 else None},
        "kill_switch": killswitch.is_engaged(),
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


@app.route("/api/capability", methods=["POST"])
def execute_capability():
    body   = request.get_json(force=True, silent=True) or {}
    tool   = body.get("tool")
    params = body.get("params", {})

    if not tool:
        return jsonify({"status": "error", "reason": "Missing tool parameter."}), 400

    # Same kill switch, consent gate and audit trail as typed commands.
    result = execute_action(registry, tool, params, raw=f"[api] {tool}", tier="api", provenance=TRUSTED)
    return jsonify(result)


@app.route("/api/killswitch", methods=["POST"])
def kill_switch():
    """Halt every capability and stop the sensory daemons. Restart the backend to resume."""
    killswitch.engage()
    cleanup_daemons()
    bus.publish(topic="system.kill_switch", payload={"engaged": True},
                provenance=TRUSTED, permission_level=4)
    return jsonify({"status": "ok", "kill_switch": True,
                    "message": "All actions halted and daemons stopped. Restart Pikina to resume."})


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


from core.todo.todo_store import ToDoStore
todo_store = ToDoStore()

@app.route("/api/deadlines", methods=["GET"])
def get_deadlines():
    todos = todo_store.list_todos(status="pending")
    deadlines = []
    for t in todos:
        priority = "low"
        if t.get("bucket") == "today":
            priority = "high"
        elif t.get("bucket") == "tomorrow":
            priority = "medium"
        deadlines.append({
            "id": t["id"],
            "title": t["text"],
            "due": t.get("due_date") or "No due date",
            "priority": priority
        })
    return jsonify({"deadlines": deadlines})

@app.route("/api/deadlines", methods=["POST"])
def save_deadlines():
    # Deprecated/NOP since we now use proper todo capabilities to write to SQLite
    return jsonify({"status": "ok", "message": "Deprecated. Use todo.add capability."})

@app.route("/api/calendar", methods=["GET"])
def get_calendar_events():
    start_str = request.args.get("start")
    end_str = request.args.get("end")
    
    if not start_str or not end_str:
        from datetime import date, timedelta
        start_date = date.today().isoformat()
        end_date = (date.today() + timedelta(days=30)).isoformat()
    else:
        start_date = start_str.split("T")[0]
        end_date = end_str.split("T")[0]

    from core.calendar.calendar_store import CalendarStore
    store = CalendarStore()
    events = store.query_range(start_date, end_date)
    
    fc_events = []
    for ev in events:
        start_val = ev["date"]
        if ev.get("time"):
            start_val += f"T{ev['time']}:00"
            
        fc_events.append({
            "id": ev["id"],
            "title": ev["title"],
            "start": start_val,
            "allDay": not ev.get("time"),
            "extendedProps": {
                "type": ev["type"],
                "source": ev["source"],
                "recurring": ev["recurring"],
                "description": ev.get("description") or ""
            }
        })
    return jsonify(fc_events)

from core.memory.graph_cache import GraphCache
graph_cache = GraphCache()

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
