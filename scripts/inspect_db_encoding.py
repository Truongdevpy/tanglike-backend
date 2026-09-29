import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
conn = sqlite3.connect('test_tanglike.db')
cursor = conn.cursor()
cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in cursor.fetchall()]
for tbl in tables:
    if tbl.startswith('sqlite_'): continue
    cursor.execute(f"PRAGMA table_info({tbl})")
    cols = [col[1] for col in cursor.fetchall()]
    for col in cols:
        try:
            cursor.execute(f"SELECT id, {col} FROM {tbl} WHERE {col} LIKE '%?%' OR {col} LIKE '%Ã%' OR {col} LIKE '%Ä%'")
            rows = cursor.fetchall()
            if rows:
                print(f"Table {tbl}.{col} has {len(rows)} suspicious rows:")
                for r in rows[:5]:
                    print("  ", r[0], repr(r[1])[:80])
        except Exception:
            pass
conn.close()
