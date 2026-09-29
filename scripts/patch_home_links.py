with open("frontend/src/app/page.tsx", "r", encoding="utf-8") as f:
    code = f.read()

code = code.replace('href="/order?prices=1"', 'href="/services"')

with open("frontend/src/app/page.tsx", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated links in frontend/src/app/page.tsx to /services")