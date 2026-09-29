from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import Order, User, Service
from app.schemas.all import (
    ApiResponse, OrderCreate, MassOrderCreate, DripFeedOrderCreate, OrderResponse
)
from app.auth.security import get_current_user
from app.services.order_service import OrderService
from app.utils.security import sanitize_search_query

router = APIRouter(prefix="/orders", tags=["Orders"])

def sanitize_order_response(ord_resp: OrderResponse, is_admin: bool = False) -> OrderResponse:
    if not is_admin:
        ord_resp.provider_price = None
        ord_resp.provider_cost = None
        ord_resp.profit = None
        ord_resp.provider_status = None
        if ord_resp.error_message:
            err_lower = ord_resp.error_message.lower()
            if any(kw in err_lower for kw in ["provider", "incorrect", "api", "exception", "traceback", "http", "502", "500", "timeout"]):
                ord_resp.error_message = "Đang xử lý hoặc chờ duyệt. Vui lòng liên hệ CSKH nếu cần hỗ trợ."
    return ord_resp


import time
from collections import defaultdict

class UserOrderRateLimiter:
    def __init__(self, max_per_minute: int = 30):
        self.max_per_minute = max_per_minute
        self.requests = defaultdict(list)

    def check(self, user_id: int):
        now = time.time()
        timestamps = [t for t in self.requests[user_id] if now - t < 60.0]
        if len(timestamps) >= self.max_per_minute:
            raise HTTPException(
                status_code=429,
                detail="Bạn đang tạo đơn quá nhanh. Vui lòng đợi 1 phút trước khi tạo đơn tiếp theo.",
                headers={"Retry-After": "60"}
            )
        timestamps.append(now)
        self.requests[user_id] = timestamps

user_order_rate_limiter = UserOrderRateLimiter(max_per_minute=30)


@router.post("", response_model=ApiResponse[OrderResponse])
async def create_order(
    payload: OrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_order_rate_limiter.check(current_user.id)
    service = OrderService(db)
    order = await service.create_single_order(
        user=current_user,
        service_id=payload.service_id,
        link=payload.link,
        quantity=payload.quantity,
        coupon_code=payload.coupon_code,
        comments=payload.comments,
        reaction=payload.reaction,
        speed=payload.speed,
        custom_data=payload.custom_data
    )
    s_res = await db.execute(select(Service).where(Service.id == order.service_id))
    s_obj = s_res.scalar_one_or_none()
    
    resp = OrderResponse.model_validate(order)
    if s_obj:
        resp.service_name = s_obj.name
        resp.platform = s_obj.platform
        resp.refill_enabled = s_obj.refill_enabled
        
    return ApiResponse(data=sanitize_order_response(resp, getattr(current_user, "role", "") == "ADMIN"), message="Đặt đơn hàng thành công!")

@router.post("/mass", response_model=ApiResponse[dict])
async def create_mass_order(
    payload: MassOrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_order_rate_limiter.check(current_user.id)
    if len(payload.orders) > 100:
        raise HTTPException(status_code=400, detail="Đặt đơn hàng loạt không được vượt quá 100 đơn cùng lúc.")

    items = [{"service_id": it.service_id, "link": it.link, "quantity": it.quantity} for it in payload.orders]
    service = OrderService(db)
    result = await service.create_mass_orders(current_user, items)
    return ApiResponse(data=result, message=f"Đã xử lý {result['total']} đơn: Thành công {result['success']}, Thất bại {result['failed']}")

@router.post("/drip-feed", response_model=ApiResponse[OrderResponse])
async def create_drip_feed_order(
    payload: DripFeedOrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user_order_rate_limiter.check(current_user.id)
    total_qty = payload.runs * payload.quantity_per_run
    service = OrderService(db)
    order = await service.create_single_order(
        user=current_user,
        service_id=payload.service_id,
        link=payload.link,
        quantity=total_qty,
        is_dripfeed=True,
        dripfeed_runs=payload.runs,
        dripfeed_interval=payload.interval_minutes
    )
    return ApiResponse(data=sanitize_order_response(OrderResponse.model_validate(order), getattr(current_user, "role", "") == "ADMIN"), message="Đã tạo đơn hàng nhỏ giọt thành công.")

@router.get("", response_model=ApiResponse[List[OrderResponse]])
async def list_orders(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Order).where(Order.user_id == current_user.id).order_by(desc(Order.created_at))
    if status and status.upper() != "ALL":
        if status.upper() == "REFILL":
            stmt = stmt.join(Service, Order.service_id == Service.id).where(
                Order.status.in_(["COMPLETED", "PARTIAL"]),
                Service.refill_enabled == True
            )
        else:
            stmt = stmt.where(Order.status == status.upper())
    search = sanitize_search_query(search)
    if search:
        stmt = stmt.where((Order.link.ilike(f"%{search}%")) | (Order.id == int(search) if search.isdigit() else False))

    skip = (page - 1) * limit
    stmt = stmt.offset(skip).limit(limit)
    res = await db.execute(stmt)
    orders = res.scalars().all()

    # Preload services
    service_ids = {o.service_id for o in orders}
    services_map = {}
    if service_ids:
        s_res = await db.execute(select(Service).where(Service.id.in_(service_ids)))
        for s in s_res.scalars().all():
            services_map[s.id] = (s.name, s.platform, s.refill_enabled)

    output = []
    for o in orders:
        ord_resp = OrderResponse.model_validate(o)
        if o.service_id in services_map:
            name, plat, ref_en = services_map[o.service_id]
            ord_resp.service_name = name
            ord_resp.platform = plat
            ord_resp.refill_enabled = ref_en
        output.append(sanitize_order_response(ord_resp, getattr(current_user, "role", "") == "ADMIN"))

    return ApiResponse(data=output, meta={"page": page, "limit": limit})

@router.get("/{id}", response_model=ApiResponse[OrderResponse])
async def get_order_detail(
    id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Order).where(Order.id == id, Order.user_id == current_user.id)
    res = await db.execute(stmt)
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")

    s_res = await db.execute(select(Service).where(Service.id == order.service_id))
    srv = s_res.scalar_one_or_none()
    resp = OrderResponse.model_validate(order)
    if srv:
        resp.service_name = srv.name
        resp.platform = srv.platform
        resp.refill_enabled = srv.refill_enabled
    return ApiResponse(data=sanitize_order_response(resp, getattr(current_user, "role", "") == "ADMIN"))

@router.post("/{id}/refill", response_model=ApiResponse[dict])
async def request_order_refill(
    id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = OrderService(db)
    result = await service.request_refill(current_user, id)
    return ApiResponse(data=result, message=result["message"])

@router.post("/{id}/cancel", response_model=ApiResponse[dict])
async def cancel_order(
    id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = OrderService(db)
    result = await service.cancel_order_by_user(current_user, id)
    return ApiResponse(data=result, message=result["message"])