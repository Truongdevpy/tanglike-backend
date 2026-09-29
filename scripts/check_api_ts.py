with open("frontend/src/lib/api.ts", "r", encoding="utf-8") as f:
    text = f.read()

idx = text.find("getAdminProviders")
print(text[idx-50:idx+600])