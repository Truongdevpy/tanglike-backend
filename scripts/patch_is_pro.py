with open("backend/app/models/all.py", "r", encoding="utf-8") as f:
    code = f.read()

old_user = """    token_version = Column(Integer, default=0, nullable=False)"""
new_user = """    token_version = Column(Integer, default=0, nullable=False)
    is_pro = Column(Boolean, default=False, nullable=False)"""

if old_user in code:
    code = code.replace(old_user, new_user)
    with open("backend/app/models/all.py", "w", encoding="utf-8") as f:
        f.write(code)
    print("Updated backend/app/models/all.py with is_pro")
else:
    print("old_user pattern not found in all.py")

with open("backend/app/schemas/all.py", "r", encoding="utf-8") as f:
    s_code = f.read()

old_schema = """    status: str
    referral_code: str"""
new_schema = """    status: str
    is_pro: bool = False
    referral_code: str"""

if old_schema in s_code:
    s_code = s_code.replace(old_schema, new_schema)
    with open("backend/app/schemas/all.py", "w", encoding="utf-8") as f:
        f.write(s_code)
    print("Updated backend/app/schemas/all.py with is_pro")
else:
    print("old_schema pattern not found in schemas/all.py")