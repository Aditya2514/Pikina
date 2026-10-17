import sqlite3

from pikina import paths

conn = sqlite3.connect(paths.data_file(paths.REPLAY_DB))
conn.execute("DELETE FROM events WHERE topic LIKE 'fs.%'")
conn.commit()
conn.execute("VACUUM")
conn.close()
print("Cleaned up replay_store.sqlite")
