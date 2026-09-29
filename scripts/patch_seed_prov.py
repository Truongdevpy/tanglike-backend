with open("backend/app/main.py", "r", encoding="utf-8") as f:
    code = f.read()

old_ttc = """        p_res = await db.execute(select(Provider).where(Provider.name == "TuongTacCheo Core"))"""
new_ttc = """        p_res = await db.execute(select(Provider).where((Provider.name == "TuongTacCheo Core") | (Provider.provider_type == "tuongtaccheo")))"""

code = code.replace(old_ttc, new_ttc)

with open("backend/app/main.py", "w", encoding="utf-8") as f:
    f.write(code)

import sqlite3
c = sqlite3.connect("tanglike.db")
c.execute("DELETE FROM providers WHERE id >= 3")
c.commit()
print("Providers in DB:", c.execute("SELECT id, name, provider_type FROM providers").fetchall())