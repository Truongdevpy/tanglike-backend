with open("backend/app/routers/users.py", "r", encoding="utf-8") as f:
    code = f.read()

if "from decimal import Decimal" not in code:
    code = "from decimal import Decimal\n" + code

endpoint = """

@router.post("/upgrade-pro", response_model=ApiResponse[UserResponse])
async def upgrade_to_pro(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    \"\"\"
    Nâng cấp tài khoản lên gói PRO để mở khóa đặt đơn hàng loạt và đơn nhỏ giọt.
    Phí nâng cấp: 50.000đ trừ trực tiếp từ số dư ví.
    \"\"\"
    if current_user.role in ["ADMIN", "PRO"] or getattr(current_user, "is_pro", False):
        current_user.is_pro = True
        await db.commit()
        await db.refresh(current_user)
        return ApiResponse(data=UserResponse.model_validate(current_user), message="Tài khoản của bạn đã có gói PRO hoạt động!")

    pro_fee = Decimal("50000.00")
    u_res = await db.execute(select(User).where(User.id == current_user.id).with_for_update())
    locked_user = u_res.scalar_one_or_none()
    if not locked_user or locked_user.balance < pro_fee:
        curr_bal = int(locked_user.balance if locked_user else 0)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Số dư không đủ để nâng cấp gói PRO (Phí: 50.000đ). Số dư hiện tại của bạn là {curr_bal:,}đ."
        )

    b_before = locked_user.balance
    locked_user.balance -= pro_fee
    locked_user.is_pro = True

    tx = Transaction(
        user_id=locked_user.id,
        type="ADJUSTMENT",
        amount=-pro_fee,
        balance_before=b_before,
        balance_after=locked_user.balance,
        reference=f"UPGRADE-PRO-{locked_user.id}-{int(datetime.utcnow().timestamp())}",
        description="Nâng cấp tài khoản gói PRO (Mở khóa Mass order & Drip-feed)",
        status="SUCCESS",
        created_at=datetime.utcnow()
    )
    db.add(tx)
    await db.commit()
    await db.refresh(locked_user)

    return ApiResponse(
        data=UserResponse.model_validate(locked_user),
        message="Chúc mừng! Bạn đã nâng cấp thành công lên tài khoản PRO."
    )
"""

code += endpoint

with open("backend/app/routers/users.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated backend/app/routers/users.py with upgrade_to_pro!")