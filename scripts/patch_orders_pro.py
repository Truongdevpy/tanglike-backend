with open("backend/app/routers/orders.py", "r", encoding="utf-8") as f:
    code = f.read()

mass_target = """async def create_mass_order(
    payload: MassOrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):"""

mass_replacement = """async def create_mass_order(
    payload: MassOrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not (current_user.role in ["ADMIN", "PRO"] or getattr(current_user, "is_pro", False)):
        raise HTTPException(
            status_code=403,
            detail="Tính năng Đặt đơn hàng loạt (Mass Order) chỉ dành riêng cho tài khoản có gói PRO hoặc Quản trị viên. Vui lòng nâng cấp tài khoản của bạn."
        )"""

drip_target = """async def create_drip_feed_order(
    payload: DripFeedOrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):"""

drip_replacement = """async def create_drip_feed_order(
    payload: DripFeedOrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if not (current_user.role in ["ADMIN", "PRO"] or getattr(current_user, "is_pro", False)):
        raise HTTPException(
            status_code=403,
            detail="Tính năng Đơn nhỏ giọt (Drip-feed) chỉ dành riêng cho tài khoản có gói PRO hoặc Quản trị viên. Vui lòng nâng cấp tài khoản của bạn."
        )"""

code = code.replace(mass_target, mass_replacement)
code = code.replace(drip_target, drip_replacement)

with open("backend/app/routers/orders.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated backend/app/routers/orders.py with PRO checks!")