with open("backend/app/routers/admin.py", "r", encoding="utf-8") as f:
    code = f.read()

old_del = """    await db.execute(delete(User).where(User.username != "admin"))
    await db.execute(delete(Order))
    await db.execute(delete(Transaction))
    await db.execute(delete(Payment))
    await db.execute(delete(RefillRequest))
    await db.execute(delete(SupportMessage))
    await db.execute(delete(SupportConversation))
    await db.execute(delete(Provider).where(Provider.id >= 3))
    await db.execute(delete(PasswordResetToken))
    await db.execute(delete(EmailVerificationToken))
    await db.execute(delete(JobRun))
    await db.execute(delete(AuditLog))
    await db.execute(delete(PriceSyncLog))"""

new_del = """    # Delete child records first to respect foreign keys
    await db.execute(delete(RefillRequest))
    await db.execute(delete(Order))
    await db.execute(delete(Transaction))
    await db.execute(delete(Payment))
    await db.execute(delete(SupportMessage))
    await db.execute(delete(SupportConversation))
    await db.execute(delete(PasswordResetToken))
    await db.execute(delete(EmailVerificationToken))
    await db.execute(delete(User).where(User.username != "admin"))
    await db.execute(delete(Provider).where(Provider.id >= 3))
    await db.execute(delete(JobRun))
    await db.execute(delete(AuditLog))
    await db.execute(delete(PriceSyncLog))"""

code = code.replace(old_del, new_del)

with open("backend/app/routers/admin.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated deletion order in backend/app/routers/admin.py")