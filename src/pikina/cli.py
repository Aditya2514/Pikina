"""
Pikina OS — terminal interface.

Run `pikina` (or `python -m pikina`) to talk to the router directly,
without the backend server or the desktop app.
"""
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
            result = mcm.receive(raw, source="user_typed")
            status = result.get("status", "unknown")
            icon   = "[OK]" if status == "ok" else ("[X]" if status in ("error", "denied") else "->")
            print(f"  {icon} {result}")


if __name__ == "__main__":
    main()
