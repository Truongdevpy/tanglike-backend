with open("backend/app/routers/admin.py", "r", encoding="utf-8") as f:
    text = f.read()

idx = text.find("admin_update_order_status")
print(text[idx-50:idx+1200])