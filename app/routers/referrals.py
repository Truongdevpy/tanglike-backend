from datetime import datetime
from fastapi import APIRouter, Depends, Request
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import User, Referral, Transaction
from app.schemas.all import ApiResponse, ReferralStatsResponse
from app.auth.security import get_current_user
from app.config.settings import settings

router = APIRouter(prefix="/referral", tags=["Referrals"])

@router.get("/stats", response_model=ApiResponse[ReferralStatsResponse])
async def get_referral_stats(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    # Total referred users
    ref_count = await db.execute(
        select(func.count(User.id)).where(User.referred_by_id == current_user.id)
    )
    total_referred = ref_count.scalar() or 0

    # Total referral commission from transactions
    tx_sum = await db.execute(
        select(func.sum(Transaction.amount)).where(
            Transaction.user_id == current_user.id,
            Transaction.type == "BONUS"
        )
    )
    total_commission = tx_sum.scalar() or 0.0

    origin = (request.headers.get("origin") or "").rstrip("/")
    if origin and "localhost" not in origin and "127.0.0.1" not in origin:
        base_url = origin
    elif settings.PUBLIC_APP_URL and "localhost" not in settings.PUBLIC_APP_URL:
        base_url = settings.PUBLIC_APP_URL.rstrip("/")
    elif origin:
        base_url = origin
    else:
        base_url = "https://tanglike.vn"
    ref_code = current_user.referral_code or ""
    return ApiResponse(
        data=ReferralStatsResponse(
            referral_code=ref_code,
            referral_link=f"{base_url}/register?ref={ref_code}" if ref_code else f"{base_url}/register",
            commission_rate=settings.DEFAULT_REFERRAL_COMMISSION_PERCENT,
            total_referred=total_referred,
            total_commission=total_commission,
            this_month_commission=total_commission
        )
    )
