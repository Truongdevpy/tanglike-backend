from decimal import Decimal
from datetime import datetime
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
import os
from app.utils.upload import validate_uploaded_image, get_safe_destination_path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import (
    User, Order, Transaction, Payment, Notification,
    SupportConversation, Referral, AuditLog
)
from app.auth.security import get_current_user, verify_password
from app.schemas.all import ApiResponse, UserResponse, DeactivateAccountRequest

router = APIRouter(prefix="/users", tags=["Users & GDPR"])

@router.get("/me", response_model=ApiResponse[UserResponse])
async def get_user_profile(current_user: User = Depends(get_current_user)):
    return ApiResponse(data=UserResponse.model_validate(current_user))

@router.get("/me/export-data", response_model=ApiResponse[Dict[str, Any]])
async def export_gdpr_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    GDPR Article 20 - Right to Data Portability.
    Exports all personal account data, orders, financial ledgers, and tickets in structured JSON.
    """
    # 1. Orders
    o_res = await db.execute(select(Order).where(Order.user_id == current_user.id).order_by(Order.id.desc()))
    orders = [
        {
            "id": o.id,
            "service_id": o.service_id,
            "link": o.link,
            "quantity": o.quantity,
            "price": o.price,
            "status": o.status,
            "created_at": o.created_at.isoformat() if o.created_at else None
        }
        for o in o_res.scalars().all()
    ]

    # 2. Transactions
    t_res = await db.execute(select(Transaction).where(Transaction.user_id == current_user.id).order_by(Transaction.id.desc()))
    transactions = [
        {
            "id": t.id,
            "type": t.type,
            "amount": t.amount,
            "balance_before": t.balance_before,
            "balance_after": t.balance_after,
            "description": t.description,
            "created_at": t.created_at.isoformat() if t.created_at else None
        }
        for t in t_res.scalars().all()
    ]

    # 3. Payments
    p_res = await db.execute(select(Payment).where(Payment.user_id == current_user.id).order_by(Payment.id.desc()))
    payments = [
        {
            "id": p.id,
            "amount": p.amount,
            "payment_method": p.payment_method,
            "transaction_code": p.transaction_code,
            "status": p.status,
            "created_at": p.created_at.isoformat() if p.created_at else None
        }
        for p in p_res.scalars().all()
    ]

    # 4. Support conversations
    s_res = await db.execute(select(SupportConversation).where(SupportConversation.user_id == current_user.id))
    tickets = [
        {
            "id": c.id,
            "subject": c.subject,
            "status": c.status,
            "created_at": c.created_at.isoformat() if c.created_at else None
        }
        for c in s_res.scalars().all()
    ]

    export_payload = {
        "export_timestamp": datetime.utcnow().isoformat(),
        "compliance": "GDPR Article 20 (Data Portability)",
        "user_profile": {
            "id": current_user.id,
            "username": current_user.username,
            "email": current_user.email,
            "full_name": current_user.full_name,
            "phone": current_user.phone,
            "role": current_user.role,
            "balance": current_user.balance,
            "referral_code": current_user.referral_code,
            "created_at": current_user.created_at.isoformat() if current_user.created_at else None,
            "last_login_at": current_user.last_login_at.isoformat() if current_user.last_login_at else None
        },
        "orders_count": len(orders),
        "orders": orders,
        "transactions_count": len(transactions),
        "transactions": transactions,
        "payments_count": len(payments),
        "payments": payments,
        "support_tickets_count": len(tickets),
        "support_tickets": tickets
    }

    return ApiResponse(
        data=export_payload,
        message="Xuất dữ liệu cá nhân theo tiêu chuẩn GDPR thành công."
    )

@router.post("/me/deactivate", response_model=ApiResponse[bool])
async def deactivate_account(
    payload: DeactivateAccountRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    GDPR Article 17 - Right to Erasure ('Right to be Forgotten').
    Soft-deletes and anonymizes user profile, invalidating future logins and API keys.
    """
    if not verify_password(payload.password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu xác nhận không chính xác."
        )

    # Anonymize PII
    current_user.is_deleted = True
    current_user.deleted_at = datetime.utcnow()
    current_user.status = "DEACTIVATED"
    current_user.token_version = (current_user.token_version or 0) + 1
    current_user.phone = None
    current_user.avatar = None
    current_user.full_name = "Deactivated User"
    current_user.api_key = None  # Revoke API key immediately
    current_user.api_key_hash = None
    current_user.api_key_prefix = None

    audit = AuditLog(
        user_id=current_user.id,
        username=current_user.username,
        action="GDPR_DEACTIVATE_ACCOUNT",
        target_type="USER",
        target_id=str(current_user.id),
        details=f"Người dùng tự yêu cầu đóng tài khoản GDPR. Lý do: {payload.reason or 'Không nêu'}"
    )
    db.add(audit)

    await db.commit()
    return ApiResponse(
        data=True,
        message="Tài khoản của bạn đã được vô hiệu hóa và ẩn thông tin cá nhân thành công."
    )


@router.post("/upgrade-pro", response_model=ApiResponse[UserResponse])
async def upgrade_to_pro(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Mở sẵn toàn bộ tính năng nâng cao cho tất cả thành viên."""
    return ApiResponse(
        data=UserResponse.model_validate(current_user),
        message="Toàn bộ tính năng đã sẵn sàng cho tài khoản của bạn!"
    )


@router.post("/avatar", response_model=ApiResponse[Dict[str, str]])
async def upload_user_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    raw_content = await file.read()
    try:
        safe_filename = validate_uploaded_image(
            filename=file.filename or "avatar.png",
            content_type=file.content_type or "application/octet-stream",
            file_bytes=raw_content
        )
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

    upload_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "static", "uploads"))
    os.makedirs(upload_dir, exist_ok=True)

    dest_path = get_safe_destination_path(upload_dir, safe_filename)
    with open(dest_path, "wb") as f:
        f.write(raw_content)

    avatar_url = f"/static/uploads/{safe_filename}"
    current_user.avatar = avatar_url
    await db.commit()
    await db.refresh(current_user)

    return ApiResponse(
        data={"avatar_url": avatar_url, "filename": safe_filename},
        message="Cập nhật ảnh đại diện thành công."
    )
