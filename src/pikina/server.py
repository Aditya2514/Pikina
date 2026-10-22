"""
Pikina OS — backend HTTP API for the desktop app.

Run with `pikina-server` (or `python -m pikina.server`). It listens on
127.0.0.1 only and every /api/* request must carry the X-Pikina-Token header
(the token lives in the data folder; the desktop app attaches it).

Importing this module has no side effects: create_app() builds the Flask app,
and main() is what binds the port and starts the background daemons.
"""
import atexit
import json
import os
import secrets
import socket
import sqlite3
import sys
import threading
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

from pikina import __version__, paths, settings
from pikina.eventbus.bus import EventBus
from pikina.eventbus.replay import ReplayStore
from pikina.governor.profiles import PROFILE_WEIGHTS, get_profile, set_profile
from pikina.governor.telemetry import get_telemetry
from pikina.mcm import killswitch
from pikina.mcm.orchestrator import Orchestrator
from pikina.mcm.provenance import TRUSTED
from pikina.memory.graph_cache import GraphCache
from pikina.registry.executor import execute_action
from pikina.registry.loader import CapabilityRegistry
from pikina.router.tier1 import Tier1Router
from pikina.router.tier2_ollama import Tier2Router

DEFAULT_PORT = 5001
_INSTANCE_LOCK_PORT = 5052


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def load_env() -> None:
    """Read .env from the launch folder (development) and from the data folder (installed use)."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()  # ./.env
    load_dotenv(paths.data_file(paths.ENV_FILE))


def load_api_token() -> str:
    """
    Shared secret between this server and the desktop app, which reads the same
    file and attaches it to every request. Without it, any web page open in a
    browser could POST to localhost and run capabilities.
    """
    token_file = Path(os.getenv("PIKINA_API_TOKEN_FILE") or paths.data_file(paths.API_TOKEN))
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


def _read_settings() -> dict:
    return settings.read()


def _update_settings(changes: dict) -> dict:
    return settings.update(changes)


# settings.json keys that control the activity log; changed only through
# Backend.apply_activity_settings so the tracker starts and stops with them.
_ACTIVITY_KEYS = {"logging": "activity_logging", "paused": "activity_paused", "window_tracking": "window_tracking"}


# ---------------------------------------------------------------------------
# Runtime services
# ---------------------------------------------------------------------------

class Backend:
    """Everything the HTTP routes need: routers, registry and background daemons."""

    def __init__(self, enable_tier2: bool = True):
        self.bus = EventBus()
        self.registry = CapabilityRegistry()
        self.router = Tier1Router(registry=self.registry)
        self.tier2 = Tier2Router(registry=self.registry) if enable_tier2 else None
        self.mcm = Orchestrator(router=self.router, tier2_router=self.tier2)
        self.graph_cache = GraphCache()
        self.trust_engine = None
        self._daemons = []
        self._stopped = False
        self._daemons_started = False
        self.tracker = None
        self._tracker_lock = threading.RLock()

    def start_daemons(self) -> None:
        from pikina.daemons.clipboard import ClipboardDaemon
        from pikina.daemons.file_indexer import FileIndexerDaemon
        from pikina.daemons.file_watcher import FileWatcherDaemon
        from pikina.memory.forgetting_engine import ForgettingEngine

        print("[Backend] Starting background daemons...")
        self._daemons = [
            ForgettingEngine(sweep_interval_sec=3600, max_age_hours=24, promotion_threshold=3),
            FileIndexerDaemon(),
            ClipboardDaemon(bus=self.bus),
            FileWatcherDaemon(bus=self.bus),
        ]
        for daemon in self._daemons:
            daemon.start()
        self._daemons_started = True
        if settings.get("window_tracking", False) is True:
            self.set_window_tracking(True)
        threading.Thread(target=self._slow_init, name="BackendInit", daemon=True).start()

    def _slow_init(self) -> None:
        """Model loading and network sync, kept off the startup path."""
        try:
            from pikina.activity.backfill import backfill, scrub_replay_log
            from pikina.activity.store import ActivityStore
            store = ActivityStore()
            store.prune()
            if not store.meta("replay_scrubbed"):
                scrub_replay_log()
                store.set_meta("replay_scrubbed", "1")
            done = backfill()
            if done.get("added"):
                print(f"[Backend] Activity log: {done['added']} past actions imported from the replay log.")
        except Exception as exc:
            print(f"[Backend] Activity backfill skipped: {type(exc).__name__}")
        try:
            from pikina.memory.trust_engine import TrustEngine
            self.trust_engine = TrustEngine(bus=self.bus)
            print("[Backend] Memory (TrustEngine) active.")
        except Exception as exc:
            print(f"[Backend] Memory disabled: {exc}")
        try:
            from pikina.calendar.holiday_sync import sync_year
            print(f"[Backend] Public holidays synced: {sync_year(datetime.now().year)}")
        except Exception as exc:
            print(f"[Backend] Holiday sync skipped: {exc}")

    def stop_daemons(self) -> None:
        with self._tracker_lock:
            if self._stopped:
                return
            self._stopped = True
        self._stop_tracker()
        for daemon in self._daemons:
            try:
                daemon.stop()
            except Exception as exc:
                print(f"[Backend] Failed to stop {type(daemon).__name__}: {exc}")

    # -- activity log and window tracker ---------------------------------------
    # _tracker_lock (re-entrant) covers: starting/stopping the tracker, the kill switch
    # and stop checks, and saving the activity switches, so overlapping requests can't
    # leave a tracker running that the saved settings or the kill switch say is off.

    def _stop_tracker(self) -> None:
        with self._tracker_lock:
            tracker, self.tracker = self.tracker, None
            if tracker is not None:
                try:
                    tracker.stop()
                except Exception as exc:
                    print(f"[Backend] Failed to stop the window tracker: {exc}")

    def set_window_tracking(self, on: bool) -> None:
        """Start or stop the tracker now. It only runs inside a backend that runs daemons."""
        with self._tracker_lock:
            if not on or killswitch.is_engaged() or self._stopped or not self._daemons_started:
                self._stop_tracker()
                return
            if self.tracker is not None:
                if self.tracker.running:
                    return
                self._stop_tracker()  # a dead or failed tracker: clean it up before replacing it
            from pikina.activity.tracker import WindowTracker
            tracker = WindowTracker()
            tracker.start()  # on failure it keeps .reason for the HUD
            self.tracker = tracker

    def activity_status(self) -> dict:
        from pikina.activity import recorder
        data, ok = settings.read_checked()
        flags = recorder.flags()
        tracking = ok and data.get("window_tracking", False) is True
        if self.tracker is not None:
            tracker = self.tracker.status()
        elif not tracking:
            tracker = {"running": False, "backend": None, "reason": None}
        elif killswitch.is_engaged():
            tracker = {"running": False, "backend": None, "reason": "Kill switch is engaged."}
        elif not self._daemons_started:
            tracker = {"running": False, "backend": None,
                       "reason": "Background services are off (PIKINA_NO_DAEMONS); restart Pikina normally."}
        else:
            tracker = {"running": False, "backend": None, "reason": "Not started."}
        return {"logging": flags["logging"], "paused": flags["paused"], "window_tracking": tracking,
                "tracker": tracker,
                "settings_error": None if ok else
                "settings.json can't be read, so recording is paused until you fix or delete it."}

    def apply_activity_settings(self, changes: dict) -> dict:
        """Save activity switches ({logging, paused, window_tracking}: bool) and apply them now."""
        from pikina.activity.store import ActivityStore
        clean = {}
        for key, setting in _ACTIVITY_KEYS.items():
            if key in changes:
                if not isinstance(changes[key], bool):
                    raise ValueError(f"'{key}' must be true or false")
                clean[setting] = changes[key]
        from pikina.activity import recorder
        with self._tracker_lock:
            if clean.get("window_tracking") and killswitch.is_engaged():
                raise KillSwitchEngaged("Kill switch is engaged — restart Pikina to turn tracking on.")
            if clean.get("window_tracking") is False:
                self._stop_tracker()  # turning off works even if saving fails below
            if clean:
                try:
                    settings.update(clean)
                except Exception:
                    if clean.get("activity_paused") is True:
                        recorder.set_runtime_pause(True)  # a pause holds even if it couldn't be saved
                    raise
                if "activity_paused" in clean:
                    recorder.set_runtime_pause(False)    # the saved setting is in charge again
            if clean.get("window_tracking"):
                self.set_window_tracking(True)
        try:  # mark when live recording began (best effort: a DB problem must not block a switch)
            ActivityStore(timeout=0.5)
        except (sqlite3.Error, OSError):
            pass
        return self.activity_status()


class KillSwitchEngaged(Exception):
    pass


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(start_daemons: bool = False, enable_tier2: bool | None = None,
               api_token: str | None = None) -> Flask:
    if enable_tier2 is None:
        enable_tier2 = os.getenv("PIKINA_TIER2", "1") != "0"

    backend = Backend(enable_tier2=enable_tier2)
    token = api_token or load_api_token()

    app = Flask(__name__)
    CORS(app, origins=["http://localhost:*", "null", "file://*"])
    app.extensions["pikina"] = backend
    app.config["PIKINA_API_TOKEN"] = token

    @app.before_request
    def _require_token():
        if request.method == "OPTIONS" or not request.path.startswith("/api/"):
            return None
        if not secrets.compare_digest(request.headers.get("X-Pikina-Token", ""), token):
            return jsonify({"status": "error", "reason": "Missing or invalid X-Pikina-Token."}), 401
        return None

    # -- System ---------------------------------------------------------------

    @app.get("/api/status")
    def status():
        return jsonify({
            "status":      "online",
            "version":     __version__,
            "profile":     get_profile(),
            "tier2":       {"enabled": backend.tier2 is not None,
                            "model": backend.tier2.model_name if backend.tier2 else None},
            "kill_switch": killswitch.is_engaged(),
            "activity":    backend.activity_status(),
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        })

    @app.get("/api/telemetry")
    def telemetry():
        return jsonify(get_telemetry())

    @app.get("/api/events")
    def events():
        since = int(request.args.get("since", 30))
        topic = request.args.get("topic") or None
        data = ReplayStore().query(since_minutes=since, topic=topic)
        return jsonify({"events": data, "count": len(data)})

    @app.get("/api/profile")
    def get_profile_route():
        return jsonify({"profile": get_profile(), "available": list(PROFILE_WEIGHTS.keys())})

    @app.post("/api/profile")
    def set_profile_route():
        profile = (request.get_json(force=True, silent=True) or {}).get("profile", "productivity")
        try:
            set_profile(profile)
            return jsonify({"status": "ok", "profile": profile})
        except ValueError as exc:
            return jsonify({"status": "error", "reason": str(exc)}), 400

    @app.post("/api/killswitch")
    def kill_switch():
        """Halt every capability and stop the daemons. Restart the backend to resume."""
        killswitch.engage()
        backend.stop_daemons()
        backend.bus.publish(topic="system.kill_switch", payload={"engaged": True},
                            provenance=TRUSTED, permission_level=4)
        return jsonify({"status": "ok", "kill_switch": True,
                        "message": "All actions halted and daemons stopped. Restart Pikina to resume."})

    # -- Commands & capabilities ---------------------------------------------

    @app.post("/api/command")
    def command():
        body = request.get_json(force=True, silent=True) or {}
        text = (body.get("text") or "").strip()
        if not text:
            return jsonify({"status": "error", "reason": "Empty command"}), 400
        # source decides trust and must stay "user_typed"; origin only labels the surface
        # (dashboard or quick panel) for the activity log.
        from pikina.activity.recorder import REQUEST_ORIGINS
        origin = body.get("origin")
        origin = origin if isinstance(origin, str) and origin in REQUEST_ORIGINS else "typed"
        return jsonify(backend.mcm.receive(text, source=body.get("source", "user_typed"), origin=origin))

    @app.post("/api/capability")
    def execute_capability():
        body = request.get_json(force=True, silent=True) or {}
        tool = body.get("tool")
        if not tool:
            return jsonify({"status": "error", "reason": "Missing tool parameter."}), 400
        # Same kill switch, consent gate and audit trail as typed commands.
        return jsonify(execute_action(backend.registry, tool, body.get("params", {}),
                                      raw=f"[api] {tool}", tier="api", provenance=TRUSTED, origin="ui"))

    @app.get("/api/tools")
    def tools():
        return jsonify({"tools": backend.registry.list_tools()})

    @app.get("/api/plugins")
    def plugins():
        from pikina import plugins as plugin_mod
        reg = backend.registry
        return jsonify({
            "loaded": [dict(p.to_dict(), tools=[t["tool"] for t in reg.list_tools() if t["source"] == p.name])
                       for p in reg.plugins],
            "disabled": reg.disabled_plugins,
            "errors": reg.plugin_errors,
            "user_folder": str(plugin_mod.user_plugins_dir()),
        })

    @app.get("/api/suggest")
    def suggest():
        q = request.args.get("q", "").strip().lower()
        if not q:
            return jsonify({"suggestions": []})

        suggestions = []
        for cmd in ["open vs code", "open chrome", "open notepad", "open calc", "lock screen", "find file "]:
            if cmd.startswith(q) or q in cmd:
                suggestions.append({"text": cmd, "type": "command"})

        if "find file" in q or "open" in q or not suggestions:
            file_q = q.replace("find file", "").replace("open", "").strip()
            if file_q:
                for c in backend.graph_cache.search(file_q, limit=5):
                    text = f"open {c['basename']}" if c["type"] == "dir" else f"find file {c['basename']}"
                    suggestions.append({"text": text, "type": c["type"], "desc": c["path"]})

        if len(suggestions) < 3 and "find file " in q:
            file_q = q.replace("find file", "").strip()
            if len(file_q) > 2:
                found = backend.registry.get_handler("fs.find_file").run(
                    {"name": f"*{file_q}*", "max_depth": 4, "max_results": 3})
                for r in found.get("results", [])[:3]:
                    if not any(s.get("desc") == r for s in suggestions):
                        suggestions.append({"text": f"find file {Path(r).name}", "type": "file", "desc": r})

        return jsonify({"suggestions": suggestions[:6]})

    @app.post("/api/cache/path")
    def cache_path():
        data = request.get_json(force=True, silent=True) or {}
        if data.get("path"):
            backend.graph_cache.add_path(data["path"], data.get("is_dir", False))
        return jsonify({"status": "ok"})

    # -- Tasks & calendar ----------------------------------------------------

    @app.get("/api/deadlines")
    def get_deadlines():
        from pikina.todo.todo_store import ToDoStore
        priority = {"today": "high", "tomorrow": "medium"}
        return jsonify({"deadlines": [
            {"id": t["id"], "title": t["text"], "due": t.get("due_date") or "No due date",
             "priority": priority.get(t.get("bucket"), "low")}
            for t in ToDoStore().list_todos(status="pending")
        ]})

    @app.get("/api/calendar")
    def get_calendar_events():
        from pikina.calendar.calendar_store import CalendarStore
        start, end = request.args.get("start"), request.args.get("end")
        if start and end:
            start_date, end_date = start.split("T")[0], end.split("T")[0]
        else:
            start_date = date.today().isoformat()
            end_date = (date.today() + timedelta(days=30)).isoformat()

        out = []
        for ev in CalendarStore().query_range(start_date, end_date):
            out.append({
                "id": ev["id"],
                "title": ev["title"],
                "start": ev["date"] + (f"T{ev['time']}:00" if ev.get("time") else ""),
                "allDay": not ev.get("time"),
                "extendedProps": {
                    "type": ev["type"], "source": ev["source"], "recurring": ev["recurring"],
                    "description": ev.get("description") or "",
                },
            })
        return jsonify(out)

    # -- Dashboard extras ----------------------------------------------------

    @app.get("/api/weather")
    def weather():
        key = os.getenv("OPENWEATHER_API_KEY", "")
        if not key:
            return jsonify({"error": "OPENWEATHER_API_KEY not configured",
                            "hint": "Add OPENWEATHER_API_KEY=<key> to your .env file"}), 503
        city = request.args.get("city", os.getenv("OPENWEATHER_CITY", "Mumbai"))
        try:
            resp = requests.get("https://api.openweathermap.org/data/2.5/weather",
                                params={"q": city, "appid": key, "units": "metric"}, timeout=5)
            resp.raise_for_status()
            d = resp.json()
            return jsonify({
                "city": d["name"], "country": d["sys"]["country"],
                "temp_c": round(d["main"]["temp"]), "feels_like": round(d["main"]["feels_like"]),
                "humidity": d["main"]["humidity"], "description": d["weather"][0]["description"].title(),
                "icon_code": d["weather"][0]["icon"], "wind_kmh": round(d["wind"]["speed"] * 3.6),
            })
        except requests.exceptions.Timeout:
            return jsonify({"error": "Weather API timed out"}), 504
        except Exception as exc:
            return jsonify({"error": str(exc)}), 502

    @app.get("/api/settings")
    def get_settings():
        return jsonify(_read_settings())

    @app.post("/api/settings")
    def save_settings():
        body = request.get_json(force=True, silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"status": "error", "reason": "Expected a JSON object."}), 400
        # Activity switches must start/stop the tracker, so they go through their own endpoint.
        body = {k: v for k, v in body.items() if k not in _ACTIVITY_KEYS.values()}
        try:
            return jsonify({"status": "ok", "settings": _update_settings(body)})
        except settings.SettingsError as exc:
            return jsonify({"status": "error", "reason": str(exc)}), 409
        except OSError as exc:
            return jsonify({"status": "error", "reason": f"Couldn't save settings ({exc.strerror or exc})."}), 500

    # -- Activity log -----------------------------------------------------------

    @app.post("/api/activity/settings")
    def activity_settings():
        body = request.get_json(force=True, silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"status": "error", "reason": "Expected a JSON object."}), 400
        try:
            return jsonify({"status": "ok", "activity": backend.apply_activity_settings(body)})
        except ValueError as exc:
            return jsonify({"status": "error", "reason": str(exc)}), 400
        except (KillSwitchEngaged, settings.SettingsError) as exc:
            return jsonify({"status": "error", "reason": str(exc)}), 409
        except (OSError, sqlite3.Error) as exc:
            return jsonify({"status": "error", "reason": f"Couldn't save settings ({getattr(exc, 'strerror', None) or exc})."}), 500

    @app.get("/api/activity")
    def activity_recent():
        from pikina.activity.store import ActivityStore
        try:
            limit = max(1, min(int(request.args.get("limit", 50)), 500))
        except ValueError:
            limit = 50
        store = ActivityStore()
        return jsonify({"actions": store.recent(limit=limit), "app_switches": store.switches(limit=limit),
                        "stats": store.stats(), "status": backend.activity_status()})

    @app.get("/api/activity/export")
    def activity_export():
        from pikina.activity.store import ActivityStore
        return jsonify(ActivityStore().export())

    @app.post("/api/wallpaper/random")
    def random_wallpaper():
        url = "https://wallhaven.cc/api/v1/search?q=anime+modern&purity=100&sorting=random"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode())
            if data.get("data"):
                image_url = data["data"][0]["path"]
                _update_settings({"wallpaperUrl": image_url})
                return jsonify({"status": "ok", "wallpaperUrl": image_url})
        except Exception as exc:
            print(f"[Backend] Wallpaper fetch failed: {exc}")
        return jsonify({"error": "Failed to fetch wallpaper"}), 500

    if start_daemons:
        backend.start_daemons()
    if backend.tier2:
        backend.tier2.warm_up_async()
    return app


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    # One backend per machine: a second copy would fight over the databases.
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(("127.0.0.1", _INSTANCE_LOCK_PORT))
    except OSError:
        print("[Backend] Another instance is already running. Exiting.")
        sys.exit(0)

    load_env()
    copied = paths.migrate_legacy_data()
    if copied:
        print(f"[Backend] Moved data from an older install into {paths.data_dir()}: {', '.join(copied)}")

    app = create_app(start_daemons=os.getenv("PIKINA_NO_DAEMONS", "0") != "1")
    atexit.register(app.extensions["pikina"].stop_daemons)

    port = int(os.getenv("BACKEND_PORT", DEFAULT_PORT))
    print(f"\n{'=' * 55}")
    print(f"  Pikina OS {__version__} backend  —  http://127.0.0.1:{port}")
    print(f"  Data    : {paths.data_dir()}")
    print(f"  Profile : {get_profile()}")
    print(f"{'=' * 55}\n")
    app.run(host="127.0.0.1", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    main()
