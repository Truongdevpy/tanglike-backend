import sqlite3

con = sqlite3.connect("tanglike.db")
cur = con.cursor()

# 1. Clean test users, keeping only real admin (username == 'admin')
cur.execute("SELECT id, username, email FROM users WHERE username = 'admin'")
admin_user = cur.fetchone()
print(f"Keeping Admin: {admin_user}")

# Delete all users except admin
cur.execute("DELETE FROM users WHERE username != 'admin'")
print(f"Deleted test users, remaining users: {cur.execute('SELECT COUNT(*) FROM users').fetchone()[0]}")

# 2. Reset orders (user has no real orders yet)
cur.execute("DELETE FROM orders")
print(f"Orders count: {cur.execute('SELECT COUNT(*) FROM orders').fetchone()[0]}")

# 3. Reset transactions
cur.execute("DELETE FROM transactions")
print(f"Transactions count: {cur.execute('SELECT COUNT(*) FROM transactions').fetchone()[0]}")

# 4. Reset payments
cur.execute("DELETE FROM payments")
print(f"Payments count: {cur.execute('SELECT COUNT(*) FROM payments').fetchone()[0]}")

# 5. Reset refill requests
cur.execute("DELETE FROM refill_requests")

# 6. Reset support tickets and messages
cur.execute("DELETE FROM support_messages")
cur.execute("DELETE FROM support_conversations")

# 7. Clean test tokens
cur.execute("DELETE FROM password_reset_tokens")
cur.execute("DELETE FROM email_verification_tokens")

# 8. Clean test logs
cur.execute("DELETE FROM job_runs")
cur.execute("DELETE FROM audit_logs")
cur.execute("DELETE FROM price_sync_logs")

# 9. Clean mock test providers, keep only TuongTacCheo (id=1) and THMXH (id=2)
cur.execute("DELETE FROM providers WHERE id >= 3")
print("Remaining providers:", cur.execute("SELECT id, name, provider_type FROM providers").fetchall())

# 10. Update TTC provider details if needed
cur.execute("UPDATE providers SET name = 'TuongTacCheo (TTC)', provider_type = 'tuongtaccheo', base_url = 'https://tuongtaccheo.com/api/v2', status = 'ACTIVE', balance = 282605.0 WHERE id = 1")

# 11. Update system settings: ttc_markup_percent = 30%, ttc_rate_divider = 20
cur.execute("UPDATE system_settings SET value = '30' WHERE key = 'ttc_markup_percent'")
cur.execute("UPDATE system_settings SET value = '20' WHERE key = 'ttc_rate_divider'")

# 12. Update TTC services with 30% profit markup compared to original web cost (rate / 20)
mappings = cur.execute("SELECT service_id, original_rate FROM provider_service_mappings WHERE provider_id = 1").fetchall()
map_dict = {m[0]: m[1] for m in mappings if m[1]}

ttc_services = cur.execute("SELECT id, external_service_id, provider_price, price FROM services WHERE provider_id = 1").fetchall()
updated_count = 0
for s_id, ext_id, prov_price, price in ttc_services:
    orig_xu = map_dict.get(s_id)
    if not orig_xu or orig_xu <= 0:
        orig_xu = prov_price * 20.0 if prov_price else 1000.0
    # Per 1,000 units rate
    unit_cost = orig_xu / 20.0
    cost_vnd = round(unit_cost * 1000.0, 2)
    selling_vnd = round(cost_vnd * 1.30, 2)
    dealer_vnd = round(cost_vnd * 1.15, 2)
    cur.execute("UPDATE services SET provider_price = ?, price = ?, dealer_price = ? WHERE id = ?", (cost_vnd, selling_vnd, dealer_vnd, s_id))
    updated_count += 1

print(f"Updated {updated_count} TTC services with 30% markup over original web cost.")

con.commit()
con.close()
print("All test data cleaned successfully and data is now REAL!")