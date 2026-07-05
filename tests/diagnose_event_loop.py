"""Diagnostic: What's generating 200K+ file events?"""
import sqlite3
import json
from collections import Counter

conn = sqlite3.connect('core/eventbus/replay_store.sqlite')

# 1. What paths are being modified most?
print("=" * 60)
print("TOP 20 MOST-MODIFIED PATHS")
print("=" * 60)
rows = conn.execute(
    "SELECT payload FROM events WHERE topic IN ('fs.file_modified', 'fs.file_created') ORDER BY timestamp DESC LIMIT 5000"
).fetchall()

path_counts = Counter()
for r in rows:
    try:
        p = json.loads(r[0]).get('path', '?')
        path_counts[p] += 1
    except:
        pass

for path, count in path_counts.most_common(20):
    print(f"  {count:>6d}  {path}")

# 2. Event rate over time
print("\n" + "=" * 60)
print("EVENT RATE (last 10 distinct timestamps)")
print("=" * 60)
rows2 = conn.execute(
    "SELECT timestamp, COUNT(*) FROM events WHERE topic LIKE 'fs.%' GROUP BY substr(timestamp, 1, 16) ORDER BY timestamp DESC LIMIT 10"
).fetchall()
for ts, cnt in rows2:
    print(f"  {ts[:19]}  →  {cnt} events")

# 3. Check if data/ or core/eventbus/ paths appear (self-inflicted loop)
print("\n" + "=" * 60)
print("SELF-INFLICTED LOOP CHECK")
print("=" * 60)
self_paths = [p for p in path_counts if 'replay_store' in p or 'vector_memory' in p or 'data\\\\' in p or 'data/' in p or '.sqlite' in p or '.db' in p or '__pycache__' in p]
if self_paths:
    print("  ⚠ FOUND paths that look like self-inflicted writes:")
    for p in self_paths:
        print(f"    {path_counts[p]:>6d}  {p}")
else:
    print("  No obvious self-inflicted loop in top 5000 events.")
    # But let's also check broader
    all_paths = list(path_counts.keys())
    internal = [p for p in all_paths if any(x in p.lower() for x in ['pikina', 'core', 'data'])]
    if internal:
        print(f"  But {len(internal)} paths reference Pikina project dirs:")
        for p in internal[:10]:
            print(f"    {path_counts[p]:>6d}  {p}")

conn.close()
