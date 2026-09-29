with open("frontend/src/app/admin/providers/page.tsx", "r", encoding="utf-8") as f:
    code = f.read()

# Make sure balance displays correct currency (p.currency || 'XU' or 'USD')
old_bal = "{p.balance?.toLocaleString('vi-VN')} {p.provider_type === 'thmxh' ? 'USD' : 'đ/XU'}"
new_bal = "{p.balance?.toLocaleString('vi-VN')} {p.currency || (p.provider_type === 'thmxh' ? 'USD' : 'XU')}"

if old_bal in code:
    code = code.replace(old_bal, new_bal)
    print("Updated balance currency in provider card!")

with open("frontend/src/app/admin/providers/page.tsx", "w", encoding="utf-8") as f:
    f.write(code)

print("Saved frontend/src/app/admin/providers/page.tsx")