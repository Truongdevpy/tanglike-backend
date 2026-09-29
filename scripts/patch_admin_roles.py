with open("backend/app/routers/admin.py", "r", encoding="utf-8") as f:
    code = f.read()

target = "@router.delete(\"/users/{id}\", response_model=ApiResponse[bool])"

new_endpoints = """@router.put("/users/{id}/role", response_model=ApiResponse[UserResponse])
async def admin_change_user_role(
    id: int,
    role_val: str = Query(..., pattern="^(USER|PRO|ADMIN|SUPPORT|RESELLER)$"),
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    \"\"\"Admin đổi vai trò của người dùng (USER, PRO, ADMIN, SUPPORT, RESELLER).\"\"\"
    res = await db.execute(select(User).where(User.id == id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")

    user.role = role_val.upper()
    if user.role in ["ADMIN", "PRO"]:
        user.is_pro = True
    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="CHANGE_USER_ROLE",
        target_type="USER",
        target_id=str(user.id),
        details=f"Admin {admin.username} đổi vai trò tài khoản {user.username} thành: {user.role}"
    )
    db.add(audit)
    await db.commit()
    await db.refresh(user)
    return ApiResponse(data=UserResponse.model_validate(user), message=f"Đã cập nhật vai trò của {user.username} thành {user.role}")

@router.put("/users/{id}/pro", response_model=ApiResponse[UserResponse])
async def admin_toggle_user_pro(
    id: int,
    is_pro: bool = Query(...),
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    \"\"\"Admin cấp hoặc hủy gói PRO cho người dùng.\"\"\"
    res = await db.execute(select(User).where(User.id == id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")

    user.is_pro = is_pro
    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="TOGGLE_USER_PRO",
        target_type="USER",
        target_id=str(user.id),
        details=f"Admin {admin.username} {'cấp' if is_pro else 'hủy'} quyền PRO cho {user.username}"
    )
    db.add(audit)
    await db.commit()
    await db.refresh(user)
    return ApiResponse(data=UserResponse.model_validate(user), message=f"Đã cập nhật trạng thái PRO cho {user.username}")

"""

code = code.replace(target, new_endpoints + target)

with open("backend/app/routers/admin.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated backend/app/routers/admin.py with role and pro endpoints!")