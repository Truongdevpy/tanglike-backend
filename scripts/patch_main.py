with open("backend/app/main.py", "r", encoding="utf-8") as f:
    code = f.read()

old_seed = """        # 2. Demo client user
        res_demo = await db.execute(select(User).where(User.username == "demo"))
        if not res_demo.scalars().first() and not is_production:"""

new_seed = """        # 2. Demo client user (only if explicitly configured or on fresh install)
        res_demo = await db.execute(select(User).where(User.username == "demo"))
        if not existing_admin and not res_demo.scalars().first() and not is_production and getattr(settings, "SEED_DEMO_USER", False):"""

code = code.replace(old_seed, new_seed)

with open("backend/app/main.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated seed_initial_data in backend/app/main.py")