from typing import List
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import Transaction, User
from app.schemas.all import ApiResponse, TransactionResponse
from app.auth.security import get_current_user

router = APIRouter(prefix="/transactions", tags=["Transactions"])

@router.get("", response_model=ApiResponse[List[TransactionResponse]])
async def list_transactions(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Transaction)
        .where(Transaction.user_id == current_user.id)
        .order_by(desc(Transaction.created_at))
        .offset((page - 1) * limit)
        .limit(limit)
    )
    res = await db.execute(stmt)
    txs = res.scalars().all()
    return ApiResponse(data=[TransactionResponse.model_validate(t) for t in txs])
