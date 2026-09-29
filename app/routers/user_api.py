from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from fastapi import APIRouter, Depends, HTTPException, Query
from app.utils.security import validate_target_link
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import User, Service, Order, Category
from app.schemas.all import ApiResponse
from app.auth.security import get_api_key_user
from app.services.order_service import OrderService

router = APIRouter(prefix="/user-api", tags=["Customer REST API v1"])

class ApiOrderCreate(BaseModel):
    service: int = Field(..., gt=0)
    link: str = Field(..., min_length=4, max_length=2048)
    quantity: int = Field(..., gt=0)

class ApiOrderAction(BaseModel):
    order_id: int = Field(..., gt=0)

@router.get("/services")
async def user_api_services(
    api_user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(Service)
        .join(Category, Service.category_id == Category.id)
        .where(
            Service.status == "ACTIVE",
            Service.is_deleted == False,
            Category.status == "ACTIVE",
            Category.is_deleted == False,
        )
        .order_by(Service.sort_order, Service.id)
    )
    res = await db.execute(stmt)
    services_list = list(res.scalars().all())
    services_list.sort(key=lambda s: (
        1 if (
            getattr(s, "is_ttc", False)
            or s.provider_id == 1
            or (s.name and "ttc" in s.name.lower())
            or (s.description and "ttc" in s.description.lower())
            or s.sort_order >= 900
        ) else 0,
        s.sort_order,
        s.id
    ))
    return [
        {
            "service": s.id,
            "name": s.name,
            "type": s.service_type,
            "category": s.platform,
            "rate": s.price,
            "min": s.min_quantity,
            "max": s.max_quantity,
            "refill": s.refill_enabled,
            "cancel": s.cancel_enabled
        }
        for s in services_list
    ]

@router.post("/order")
async def user_api_create_order(
    payload: ApiOrderCreate,
    api_user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    order_service = OrderService(db)
    order = await order_service.create_single_order(
        user=api_user,
        service_id=payload.service,
        link=payload.link,
        quantity=payload.quantity
    )
    return {"order": order.id}

@router.get("/order/{id}")
async def user_api_order_status(
    id: int,
    api_user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Order).where(Order.id == id, Order.user_id == api_user.id)
    res = await db.execute(stmt)
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")
    return {
        "charge": order.price,
        "start_count": order.start_count,
        "status": order.status,
        "remains": order.remains,
        "currency": "VND"
    }

@router.get("/orders")
async def user_api_orders(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    api_user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (await db.execute(
        select(Order)
        .where(Order.user_id == api_user.id)
        .order_by(desc(Order.created_at))
        .offset((page - 1) * limit)
        .limit(limit)
    )).scalars().all()
    return {
        "page": page,
        "limit": limit,
        "orders": [{
            "order": order.id,
            "charge": order.price,
            "start_count": order.start_count,
            "status": order.status,
            "remains": order.remains,
            "currency": "VND",
        } for order in rows],
    }

@router.get("/balance")
async def user_api_balance(
    api_user: User = Depends(get_api_key_user)
):
    return {
        "balance": api_user.balance,
        "currency": "VND"
    }

@router.post("/refill")
async def user_api_refill(
    payload: ApiOrderAction,
    api_user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db),
):
    result = await OrderService(db).request_refill(api_user, payload.order_id)
    return {"order": payload.order_id, "result": result}

@router.post("/cancel")
async def user_api_cancel(
    payload: ApiOrderAction,
    api_user: User = Depends(get_api_key_user),
    db: AsyncSession = Depends(get_db),
):
    result = await OrderService(db).cancel_order_by_user(api_user, payload.order_id)
    return {"order": payload.order_id, "result": result}
