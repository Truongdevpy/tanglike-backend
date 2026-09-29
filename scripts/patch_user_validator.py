with open("backend/app/schemas/all.py", "r", encoding="utf-8") as f:
    code = f.read()

old_imp = "from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator"
new_imp = "from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator, model_validator"

code = code.replace(old_imp, new_imp)

old_ur = """    created_at: datetime
    last_login_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)"""

new_ur = """    created_at: datetime
    last_login_at: Optional[datetime]

    @model_validator(mode="after")
    def check_admin_pro(self):
        if self.role in ["ADMIN", "PRO"]:
            self.is_pro = True
        return self

    model_config = ConfigDict(from_attributes=True)"""

code = code.replace(old_ur, new_ur)

with open("backend/app/schemas/all.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated backend/app/schemas/all.py with model_validator!")