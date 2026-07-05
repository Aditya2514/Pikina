import sys
sys.path.insert(0, '.')

from core.eventbus.bus import EventBus
from core.registry.loader import CapabilityRegistry
from core.router.tier1_win32 import Tier1Router
from core.mcm.orchestrator import Orchestrator
from core.governor.telemetry import get_telemetry

bus    = EventBus()
reg    = CapabilityRegistry()
router = Tier1Router(registry=reg)
mcm    = Orchestrator(router=router)

# Test 1: Capability — find file in project dir only (narrow root)
r1 = reg.execute("fs.find_file", {"name": "README.md", "root": ".", "max_results": 3})
print("find_file test:", r1["status"], r1["count"], "results")
assert r1["status"] == "ok"

# Test 2: Telemetry
t = get_telemetry()
print("CPU:", t["cpu"]["percent"], "% | RAM:", t["ram"]["percent"], "%")
assert 0 <= t["cpu"]["percent"] <= 100

# Test 3: Event bus publish + replay
eid = bus.publish("test.smoke", {"msg": "all clear"}, "TRUSTED_COMMAND")
events = bus.replay.query(since_minutes=1)
print("Events in last 1 min:", len(events))
assert len(events) >= 1

# Test 4: MCM rejects untrusted input
r2 = mcm.receive("rm -rf /", source="file_contents")
print("MCM untrusted rejection:", r2["status"])
assert r2["status"] == "rejected"

# Test 5: Tier 1 no-match
r3 = router.route("this is not a command")
print("No-match result:", r3["status"])
assert r3["status"] == "no_match"

print()
print("=== Phase 1 smoke test PASSED ===")
