import win32clipboard, win32con, time
from pikina.eventbus.bus import EventBus
from pikina.memory.trust_engine import TrustEngine
from pikina.daemons.clipboard import ClipboardDaemon
import sqlite3

bus = EventBus()
te = TrustEngine(bus=bus)
cd = ClipboardDaemon(bus=bus)
cd.start()

# Wait a beat for the daemon to start listening
time.sleep(1)

win32clipboard.OpenClipboard()
win32clipboard.EmptyClipboard()
win32clipboard.SetClipboardText('LiveClipboardTest_FixVerified999', win32con.CF_UNICODETEXT)
win32clipboard.CloseClipboard()

# Wait for daemon -> EventBus -> TrustEngine -> VectorStore
time.sleep(3)

from pikina import paths
c = sqlite3.connect(paths.data_file(paths.VECTOR_DB))
rows = c.execute("SELECT content FROM ephemeral_vectors WHERE content LIKE '%FixVerified999%'").fetchall()

print(f"Verified rows found in vector_memory.db: {len(rows)}")
for row in rows:
    print(f"Row content: {row[0]}")

cd.stop()
