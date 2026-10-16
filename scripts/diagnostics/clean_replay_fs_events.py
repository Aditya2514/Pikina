import sqlite3

conn = sqlite3.connect('core/eventbus/replay_store.sqlite')
conn.execute("DELETE FROM events WHERE topic LIKE 'fs.%'")
conn.commit()
conn.execute("VACUUM")
conn.close()
print("Cleaned up replay_store.sqlite")
