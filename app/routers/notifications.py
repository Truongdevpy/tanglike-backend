from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import Notification, User
from app.schemas.all import ApiResponse, NotificationResponse
from app.auth.security import get_current_user

router = APIRouter(prefix="/notifications", tags=["Notifications"])

@router.get("", response_model=ApiResponse[List[NotificationResponse]])
async def list_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Notification).where(Notification.user_id == current_user.id).order_by(desc(Notification.created_at)).limit(30)
    res = await db.execute(stmt)
    notifs = res.scalars().all()
    return ApiResponse(data=[NotificationResponse.model_validate(n) for n in notifs])

@router.put("/{id}/read", response_model=ApiResponse[bool])
async def mark_notification_read(
    id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Notification).where(Notification.id == id, Notification.user_id == current_user.id))
    notif = res.scalar_one_or_none()
    if not notif:
        raise HTTPException(status_code=404, detail="Thông báo không tồn tại.")
    notif.is_read = True
    await db.commit()
    return ApiResponse(data=True)

@router.put("/read-all", response_model=ApiResponse[bool])
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Notification).where(Notification.user_id == current_user.id, Notification.is_read == False))
    for n in res.scalars().all():
        n.is_read = True
    await db.commit()
    return ApiResponse(data=True, message="Đã đánh dấu tất cả là đã đọc.")
