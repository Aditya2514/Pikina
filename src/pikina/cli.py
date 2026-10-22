"""
Pikina OS — terminal interface.

Run `pikina` (or `python -m pikina`) to talk to the router directly,
without the backend server or the desktop app.
"""
import sqlite3

from pikina import __version__, paths
from pikina.eventbus.replay import ReplayStore
from pikina.governor.telemetry import get_telemetry
from pikina.mcm.orchestrator import Orchestrator
from pikina.registry.loader import CapabilityRegistry
from pikina.router.tier1 import Tier1Router
from pikina.router.tier2_ollama import Tier2Router


HELP = """
Commands:
  <anything>        Route through MCM -> Tier 1 regex, then Tier 2 Ollama (e.g. "open vs code")
  replay [N]        Show events from last N minutes (default 60)
  tools             List all registered capabilities
  plugins           List loaded, disabled and broken plugins
  activity          What the activity log recorded today ("activity this week" for 7 days)
  activity backfill Import past actions from the replay log again (safe to repeat)
  activity export F Save everything the activity log holds to the JSON file F
  telemetry         Show current CPU / RAM / battery
  quit / exit       Exit
"""


def main():
    print("=" * 60)
    print(f"  Pikina OS  {__version__}  —  CLI")
    print("  Tier 1 runs with zero models; Tier 2 uses Ollama if it is running.")
    print(HELP)
    print("=" * 60)

    from pikina.server import load_env
    load_env()
    paths.migrate_legacy_data()

    registry = CapabilityRegistry()
    router   = Tier1Router(registry=registry)
    tier2    = Tier2Router(registry=registry)
    mcm      = Orchestrator(router=router, tier2_router=tier2)

    while True:
        try:
            raw = input("\npikina> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[Pikina] Goodbye.")
            break

        if not raw:
            continue

        parts = raw.split()
        cmd   = parts[0].lower()

        if cmd in ("quit", "exit", "q"):
            print("[Pikina] Goodbye.")
            break

        elif cmd == "replay":
            minutes = int(parts[1]) if len(parts) > 1 else 60
            store   = ReplayStore()
            events  = store.query(since_minutes=minutes)
            if not events:
                print(f"[Replay] No events in the last {minutes} minutes.")
            else:
                print(f"\n[Replay] Last {minutes} minutes — {len(events)} events:\n")
                for ev in events:
                    ts    = ev["timestamp"][:19].replace("T", " ")
                    fc    = ev.get("failure_class") or "—"
                    print(f"  [{ts}] {ev['topic']:35s} {ev['provenance']:20s} fc={fc}")

        elif cmd == "tools":
            print("\n[Registry] Loaded capabilities:\n")
            for t in registry.list_tools():
                lvl = t["permission_level"]
                print(f"  [L{lvl}] {t['tool']:30s} — {t['description']}")

        elif cmd == "plugins":
            from pikina import plugins as plugin_mod
            print(f"\n[Plugins] Your plugins folder: {plugin_mod.user_plugins_dir()}\n")
            for p in registry.plugins:
                print(f"  [on]       {p.name} {p.version} ({p.source}) — {p.description}")
            for name in registry.disabled_plugins:
                print(f"  [off]      {name} — add it to \"plugins_enabled\" in settings.json")
            for err in registry.plugin_errors:
                print(f"  [skipped]  {err['plugin']} — {err['error']}")

        elif cmd == "activity" and len(parts) > 1 and parts[1].lower() in ("backfill", "export"):
            from pikina.activity.store import ActivityStore
            try:
                if parts[1].lower() == "backfill":
                    from pikina.activity.backfill import backfill
                    done = backfill(force=True)
                    print(f"  [Activity] {done.get('status')}: {done.get('added', 0)} rows added"
                          + (f" ({done['reason']})" if done.get("reason") else ""))
                elif len(parts) < 3:
                    print("  Usage: activity export <file.json>")
                else:
                    import json
                    from pathlib import Path
                    out = Path(" ".join(parts[2:])).expanduser()
                    data = ActivityStore().export()
                    out.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    print(f"  [Activity] Saved {len(data['activity'])} actions and "
                          f"{len(data['app_switches'])} app switches to {out}")
            except (OSError, ValueError, sqlite3.Error) as exc:
                print(f"  [Activity] Couldn't do that: {getattr(exc, 'strerror', None) or exc}")

        elif cmd == "telemetry":
            t = get_telemetry()
            print(f"\n  CPU:  {t['cpu']['percent']}%  ({t['cpu']['core_count']} cores)")
            print(f"  RAM:  {t['ram']['used_gb']} / {t['ram']['total_gb']} GB  ({t['ram']['percent']}%)")
            if t["battery"]:
                b = t["battery"]
                plug = "🔌 Plugged" if b["plugged"] else "🔋 Battery"
                print(f"  BAT:  {b['percent']}%  {plug}")
            print(f"  DISK: {t['disk']['used_gb']} / {t['disk']['total_gb']} GB  ({t['disk']['percent']}%)")

        else:
            try:
                result = mcm.receive(raw, source="user_typed", origin="cli")
            except Exception as exc:  # one bad command must not end the session
                print(f"  [X] {type(exc).__name__}: {exc}")
                continue
            status = result.get("status", "unknown")
            icon   = "[OK]" if status == "ok" else ("[X]" if status in ("error", "denied") else "->")
            text   = result.get("message") or result.get("reason")
            if text:
                print(f"  {icon} " + str(text).replace("\n", "\n      "))
            else:
                print(f"  {icon} {result}")


if __name__ == "__main__":
    main()
