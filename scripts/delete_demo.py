import sqlite3
c = sqlite3.connect('tanglike.db')
c.execute("DELETE FROM users WHERE username = 'demo'")
c.commit()
print("Remaining users:", c.execute("SELECT id, username, email FROM users").fetchall())