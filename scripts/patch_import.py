with open("backend/app/routers/admin.py", "r", encoding="utf-8") as f:
    code = f.read()

old_mod = """from app.models.all import (
    User, Service, Category, Provider, Order, Payment, BankTransaction,
    Transaction, Coupon, SupportConversation, SupportMessage, AuditLog, SystemSetting,
    Notification, Referral, SubSite, RefillRequest, JobRun,
    ProviderServiceMapping, PriceSyncLog
)"""

new_mod = """from app.models.all import (
    User, Service, Category, Provider, Order, Payment, BankTransaction,
    Transaction, Coupon, SupportConversation, SupportMessage, AuditLog, SystemSetting,
    Notification, Referral, SubSite, RefillRequest, JobRun,
    ProviderServiceMapping, PriceSyncLog, PasswordResetToken, EmailVerificationToken
)"""

code = code.replace(old_mod, new_mod)

with open("backend/app/routers/admin.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Imported PasswordResetToken and EmailVerificationToken in admin.py")