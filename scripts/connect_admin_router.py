with open("backend/app/routers/admin.py", "r", encoding="utf-8") as f:
    text = f.read()

# Check if provider_router is imported
if "from app.routers.admin_providers import provider_router" not in text:
    text = "from app.routers.admin_providers import provider_router\n" + text

# Locate old provider routes
target_start = "@router.get(\"/providers\""
target_end = "# 4. Service Management"

idx_start = text.find(target_start)
idx_end = text.find(target_end)

if idx_start != -1 and idx_end != -1:
    replacement = "router.include_router(provider_router)\n\n"
    text = text[:idx_start] + replacement + text[idx_end:]
    print("Replaced old provider routes with router.include_router(provider_router)")
else:
    print("Could not find start or end:", idx_start, idx_end)

with open("backend/app/routers/admin.py", "w", encoding="utf-8") as f:
    f.write(text)

import ast
ast.parse(open("backend/app/routers/admin.py", "r", encoding="utf-8").read())
print("admin.py syntax OK!")