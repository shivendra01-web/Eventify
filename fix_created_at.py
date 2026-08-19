import sqlite3

conn = sqlite3.connect("eventify.db")

cursor = conn.cursor()

cursor.execute("""
UPDATE events
SET created_at = CURRENT_TIMESTAMP
WHERE created_at IS NULL
""")

conn.commit()

print("Fixed:", cursor.rowcount)

conn.close()