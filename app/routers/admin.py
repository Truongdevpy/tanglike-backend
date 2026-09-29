import math
from app.routers.admin_providers import provider_router
from app.routers.services import invalidate_service_cache
from app.utils.crypto import encrypt_secret, decrypt_secret
from app.utils.security import is_safe_url, is_safe_presentation_url, sanitize_search_query
from datetime import datetime, timedelta
from decimal import Decimal
import json
import logging
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select, update, delete, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import (
    User, Service, Category, Provider, Order, Payment, BankTransaction,
    Transaction, Coupon, SupportConversation, SupportMessage, AuditLog, SystemSetting,
    Notification, Referral, SubSite, RefillRequest, JobRun,
    ProviderServiceMapping, PriceSyncLog, PasswordResetToken, EmailVerificationToken
)
from app.services.pricing_service import (
    calculate_single_price, calculate_service_prices, generate_markup_previews
)
from app.schemas.all import (
    ApiResponse, UserResponse, ServiceCreate, ServiceUpdate, ServiceResponse,
    StatusToggleRequest, BulkStatusRequest,
    CategoryCreate, CategoryUpdate, CategoryResponse, ProviderCreate,
    PaymentResponse, TransactionResponse, SupportConversationResponse,
    ProviderUpdate, ProviderResponse, OrderResponse, CouponCreate, CouponUpdate, CouponResponse,
    AuditLogResponse, DashboardStats,
    ProviderTestConnectionRequest, ProviderTestConnectionResponse,
    ProviderCatalogItem, ImportServicesRequest, BulkMarkupPreviewRequest,
    ProviderMappingResponse, UpdateMappingRequest, PriceSyncLogResponse
)
from app.auth.security import require_role
from app.providers.manager import ProviderManager
from app.services.order_service import OrderService
from app.payments.bank_sync import BankSyncService
from app.payments.service import PaymentService
from app.payments.helpers import VietQRHelper
from app.config.settings import settings
from app.schemas.all import (
    BankingConfigResponse, BankingConfigUpdateRequest,
    BankingStatsResponse, BankTransactionItemResponse
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin Operations"], dependencies=[Depends(require_role(["ADMIN"]))])


class AdminOrderStatusUpdate(BaseModel):
    status: str
    remains: Optional[int] = None
    start_count: Optional[int] = None
    external_order_id: Optional[str] = None

class TicketStatusUpdate(BaseModel):
    status: Optional[str] = None
    customer_type: Optional[str] = None
    product_status: Optional[str] = None
    tags: Optional[str] = None

class BulkTicketActionRequest(BaseModel):
    ticket_ids: List[int]

class BulkMessageActionRequest(BaseModel):
    message_ids: List[int]

class BulkTicketStatusRequest(BaseModel):
    ticket_ids: List[int]
    status: Optional[str] = None
    customer_type: Optional[str] = None
    product_status: Optional[str] = None
    is_hidden: Optional[bool] = None

class SubSiteStatusUpdate(BaseModel):
    status: str

class BroadcastNotificationRequest(BaseModel):
    title: str
    message: str
    type: str = "INFO"
    target_role: Optional[str] = None

class SystemSettingsUpdateRequest(BaseModel):
    settings: Dict[str, str]


# Settings stored here are intentionally limited to non-secret presentation and
# feature flags. Credentials belong in the deployment environment or a secret
# manager and must never be returned by this API.
PUBLIC_SYSTEM_SETTING_KEYS = frozenset({
    "site_name", "site_title", "site_description", "currency_symbol",
    "site_logo", "favicon_url", "brand_color", "primary_color", "auth_banner_text", "meta_keywords",
    "ttc_rate_divider", "ttc_markup_percent", "default_markup_percent",
    "thmxh_usd_rate", "usd_rate",
    "min_deposit_amount", "maintenance_mode", "registration_enabled",
    "telegram_notifications",
    "zalo_url", "popup_enabled", "popup_title", "popup_content",
    "auto_round", "round_type", "round_unit",
    "support_email", "admin_email",
    "rate_limit_per_minute", "max_login_attempts", "enable_2fa",
    "cleanup_auto_enabled",
    "retention_audit_logs_days",
    "retention_job_runs_days",
    "retention_price_sync_days",
    "retention_reset_tokens_days",
    "retention_read_notifications_days",
    "retention_stale_payments_days",
    "cleanup_last_run",
    "cleanup_last_summary",
})

class AdjustBalanceRequest(BaseModel):
    amount: float
    description: str = "Điều chỉnh số dư bởi Quản trị viên"

class BulkMarkupRequest(BaseModel):
    markup_percent: float
    auto_round: Optional[bool] = True
    round_type: Optional[str] = "nearest"
    round_unit: Optional[float] = 10.0

class CleanupNowRequest(BaseModel):
    audit_logs_days: Optional[int] = None
    job_runs_days: Optional[int] = None
    price_sync_days: Optional[int] = None
    reset_tokens_days: Optional[int] = None
    read_notifications_days: Optional[int] = None
    stale_payments_days: Optional[int] = None

class ApplyRoundingRequest(BaseModel):
    auto_round: Optional[bool] = True
    round_type: Optional[str] = "nearest"
    round_unit: Optional[float] = 10.0

class RefundActionRequest(BaseModel):
    amount: Optional[float] = None
    reason: str = "Duyệt hoàn tiền bởi Admin"

@router.get("/job-runs", response_model=ApiResponse[list])
async def admin_list_job_runs(
    job_name: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(JobRun).order_by(desc(JobRun.started_at)).limit(limit)
    if job_name:
        stmt = stmt.where(JobRun.job_name == job_name)
    rows = (await db.execute(stmt)).scalars().all()
    return ApiResponse(data=[{
        "id": row.id,
        "job_name": row.job_name,
        "status": row.status,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "duration_ms": row.duration_ms,
        "details": row.details,
    } for row in rows])


@router.get("/job-status", response_model=ApiResponse[list])
async def admin_job_status(db: AsyncSession = Depends(get_db)):
    schedules = {
        "bank_sync": max(15, settings.MB_BANK_POLL_INTERVAL_SECONDS),
        "order_sync": 15,
        "refill_sync": 15,
        "maintenance_cleanup": 24 * 60 * 60,
        "provider_service_sync": max(60, settings.PROVIDER_SYNC_INTERVAL_SECONDS),
    }
    output = []
    for name, interval_seconds in schedules.items():
        rows = (await db.execute(
            select(JobRun)
            .where(JobRun.job_name == name)
            .order_by(desc(JobRun.started_at))
            .limit(20)
        )).scalars().all()
        latest = rows[0] if rows else None
        failures = 0
        for run in rows:
            if run.status != "FAILED":
                break
            failures += 1
        next_run_at = (
            latest.started_at + timedelta(seconds=interval_seconds)
            if latest else datetime.utcnow() + timedelta(seconds=interval_seconds)
        )
        is_job_enabled = True
        if name == "bank_sync":
            from app.services.payment_service import PaymentService
            b_conf = await PaymentService(db).get_banking_config()
            is_job_enabled = bool(b_conf.get("enabled")) or bool(settings.MB_BANK_API_URL and settings.MB_BANK_API_TOKEN)

        output.append({
            "job_name": name,
            "enabled": is_job_enabled,
            "interval_seconds": interval_seconds,
            "last_run": {
                "status": latest.status,
                "started_at": latest.started_at,
                "completed_at": latest.completed_at,
                "duration_ms": latest.duration_ms,
                "details": latest.details,
            } if latest else None,
            "next_run_at": next_run_at,
            "consecutive_failures": failures,
        })
    return ApiResponse(data=output)


# 1. Admin Dashboard Stats
@router.get("/dashboard-stats", response_model=ApiResponse[DashboardStats])
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    user_count = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_users = (await db.execute(select(func.count(User.id)).where(User.status == "ACTIVE"))).scalar() or 0
    
    order_count = (await db.execute(select(func.count(Order.id)))).scalar() or 0
    completed_orders = (await db.execute(select(func.count(Order.id)).where(Order.status == "COMPLETED"))).scalar() or 0
    pending_orders = (await db.execute(select(func.count(Order.id)).where(Order.status == "PENDING"))).scalar() or 0
    processing_orders = (await db.execute(select(func.count(Order.id)).where(Order.status == "PROCESSING"))).scalar() or 0

    valid_statuses = ["COMPLETED", "PROCESSING", "PENDING", "PARTIAL", "IN_PROGRESS", "ACTIVE"]
    revenue_raw = (await db.execute(
        select(func.sum(Order.price)).where(Order.status.in_(valid_statuses))
    )).scalar() or 0.0
    revenue = round(float(revenue_raw), 2)

    cost_raw = (await db.execute(
        select(func.sum(Order.quantity * func.coalesce(Service.provider_price, 0.0) / 1000.0))
        .join(Service, Order.service_id == Service.id)
        .where(Order.status.in_(valid_statuses))
    )).scalar() or 0.0
    provider_cost = round(float(cost_raw), 2)

    profit = round(max(0.0, revenue - provider_cost), 2) if revenue >= provider_cost else round(revenue - provider_cost, 2)
    profit_percent = round((profit / revenue) * 100.0, 1) if revenue > 0 else 0.0
    markup_percent = round((profit / provider_cost) * 100.0, 1) if provider_cost > 0 else 0.0

    open_tickets = (await db.execute(select(func.count(SupportConversation.id)).where(SupportConversation.status == "OPEN"))).scalar() or 0
    total_user_balance = (await db.execute(select(func.sum(User.balance)).where(User.role != "ADMIN"))).scalar() or 0.0

    return ApiResponse(
        data=DashboardStats(
            total_users=user_count,
            active_users=active_users,
            total_orders=order_count,
            completed_orders=completed_orders,
            pending_orders=pending_orders,
            processing_orders=processing_orders,
            revenue=revenue,
            profit=profit,
            provider_cost=provider_cost,
            profit_percent=profit_percent,
            markup_percent=markup_percent,
            open_tickets=open_tickets,
            user_balance=round(float(total_user_balance), 2)
        )
    )

@router.post("/clean-test-data", response_model=ApiResponse[dict])
async def admin_clean_test_data(
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Xóa dữ liệu thử nghiệm/mô phỏng, bảo toàn tài khoản Admin và đưa số liệu về thực tế 100%."""
    # Delete child records first to respect foreign keys
    await db.execute(delete(RefillRequest))
    await db.execute(delete(Order))
    await db.execute(delete(Transaction))
    await db.execute(delete(Payment))
    await db.execute(delete(SupportMessage))
    await db.execute(delete(SupportConversation))
    await db.execute(delete(PasswordResetToken))
    await db.execute(delete(EmailVerificationToken))
    await db.execute(delete(User).where(User.id != admin.id, User.role != "ADMIN", User.username != "admin"))
    await db.execute(delete(Provider).where(Provider.id >= 3))
    await db.execute(delete(JobRun))
    await db.execute(delete(AuditLog))
    await db.execute(delete(PriceSyncLog))
    await db.commit()
    return ApiResponse(data={"status": "cleaned"}, message="Đã xóa toàn bộ dữ liệu mẫu. Hệ thống đã sạch và sẵn sàng hoạt động với số liệu thật!")

# 2. User Management
@router.get("/users", response_model=ApiResponse[List[UserResponse]])
async def admin_list_users(
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(User).order_by(desc(User.created_at))
    search = sanitize_search_query(search)
    if search:
        stmt = stmt.where((User.username.ilike(f"%{search}%")) | (User.email.ilike(f"%{search}%")))
    stmt = stmt.offset((page - 1) * limit).limit(limit)
    res = await db.execute(stmt)
    users = res.scalars().all()
    return ApiResponse(data=[UserResponse.model_validate(u) for u in users])

@router.put("/users/{id}/balance", response_model=ApiResponse[UserResponse])
async def admin_adjust_balance(
    id: int,
    payload: AdjustBalanceRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    # Serialize manual adjustments with every other wallet mutation.
    res = await db.execute(select(User).where(User.id == id).with_for_update())
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")

    if not math.isfinite(payload.amount) or payload.amount == 0:
        raise HTTPException(status_code=400, detail="Số tiền điều chỉnh phải là một số hữu hạn khác 0.")
    balance_before = user.balance
    adjustment = Decimal(str(payload.amount))
    balance_after = balance_before + adjustment
    if balance_after < 0:
        raise HTTPException(status_code=400, detail="Số dư người dùng không thể bị âm.")
    user.balance = balance_after

    # Audit log
    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="ADJUST_BALANCE",
        target_type="USER",
        target_id=str(user.id),
        details=f"Điều chỉnh: {payload.amount:+,}đ ({balance_before:,}đ -> {balance_after:,}đ). Lý do: {payload.description}"
    )
    db.add(audit)

    # Transaction ledger
    tx = Transaction(
        user_id=user.id,
        type="ADJUSTMENT",
        amount=adjustment,
        balance_before=balance_before,
        balance_after=balance_after,
        # A separate immutable ledger reference for every manual adjustment.
        reference=f"ADMIN-{admin.id}-{user.id}-{int(datetime.utcnow().timestamp() * 1_000_000)}",
        description=f"Admin {admin.username} điều chỉnh: {payload.description}",
        status="SUCCESS",
        created_at=datetime.utcnow()
    )
    db.add(tx)

    await db.commit()
    await db.refresh(user)
    return ApiResponse(data=UserResponse.model_validate(user), message=f"Đã cập nhật số dư cho {user.username}.")

@router.put("/users/{id}/status", response_model=ApiResponse[UserResponse])
async def admin_change_user_status(
    id: int,
    status_val: str = Query(..., pattern="^(ACTIVE|BANNED)$"),
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(User).where(User.id == id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    if user.id == admin.id and status_val.upper() != "ACTIVE":
        raise HTTPException(status_code=400, detail="Không thể tự khóa tài khoản của chính mình.")

    user.status = status_val
    if status_val != "ACTIVE":
        user.token_version = (user.token_version or 0) + 1
    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="CHANGE_USER_STATUS",
        target_type="USER",
        target_id=str(user.id),
        details=f"Chuyển trạng thái sang: {status_val}"
    )
    db.add(audit)
    await db.commit()
    await db.refresh(user)
    return ApiResponse(data=UserResponse.model_validate(user))
@router.put("/users/{id}/role", response_model=ApiResponse[UserResponse])
async def admin_change_user_role(
    id: int,
    role_val: str = Query(..., pattern="^(USER|PRO|ADMIN|SUPPORT|RESELLER)$"),
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Admin đổi vai trò của người dùng (USER, PRO, ADMIN, SUPPORT, RESELLER)."""
    res = await db.execute(select(User).where(User.id == id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    if user.id == admin.id and role_val.upper() != "ADMIN":
        raise HTTPException(status_code=400, detail="Không thể tự hạ quyền Quản trị viên của chính mình.")

    user.role = role_val.upper()
    if user.role in ["ADMIN", "PRO"]:
        user.is_pro = True
    user.token_version = (user.token_version or 0) + 1
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
    """Admin cấp hoặc hủy gói PRO cho người dùng."""
    res = await db.execute(select(User).where(User.id == id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    if user.id == admin.id and not is_pro:
        raise HTTPException(status_code=400, detail="Tài khoản Quản trị viên luôn có quyền PRO.")

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

@router.delete("/users/{id}", response_model=ApiResponse[bool])
async def admin_delete_user(
    id: int,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(User).where(User.id == id))
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Không tìm thấy người dùng.")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Không thể tự xóa tài khoản Quản trị viên của chính mình.")

    user.is_deleted = True
    user.deleted_at = datetime.utcnow()
    user.status = "BANNED"
    user.token_version = (user.token_version or 0) + 1
    user.api_key = None
    user.api_key_hash = None
    user.api_key_prefix = None
    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="DELETE_USER",
        target_type="USER",
        target_id=str(user.id),
        details=f"Admin {admin.username} đã vô hiệu hóa (soft delete) tài khoản {user.username}."
    )
    db.add(audit)
    await db.commit()
    return ApiResponse(data=True, message=f"Đã vô hiệu hóa tài khoản {user.username} thành công.")


# 3. Provider Management
router.include_router(provider_router)

# 4. Service Management
@router.get("/services", response_model=ApiResponse[List[ServiceResponse]])
async def admin_list_services(
    platform: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Service).where(Service.is_deleted == False).order_by(Service.sort_order, Service.id)
    if platform and platform.strip().upper() not in ("ALL", "*", ""):
        stmt = stmt.where(Service.platform.ilike(platform.strip()))
    if category_id:
        stmt = stmt.where(Service.category_id == category_id)
    if status and status.strip().upper() not in ("ALL", "*", ""):
        stmt = stmt.where(Service.status == status.strip().upper())
    search = sanitize_search_query(search)
    if search:
        stmt = stmt.where(Service.name.ilike(f"%{search}%"))

    res = await db.execute(stmt)
    services = res.scalars().all()

    # Preload categories and providers
    cat_ids = {s.category_id for s in services}
    cat_map = {}
    if cat_ids:
        c_res = await db.execute(select(Category).where(Category.id.in_(cat_ids)))
        for c in c_res.scalars().all():
            cat_map[c.id] = c.name

    prov_ids = {s.provider_id for s in services if s.provider_id}
    prov_map = {}
    if prov_ids:
        p_res = await db.execute(select(Provider).where(Provider.id.in_(prov_ids)))
        for p in p_res.scalars().all():
            prov_map[p.id] = (p.name or "", p.provider_type or "")

    output = []
    for s in services:
        sr = ServiceResponse.model_validate(s)
        sr.category_name = cat_map.get(s.category_id)
        p_name, p_type = prov_map.get(s.provider_id, ("", ""))
        is_ttc = bool(
            s.provider_id == 1
            or p_type.lower() == "tuongtaccheo"
            or "tuongtaccheo" in p_name.lower()
            or "ttc" in p_name.lower()
            or "tuongtaccheo" in (s.description or "").lower()
            or "ttc" in (s.description or "").lower()
            or "[ttc]" in (s.name or "").lower()
        )
        sr.is_ttc = is_ttc
        sr.server_tag = "TTC" if is_ttc else ("THMXH" if "thmxh" in p_type.lower() or "thmxh" in p_name.lower() else "SMM")
        output.append(sr)

    # Sap xep TTC xuong duoi cung
    output.sort(key=lambda s: (1 if getattr(s, 'is_ttc', False) else 0, s.sort_order, s.id))

    return ApiResponse(data=output)

@router.put("/services/{id}/status", response_model=ApiResponse[ServiceResponse])
async def admin_update_service_status(
    id: int,
    payload: StatusToggleRequest,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Service).where(Service.id == id, Service.is_deleted == False))
    srv = res.scalar_one_or_none()
    if not srv:
        raise HTTPException(status_code=404, detail="Không tìm thấy dịch vụ.")
    new_status = payload.status.strip().upper()
    if new_status not in ("ACTIVE", "INACTIVE", "HIDDEN"):
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ (chọn ACTIVE hoặc INACTIVE).")
    srv.status = new_status
    srv.updated_at = datetime.utcnow()
    await db.commit()
    invalidate_service_cache()
    await db.refresh(srv)
    msg = "Đã hiển thị dịch vụ." if new_status == "ACTIVE" else "Đã ẩn dịch vụ thành công."
    return ApiResponse(data=ServiceResponse.model_validate(srv), message=msg)

@router.post("/services/bulk-status", response_model=ApiResponse[dict])
async def admin_bulk_service_status(
    payload: BulkStatusRequest,
    db: AsyncSession = Depends(get_db)
):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="Danh sách ID rỗng.")
    new_status = payload.status.strip().upper()
    if new_status not in ("ACTIVE", "INACTIVE", "HIDDEN"):
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ.")

    res = await db.execute(select(Service).where(Service.id.in_(payload.ids), Service.is_deleted == False))
    services = res.scalars().all()
    count = 0
    for s in services:
        s.status = new_status
        s.updated_at = datetime.utcnow()
        count += 1

    await db.commit()
    invalidate_service_cache()
    msg = f"Đã hiển thị {count} dịch vụ thành công." if new_status == "ACTIVE" else f"Đã ẩn {count} dịch vụ thành công."
    return ApiResponse(data={"updated": count, "status": new_status}, message=msg)

@router.post("/services", response_model=ApiResponse[ServiceResponse])
async def admin_create_service(payload: ServiceCreate, db: AsyncSession = Depends(get_db)):
    srv = Service(
        category_id=payload.category_id,
        provider_id=payload.provider_id,
        external_service_id=payload.external_service_id,
        name=payload.name,
        description=payload.description,
        platform=payload.platform,
        service_type=payload.service_type,
        price=payload.price,
        provider_price=payload.provider_price,
        min_quantity=payload.min_quantity,
        max_quantity=payload.max_quantity,
        dripfeed_enabled=payload.dripfeed_enabled,
        refill_enabled=payload.refill_enabled,
        cancel_enabled=payload.cancel_enabled,
        status=payload.status,
        sort_order=payload.sort_order,
        created_at=datetime.utcnow()
    )
    db.add(srv)
    await db.commit()
    invalidate_service_cache()
    await db.refresh(srv)
    return ApiResponse(data=ServiceResponse.model_validate(srv), message="Thêm dịch vụ thành công.")

@router.put("/services/{id}", response_model=ApiResponse[ServiceResponse])
async def admin_update_service(id: int, payload: ServiceUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Service).where(Service.id == id))
    srv = res.scalar_one_or_none()
    if not srv:
        raise HTTPException(status_code=404, detail="Không tìm thấy dịch vụ.")

    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(srv, k, v)
    srv.updated_at = datetime.utcnow()
    await db.commit()
    invalidate_service_cache()
    await db.refresh(srv)
    return ApiResponse(data=ServiceResponse.model_validate(srv), message="Cập nhật dịch vụ thành công.")

@router.delete("/services/{id}", response_model=ApiResponse[bool])
async def admin_delete_service(id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Service).where(Service.id == id))
    srv = res.scalar_one_or_none()
    if not srv:
        raise HTTPException(status_code=404, detail="Không tìm thấy dịch vụ.")
    srv.is_deleted = True
    srv.status = "INACTIVE"
    srv.deleted_at = datetime.utcnow()
    await db.commit()
    invalidate_service_cache()
    return ApiResponse(data=True, message="Đã xóa dịch vụ thành công.")

@router.post("/services/bulk-markup", response_model=ApiResponse[dict])
async def admin_bulk_markup(payload: BulkMarkupRequest, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Service))
    services = res.scalars().all()
    count = 0
    for s in services:
        if s.provider_price and s.provider_price > 0:
            s.price = calculate_single_price(
                rate=float(s.provider_price),
                markup_type="PERCENT",
                markup_value=payload.markup_percent,
                auto_round=bool(payload.auto_round),
                round_type=payload.round_type or "nearest",
                round_unit=float(payload.round_unit or 10.0)
            )
            count += 1
    await db.commit()
    invalidate_service_cache()
    return ApiResponse(data={"updated": count}, message=f"Đã cập nhật giá cho {count} dịch vụ theo tỉ lệ +{payload.markup_percent}% (Làm tròn {payload.round_type or 'gần nhất'} đến {payload.round_unit or 10}đ).")

@router.post("/services/apply-rounding", response_model=ApiResponse[dict])
async def admin_apply_rounding(
    payload: Optional[ApplyRoundingRequest] = None,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Làm tròn toàn bộ giá bán dịch vụ và lưu cấu hình làm tròn vào hệ thống."""
    # Read rounding settings
    auto_round = payload.auto_round if (payload and payload.auto_round is not None) else True
    round_type = (payload.round_type if payload and payload.round_type else "nearest").lower()
    round_unit = float(payload.round_unit if payload and payload.round_unit else 10.0)

    # Persist rounding settings in SystemSetting
    round_settings = {
        "auto_round": str(auto_round).lower(),
        "round_type": round_type,
        "round_unit": str(round_unit)
    }
    for k, v in round_settings.items():
        s_res = await db.execute(select(SystemSetting).where(SystemSetting.key == k))
        s_row = s_res.scalar_one_or_none()
        if s_row:
            s_row.value = v
            s_row.updated_at = datetime.utcnow()
        else:
            db.add(SystemSetting(key=k, value=v, description="Cấu hình làm tròn giá bán", updated_at=datetime.utcnow()))

    # Update all services
    s_res = await db.execute(select(Service).where(Service.is_deleted == False))
    services = s_res.scalars().all()
    updated_count = 0

    for s in services:
        if s.price > 0:
            s.price = calculate_single_price(
                rate=float(s.price),
                markup_type="FIXED",
                markup_value=0.0,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit
            )
            if s.dealer_price and s.dealer_price > 0:
                s.dealer_price = calculate_single_price(
                    rate=float(s.dealer_price),
                    markup_type="FIXED",
                    markup_value=0.0,
                    auto_round=auto_round,
                    round_type=round_type,
                    round_unit=round_unit
                )
            s.updated_at = datetime.utcnow()
            updated_count += 1

    # Update provider service mappings
    m_res = await db.execute(select(ProviderServiceMapping))
    mappings = m_res.scalars().all()
    for m in mappings:
        m.auto_round = auto_round
        m.round_type = round_type
        m.round_unit = round_unit
        if m.original_rate > 0:
            prices = calculate_service_prices(
                rate=m.original_rate,
                user_markup_type=m.markup_type or "PERCENT",
                user_markup_value=m.markup_value or 30.0,
                dealer_markup_type=m.dealer_markup_type or "PERCENT",
                dealer_markup_value=m.dealer_markup_value or 15.0,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit
            )
            m.selling_price = prices["selling_price"]
            m.dealer_price = prices["dealer_price"]

    await db.commit()
    invalidate_service_cache()

    desc_type = "gần nhất" if round_type == "nearest" else ("lên" if round_type == "ceil" else "xuống")
    return ApiResponse(
        data={"updated": updated_count, "auto_round": auto_round, "round_type": round_type, "round_unit": round_unit},
        message=f"Đã tự động làm tròn giá cho {updated_count} dịch vụ (làm tròn {desc_type} đến {round_unit:,.0f}đ)!"
    )

# 5. Refunds & Orders
@router.post("/refund/{order_id}", response_model=ApiResponse[dict])
async def admin_refund_order(
    order_id: int,
    payload: RefundActionRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    service = OrderService(db)
    result = await service.refund_order_by_admin(order_id, payload.amount, payload.reason)
    return ApiResponse(data=result, message="Đã duyệt hoàn tiền thành công.")

# 6. Audit Logs
@router.get("/logs", response_model=ApiResponse[List[AuditLogResponse]])
async def admin_list_logs(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(AuditLog).order_by(desc(AuditLog.created_at)).limit(100))
    logs = res.scalars().all()
    return ApiResponse(data=[AuditLogResponse.model_validate(l) for l in logs])

# 7. Coupons
@router.get("/coupons", response_model=ApiResponse[List[CouponResponse]])
async def admin_list_coupons(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Coupon).order_by(desc(Coupon.created_at)))
    coupons = res.scalars().all()
    return ApiResponse(data=[CouponResponse.model_validate(c) for c in coupons])

@router.post("/coupons", response_model=ApiResponse[CouponResponse])
async def admin_create_coupon(payload: CouponCreate, db: AsyncSession = Depends(get_db)):
    coupon = Coupon(
        code=payload.code.upper().strip(),
        type=payload.type,
        value=payload.value,
        min_amount=payload.min_amount,
        max_discount=payload.max_discount,
        usage_limit=payload.usage_limit,
        expires_at=payload.expires_at,
        status="ACTIVE",
        created_at=datetime.utcnow()
    )
    db.add(coupon)
    await db.commit()
    await db.refresh(coupon)
    return ApiResponse(data=CouponResponse.model_validate(coupon), message="Tạo mã giảm giá thành công.")


@router.put("/coupons/{coupon_id}", response_model=ApiResponse[CouponResponse])
async def admin_update_coupon(
    coupon_id: int,
    payload: CouponUpdate,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Coupon).where(Coupon.id == coupon_id))
    coupon = res.scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="Không tìm thấy mã giảm giá.")

    if payload.value is not None:
        coupon.value = payload.value
    if payload.type is not None:
        coupon.type = payload.type
    if payload.min_amount is not None:
        coupon.min_amount = payload.min_amount
    if payload.max_discount is not None:
        coupon.max_discount = payload.max_discount
    if payload.usage_limit is not None:
        coupon.usage_limit = payload.usage_limit
    if payload.expires_at is not None:
        coupon.expires_at = payload.expires_at
    if payload.status is not None:
        coupon.status = payload.status

    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="UPDATE_COUPON",
        target_type="COUPON",
        target_id=coupon.id,
        details=f"Cập nhật mã giảm giá #{coupon.id} ({coupon.code})"
    )
    db.add(audit)
    await db.commit()
    await db.refresh(coupon)
    return ApiResponse(data=CouponResponse.model_validate(coupon), message="Đã cập nhật mã giảm giá thành công.")


@router.put("/coupons/{coupon_id}/toggle-status", response_model=ApiResponse[CouponResponse])
async def admin_toggle_coupon_status(
    coupon_id: int,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Coupon).where(Coupon.id == coupon_id))
    coupon = res.scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="Không tìm thấy mã giảm giá.")

    coupon.status = "INACTIVE" if coupon.status == "ACTIVE" else "ACTIVE"
    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="TOGGLE_COUPON_STATUS",
        target_type="COUPON",
        target_id=coupon.id,
        details=f"Đổi trạng thái mã #{coupon.id} ({coupon.code}) sang {coupon.status}"
    )
    db.add(audit)
    await db.commit()
    await db.refresh(coupon)
    return ApiResponse(data=CouponResponse.model_validate(coupon), message=f"Đã đổi trạng thái mã giảm giá sang {coupon.status}.")



@router.delete("/coupons/{coupon_id}", response_model=ApiResponse[dict])
async def admin_delete_coupon(
    coupon_id: int,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Coupon).where(Coupon.id == coupon_id))
    coupon = res.scalar_one_or_none()
    if not coupon:
        raise HTTPException(status_code=404, detail="Không tìm thấy mã giảm giá.")

    used_count = getattr(coupon, "used_count", 0) or 0
    if used_count > 0:
        coupon.status = "INACTIVE"
        audit = AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="DEACTIVATE_COUPON",
            target_type="COUPON",
            target_id=coupon.id,
            details=f"Vô hiệu hóa mã giảm giá #{coupon.id} ({coupon.code}) đã dùng {used_count} lần"
        )
        db.add(audit)
        await db.commit()
        return ApiResponse(
            data={"id": coupon_id, "action": "deactivated", "status": "INACTIVE"},
            message=f"Mã '{coupon.code}' đã được sử dụng ({used_count} lần) nên đã được chuyển sang trạng thái Vô hiệu hóa (Ngừng kích hoạt)."
        )

    await db.delete(coupon)
    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="DELETE_COUPON",
        target_type="COUPON",
        target_id=coupon_id,
        details=f"Xóa vĩnh viễn mã giảm giá #{coupon_id} ({coupon.code})"
    )
    db.add(audit)
    await db.commit()
    return ApiResponse(
        data={"id": coupon_id, "action": "deleted"},
        message=f"Đã xóa vĩnh viễn mã giảm giá '{coupon.code}'."
    )


# 8. Category Management
@router.get("/categories", response_model=ApiResponse[List[CategoryResponse]])
async def admin_list_categories(
    platform: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Category).where(Category.is_deleted == False).order_by(Category.sort_order, Category.id)
    if platform and platform.strip().upper() not in ("ALL", "*", ""):
        stmt = stmt.where(Category.platform.ilike(platform.strip()))
    if status and status.strip().upper() not in ("ALL", "*", ""):
        stmt = stmt.where(Category.status == status.strip().upper())
    search = sanitize_search_query(search)
    if search:
        stmt = stmt.where(Category.name.ilike(f"%{search}%"))

    res = await db.execute(stmt)
    cats = res.scalars().all()

    srv_counts_res = await db.execute(
        select(Service.category_id, func.count(Service.id))
        .where(Service.is_deleted == False)
        .group_by(Service.category_id)
    )
    count_map = {row[0]: row[1] for row in srv_counts_res.fetchall()}

    # Identify TTC categories
    ttc_res = await db.execute(
        select(Service.category_id).where(Service.provider_id == 1, Service.is_deleted == False)
    )
    ttc_cat_ids = set(ttc_res.scalars().all())

    output = []
    for c in cats:
        cr = CategoryResponse.model_validate(c)
        cr.service_count = count_map.get(c.id, 0)
        is_ttc_cat = bool(c.id in ttc_cat_ids or c.id in (1, 2, 4, 5) or 'ttc' in (c.name or '').lower() or c.sort_order >= 900)
        cr.is_ttc = is_ttc_cat
        output.append(cr)

    # Sap xep danh muc TTC xuong duoi cung
    output.sort(key=lambda c: (1 if getattr(c, 'is_ttc', False) else 0, c.sort_order, c.id))

    return ApiResponse(data=output)

@router.put("/categories/{id}/status", response_model=ApiResponse[CategoryResponse])
async def admin_update_category_status(
    id: int,
    payload: StatusToggleRequest,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Category).where(Category.id == id, Category.is_deleted == False))
    cat = res.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Không tìm thấy danh mục.")
    new_status = payload.status.strip().upper()
    if new_status not in ("ACTIVE", "INACTIVE", "HIDDEN"):
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ.")
    cat.status = new_status
    await db.commit()
    invalidate_service_cache()
    await db.refresh(cat)

    srv_counts_res = await db.execute(
        select(func.count(Service.id))
        .where(Service.category_id == cat.id, Service.is_deleted == False)
    )
    cnt = srv_counts_res.scalar() or 0
    cr = CategoryResponse.model_validate(cat)
    cr.service_count = cnt
    msg = "Đã hiển thị danh mục." if new_status == "ACTIVE" else "Đã ẩn danh mục thành công."
    return ApiResponse(data=cr, message=msg)

@router.post("/categories/bulk-status", response_model=ApiResponse[dict])
async def admin_bulk_category_status(
    payload: BulkStatusRequest,
    db: AsyncSession = Depends(get_db)
):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="Danh sách ID rỗng.")
    new_status = payload.status.strip().upper()
    if new_status not in ("ACTIVE", "INACTIVE", "HIDDEN"):
        raise HTTPException(status_code=400, detail="Trạng thái không hợp lệ.")

    res = await db.execute(select(Category).where(Category.id.in_(payload.ids), Category.is_deleted == False))
    cats = res.scalars().all()
    count = 0
    for c in cats:
        c.status = new_status
        count += 1

    await db.commit()
    invalidate_service_cache()
    msg = f"Đã hiển thị {count} danh mục thành công." if new_status == "ACTIVE" else f"Đã ẩn {count} danh mục thành công."
    return ApiResponse(data={"updated": count, "status": new_status}, message=msg)

@router.post("/categories", response_model=ApiResponse[CategoryResponse])
async def admin_create_category(payload: CategoryCreate, db: AsyncSession = Depends(get_db)):
    clean_slug = payload.slug.lower().strip()
    existing = await db.execute(select(Category).where(Category.slug == clean_slug))
    if existing.scalar_one_or_none():
        clean_slug = f"{clean_slug}-{int(datetime.utcnow().timestamp())}"
    cat = Category(
        name=payload.name,
        slug=clean_slug,
        platform=payload.platform,
        icon=payload.icon,
        status=getattr(payload, 'status', None) or 'ACTIVE',
        sort_order=payload.sort_order,
        created_at=datetime.utcnow()
    )
    db.add(cat)
    await db.commit()
    invalidate_service_cache()
    await db.refresh(cat)
    return ApiResponse(data=CategoryResponse.model_validate(cat), message="Tạo danh mục thành công.")

@router.put("/categories/{id}", response_model=ApiResponse[CategoryResponse])
async def admin_update_category(id: int, payload: CategoryUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Category).where(Category.id == id))
    cat = res.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Không tìm thấy danh mục.")
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(cat, k, v)
    await db.commit()
    invalidate_service_cache()
    await db.refresh(cat)
    return ApiResponse(data=CategoryResponse.model_validate(cat), message="Cập nhật danh mục thành công.")

@router.delete("/categories/{id}", response_model=ApiResponse[bool])
async def admin_delete_category(id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Category).where(Category.id == id, Category.is_deleted == False))
    cat = res.scalar_one_or_none()
    if not cat:
        raise HTTPException(status_code=404, detail="Không tìm thấy danh mục.")
    cat.is_deleted = True
    cat.status = "INACTIVE"
    cat.deleted_at = datetime.utcnow()
    await db.commit()
    invalidate_service_cache()
    return ApiResponse(data=True, message="Đã xóa danh mục.")

# 9. Orders Management
@router.get("/orders/{order_id}", response_model=ApiResponse[OrderResponse])
async def admin_get_order(order_id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Order).where(Order.id == order_id))
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")
    resp = OrderResponse.model_validate(order)
    s = await db.scalar(select(Service).where(Service.id == order.service_id))
    if s:
        resp.service_name = s.name
        resp.platform = s.platform
    return ApiResponse(data=resp)

@router.get("/orders", response_model=ApiResponse[List[OrderResponse]])
async def admin_list_orders(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    user_id: Optional[int] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Order).order_by(desc(Order.created_at))
    if status and status.upper() != "ALL":
        stmt = stmt.where(Order.status == status.upper())
    if user_id:
        stmt = stmt.where(Order.user_id == user_id)
    search = sanitize_search_query(search)
    if search:
        stmt = stmt.where(
            (Order.link.ilike(f"%{search}%")) |
            (Order.id == int(search) if search.isdigit() else False) |
            (Order.external_order_id.ilike(f"%{search}%"))
        )
    skip = (page - 1) * limit
    stmt = stmt.offset(skip).limit(limit)
    res = await db.execute(stmt)
    orders = res.scalars().all()

    service_ids = {o.service_id for o in orders}
    services_map = {}
    if service_ids:
        s_res = await db.execute(select(Service).where(Service.id.in_(service_ids)))
        for s in s_res.scalars().all():
            services_map[s.id] = (s.name, s.platform, float(s.provider_price or 0.0))

    output = []
    for o in orders:
        ord_resp = OrderResponse.model_validate(o)
        if o.service_id in services_map:
            name, plat, prov_price = services_map[o.service_id]
            ord_resp.service_name = name
            ord_resp.platform = plat
            cost = round((o.quantity / 1000.0) * prov_price, 2)
            ord_resp.provider_price = prov_price
            ord_resp.provider_cost = cost
            ord_resp.profit = round(float(o.price) - cost, 2)
        output.append(ord_resp)

    return ApiResponse(data=output, meta={"page": page, "limit": limit})

@router.put("/orders/{id}/status", response_model=ApiResponse[OrderResponse])
async def admin_update_order_status(
    id: int,
    payload: AdminOrderStatusUpdate,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Order).where(Order.id == id))
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")
    
    old_status = order.status
    order.status = payload.status.upper()
    if payload.remains is not None:
        order.remains = payload.remains
    if payload.start_count is not None:
        order.start_count = payload.start_count
    if payload.external_order_id:
        order.external_order_id = payload.external_order_id
    order.updated_at = datetime.utcnow()

    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="UPDATE_ORDER_STATUS",
        target_type="ORDER",
        target_id=str(order.id),
        details=f"Đổi trạng thái đơn #{order.id}: {old_status} -> {order.status}"
    )
    db.add(audit)
    await db.commit()
    await db.refresh(order)
    return ApiResponse(data=OrderResponse.model_validate(order), message="Cập nhật trạng thái đơn thành công.")

# 10. Payments Management


@router.post("/orders/{id}/sync-status", response_model=ApiResponse[OrderResponse])
async def admin_sync_order_status(id: int, db: AsyncSession = Depends(get_db)):
    """Đồng bộ trạng thái đơn hàng trực tiếp từ nhà cung cấp (action=status)."""
    res = await db.execute(select(Order).where(Order.id == id))
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")

    if not order.external_order_id:
        raise HTTPException(status_code=400, detail="Đơn hàng chưa có ID từ nhà cung cấp (external_order_id).")

    provider_obj = None
    if order.provider_id:
        p_res = await db.execute(select(Provider).where(Provider.id == order.provider_id))
        provider_obj = p_res.scalar_one_or_none()

    adapter = ProviderManager.get_provider(provider_obj)
    status_info = await adapter.get_order_status(order.external_order_id)

    order.start_count = status_info.start_count
    order.remains = status_info.remains
    if status_info.error_message:
        order.error_message = status_info.error_message

    if status_info.status and status_info.status != "FAILED":
        order.status = status_info.status
        if status_info.status == "COMPLETED":
            order.completed_at = datetime.utcnow()
            order.remains = 0

    await db.commit()
    await db.refresh(order)
    return ApiResponse(data=OrderResponse.model_validate(order), message=f"Đã cập nhật trạng thái đơn hàng: {order.status}")

@router.post("/orders/{id}/retry-provider", response_model=ApiResponse[OrderResponse])
async def admin_retry_order_provider(id: int, db: AsyncSession = Depends(get_db)):
    """Thử lại gửi đơn sang nhà cung cấp nếu trước đó bị lỗi kết nối."""
    res = await db.execute(select(Order).where(Order.id == id))
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")

    s_res = await db.execute(select(Service).where(Service.id == order.service_id))
    service = s_res.scalar_one_or_none()
    if not service:
        raise HTTPException(status_code=404, detail="Không tìm thấy dịch vụ tương ứng.")

    provider_obj = None
    if service.provider_id:
        p_res = await db.execute(select(Provider).where(Provider.id == service.provider_id))
        provider_obj = p_res.scalar_one_or_none()

    adapter = ProviderManager.get_provider(provider_obj)
    try:
        external_res = await adapter.create_order(
            service_id=service.external_service_id or str(service.id),
            link=order.link,
            quantity=order.quantity,
            runs=order.dripfeed_runs,
            interval=order.dripfeed_interval
        )
        order.external_order_id = str(external_res.get("order_id", ""))
        order.provider_id = service.provider_id
        order.status = "PROCESSING"
        order.error_message = None
        order.next_retry_at = None
        await db.commit()
        await db.refresh(order)
        return ApiResponse(data=OrderResponse.model_validate(order), message="Đã gửi lại đơn sang nhà cung cấp thành công!")
    except Exception as e:
        order.error_message = str(e)
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Lỗi gửi đơn sang nhà cung cấp: {str(e)}")

@router.post("/orders/{id}/refill-action", response_model=ApiResponse[dict])
async def admin_order_refill_action(id: int, db: AsyncSession = Depends(get_db)):
    """Gửi yêu cầu bảo hành (refill) sang nhà cung cấp."""
    res = await db.execute(select(Order).where(Order.id == id))
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")
    if not order.external_order_id:
        raise HTTPException(status_code=400, detail="Đơn hàng chưa có mã nhà cung cấp.")

    provider_obj = None
    if order.provider_id:
        p_res = await db.execute(select(Provider).where(Provider.id == order.provider_id))
        provider_obj = p_res.scalar_one_or_none()

    adapter = ProviderManager.get_provider(provider_obj)
    try:
        ref_res = await adapter.refill_order(order.external_order_id)
        return ApiResponse(data=ref_res, message="Đã gửi yêu cầu bảo hành sang nhà cung cấp.")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Lỗi bảo hành từ nhà cung cấp: {str(e)}")

@router.post("/orders/{id}/cancel-action", response_model=ApiResponse[dict])
async def admin_order_cancel_action(id: int, db: AsyncSession = Depends(get_db)):
    """Hủy đơn hàng tại nhà cung cấp và hoàn tiền cho thành viên."""
    res = await db.execute(select(Order).where(Order.id == id).with_for_update())
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")
    if order.status in {"CANCELED", "REFUNDED"}:
        raise HTTPException(status_code=409, detail="Đơn hàng đã được hủy hoặc hoàn tiền trước đó.")

    prior_refund = await db.execute(
        select(Transaction.id).where(
            Transaction.user_id == order.user_id,
            Transaction.type == "REFUND",
            Transaction.reference.in_([
                f"CANCEL-ORDER-{order.id}",
                f"REFUND-ORDER-{order.id}",
                f"ADMIN-CANCEL-{order.id}",
                f"AUTO-REFUND-CANCEL-{order.id}",
                f"AUTO-REFUND-PARTIAL-{order.id}",
            ]),
        )
    )
    if prior_refund.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="Đơn hàng đã được hoàn tiền trước đó.")

    if order.external_order_id:
        provider_obj = None
        if order.provider_id:
            p_res = await db.execute(select(Provider).where(Provider.id == order.provider_id))
            provider_obj = p_res.scalar_one_or_none()
        adapter = ProviderManager.get_provider(provider_obj)
        try:
            await adapter.cancel_order(order.external_order_id)
        except Exception:
            pass

    order.status = "CANCELED"
    u_res = await db.execute(select(User).where(User.id == order.user_id).with_for_update())
    user = u_res.scalar_one_or_none()
    if user:
        b_before = user.balance
        user.balance += order.price
        tx = Transaction(
            user_id=user.id,
            type="REFUND",
            amount=order.price,
            balance_before=b_before,
            balance_after=user.balance,
            reference=f"ADMIN-CANCEL-{order.id}",
            description=f"Admin hủy và hoàn tiền đơn #{order.id}",
            status="SUCCESS",
            created_at=datetime.utcnow()
        )
        db.add(tx)

    await db.commit()
    return ApiResponse(data={"order_id": order.id, "status": "CANCELED"}, message=f"Đã hủy đơn #{order.id} và hoàn tiền {order.price:,.0f}đ.")


@router.get("/payments", response_model=ApiResponse[List[PaymentResponse]])
async def admin_list_payments(
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Payment).order_by(desc(Payment.created_at))
    if status and status.upper() != "ALL":
        requested_status = status.upper()
        if requested_status in {"SUCCESS", "COMPLETED"}:
            stmt = stmt.where(Payment.status.in_(["SUCCESS", "COMPLETED"]))
        else:
            stmt = stmt.where(Payment.status == requested_status)
    search = sanitize_search_query(search)
    if search:
        stmt = stmt.where(Payment.transaction_code.ilike(f"%{search}%"))
    if start_date:
        try:
            s_dt = datetime.fromisoformat(start_date)
            stmt = stmt.where(Payment.created_at >= s_dt)
        except Exception:
            pass
    if end_date:
        try:
            e_dt = datetime.fromisoformat(end_date)
            stmt = stmt.where(Payment.created_at <= e_dt)
        except Exception:
            pass
    stmt = stmt.offset((page - 1) * limit).limit(limit)
    res = await db.execute(stmt)
    payments = res.scalars().all()
    user_ids = {p.user_id for p in payments if p.user_id}
    users_map = {}
    if user_ids:
        u_res = await db.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
        users_map = {uid: uname for uid, uname in u_res.all()}

    out = []
    for p in payments:
        resp = PaymentResponse.model_validate(p)
        resp.user_id = p.user_id
        resp.username = users_map.get(p.user_id) or (f"user_{p.user_id}" if p.user_id else "guest")
        out.append(resp)
    return ApiResponse(data=out)


@router.post("/payments/sync-mbbank", response_model=ApiResponse[dict])
@router.post("/banking/sync", response_model=ApiResponse[dict])
async def admin_sync_mbbank(db: AsyncSession = Depends(get_db)):
    """Run an on-demand bank sync without exposing credentials or raw bank data."""
    try:
        result = await BankSyncService(db).sync_mbbank()
    except Exception as exc:
        err_msg = str(exc)
        raw_cfg = await PaymentService(db).get_banking_config()
        token = raw_cfg.get("token") or ""
        if token and token in err_msg:
            err_msg = err_msg.replace(token, "••••••••")
        logger.error("Admin Bank sync failed: %s", err_msg)
        raise HTTPException(status_code=502, detail="Không thể đồng bộ giao dịch ngân hàng. Vui lòng kiểm tra lại cấu hình kết nối.")
    return ApiResponse(data=result, message=result.get("message", "Đã hoàn thành đồng bộ giao dịch ngân hàng."))

@router.post("/banking/test-fetch", response_model=ApiResponse[dict])
async def admin_test_banking_fetch(
    payload: Optional[dict] = None,
    db: AsyncSession = Depends(get_db)
):
    try:
        result = await BankSyncService(db).test_fetch_transactions(custom_config=payload)
    except Exception as exc:
        err_msg = str(exc)
        logger.error("Admin bank test fetch failed: %s", err_msg)
        return ApiResponse(
            data={"success": False, "error": err_msg, "total_found": 0, "transactions": []},
            message=f"Lỗi khi kiểm tra kết nối ngân hàng: {err_msg}",
            success=False
        )
    return ApiResponse(
        data=result,
        message=result.get("message", "Đã kiểm tra lịch sử giao dịch thành công.")
    )

@router.get("/banking/config", response_model=ApiResponse[BankingConfigResponse])
async def admin_get_banking_config(db: AsyncSession = Depends(get_db)):
    """Get masked bank configuration for auto-deposits."""
    service = PaymentService(db)
    raw_cfg = await service.get_banking_config()

    has_token = bool(raw_cfg.get("token"))
    has_internal_password = bool(raw_cfg.get("internal_mb_password"))
    has_cron_secret = bool(raw_cfg.get("cron_secret"))

    bank_name = str(raw_cfg.get("bank_name") or "MB Bank").strip()
    bank_code = str(raw_cfg.get("bank_code") or "MBBANK").strip()
    bank_bin = str(raw_cfg.get("bank_bin") or "970422").strip()
    account_number = str(raw_cfg.get("account_number") or "").strip()
    account_name = str(raw_cfg.get("account_name") or "").strip()
    content_prefix = str(raw_cfg.get("content_prefix") or "NAP").strip().upper()
    template = str(raw_cfg.get("vietqr_template") or "compact2").strip()
    min_dep = int(raw_cfg.get("min_deposit") or 10000)

    qr_preview = VietQRHelper.generate_qr_url(
        bank_name=bank_name,
        bank_code=bank_code,
        account_no=account_number or "0000000000",
        amount=50000,
        memo=f"{content_prefix} DEMO123",
        template=template,
        account_holder=account_name
    )

    data = BankingConfigResponse(
        enabled=bool(raw_cfg.get("enabled")),
        api_type=str(raw_cfg.get("api_type") or "thueapi"),
        bank_name=bank_name,
        bank_code=bank_code,
        bank_bin=bank_bin,
        account_number=account_number,
        account_name=account_name,
        has_token=has_token,
        token_masked="●●●●●●●●" if has_token else "",
        internal_mb_username=str(raw_cfg.get("internal_mb_username") or "").strip(),
        has_internal_password=has_internal_password,
        internal_password_masked="●●●●●●●●" if has_internal_password else "",
        content_prefix=content_prefix,
        min_deposit=min_dep,
        vietqr_template=template,
        has_cron_secret=has_cron_secret,
        cron_secret_masked="●●●●●●●●" if has_cron_secret else "",
        cron_secret=str(raw_cfg.get("cron_secret") or "").strip(),
        vietqr_preview_url=qr_preview
    )
    return ApiResponse(data=data)

@router.put("/banking/config", response_model=ApiResponse[BankingConfigResponse])
async def admin_update_banking_config(
    payload: BankingConfigUpdateRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Update bank configuration securely, keeping credentials protected."""
    service = PaymentService(db)
    current_cfg = await service.get_banking_config()
    cfg = dict(current_cfg)

    if payload.enabled is not None:
        cfg["enabled"] = bool(payload.enabled)
    if payload.api_type is not None:
        cfg["api_type"] = "internal" if payload.api_type == "internal" else "thueapi"
    if payload.bank_name is not None:
        cfg["bank_name"] = payload.bank_name.strip()[:100]
    if payload.bank_code is not None:
        cfg["bank_code"] = "".join(c for c in payload.bank_code.upper() if c.isalnum() or c == "_")[:30]
    if payload.bank_bin is not None:
        cfg["bank_bin"] = "".join(c for c in payload.bank_bin if c.isdigit())[:20]
    if payload.account_number is not None:
        cfg["account_number"] = "".join(c for c in payload.account_number if c.isalnum())[:40]
    if payload.account_name is not None:
        cfg["account_name"] = payload.account_name.strip().upper()[:100]
    if payload.content_prefix is not None:
        cfg["content_prefix"] = "".join(c for c in payload.content_prefix.upper() if c.isalnum())[:20] or "NAP"
    if payload.min_deposit is not None:
        cfg["min_deposit"] = max(1000, min(100000000, int(payload.min_deposit)))
    if payload.vietqr_template is not None:
        cfg["vietqr_template"] = "".join(c for c in payload.vietqr_template.lower() if c.isalnum() or c == "_")[:30] or "compact2"

    # Token handling
    if payload.clear_token:
        cfg["token"] = ""
    elif payload.token and payload.token.strip():
        cfg["token"] = payload.token.strip()

    # Internal credentials handling
    if payload.internal_mb_username is not None:
        cfg["internal_mb_username"] = payload.internal_mb_username.strip()[:100]
    if payload.clear_internal_mb_password:
        cfg["internal_mb_password"] = ""
    elif payload.internal_mb_password and payload.internal_mb_password.strip():
        cfg["internal_mb_password"] = payload.internal_mb_password.strip()

    # Cron secret handling
    if payload.clear_cron_secret:
        cfg["cron_secret"] = ""
    elif payload.cron_secret and payload.cron_secret.strip():
        cfg["cron_secret"] = payload.cron_secret.strip()

    # Validation when enabled
    if cfg.get("enabled"):
        if not cfg.get("account_number") or not cfg.get("account_name"):
            raise HTTPException(status_code=400, detail="Bật nạp tiền tự động yêu cầu phải có Số tài khoản và Tên chủ tài khoản.")
        if cfg.get("api_type") == "thueapi" and not cfg.get("token") and not (settings.MB_BANK_API_URL and settings.MB_BANK_API_TOKEN):
            raise HTTPException(status_code=400, detail="Dùng ThueAPI yêu cầu nhập Token MB Bank.")
        if cfg.get("api_type") == "internal" and (not cfg.get("internal_mb_username") or not cfg.get("internal_mb_password")):
            raise HTTPException(status_code=400, detail="Dùng API Nội bộ yêu cầu nhập Tên đăng nhập và Mật khẩu MBBank.")

    # Save to SystemSetting table
    val_json = json.dumps(cfg, ensure_ascii=False)
    setting_res = await db.execute(select(SystemSetting).where(SystemSetting.key == "banking_config"))
    setting_row = setting_res.scalar_one_or_none()
    if setting_row:
        setting_row.value = val_json
        setting_row.updated_at = datetime.utcnow()
    else:
        db.add(SystemSetting(
            key="banking_config",
            value=val_json,
            description="Cấu hình banking MB Bank & nạp tiền tự động",
            updated_at=datetime.utcnow()
        ))

    # Synchronize non-secret mirror settings for global queries
    mirror_map = {
        "bank_name": cfg.get("bank_name", "MB Bank"),
        "bank_account_number": cfg.get("account_number", ""),
        "bank_account_holder": cfg.get("account_name", ""),
        "min_deposit_amount": str(cfg.get("min_deposit", 10000)),
    }
    for m_k, m_v in mirror_map.items():
        m_res = await db.execute(select(SystemSetting).where(SystemSetting.key == m_k))
        m_row = m_res.scalar_one_or_none()
        if m_row:
            m_row.value = str(m_v)
            m_row.updated_at = datetime.utcnow()
        else:
            db.add(SystemSetting(key=m_k, value=str(m_v), description=f"Cài đặt hiển thị {m_k}", updated_at=datetime.utcnow()))

    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="UPDATE_BANKING_CONFIG",
        target_type="SYSTEM",
        details=f"Cập nhật cấu hình Banking: Ngân hàng {cfg.get('bank_name')}, STK {cfg.get('account_number')}, Bật: {cfg.get('enabled')}"
    )
    db.add(audit)
    await db.commit()

    return await admin_get_banking_config(db)

@router.get("/banking/stats", response_model=ApiResponse[BankingStatsResponse])
async def admin_get_banking_stats(db: AsyncSession = Depends(get_db)):
    """Get banking auto-deposit metrics."""
    from sqlalchemy import func
    res_total = await db.execute(select(func.count(BankTransaction.id)))
    total = res_total.scalar() or 0

    res_proc = await db.execute(
        select(func.count(BankTransaction.id), func.coalesce(func.sum(BankTransaction.amount), 0))
        .where(BankTransaction.status == "PROCESSED")
    )
    proc_row = res_proc.first()
    credited_count = (proc_row[0] if proc_row else 0) or 0
    credited_amount = float(proc_row[1] if proc_row else 0)

    res_ign = await db.execute(
        select(func.count(BankTransaction.id)).where(BankTransaction.status == "IGNORED")
    )
    ignored_count = res_ign.scalar() or 0

    res_fail = await db.execute(
        select(func.count(BankTransaction.id)).where(BankTransaction.status == "FAILED")
    )
    failed_count = res_fail.scalar() or 0

    stats = BankingStatsResponse(
        total=total,
        credited_count=credited_count,
        credited_amount=credited_amount,
        ignored_count=ignored_count,
        failed_count=failed_count
    )
    return ApiResponse(data=stats)

@router.get("/banking/transactions", response_model=ApiResponse[List[BankTransactionItemResponse]])
async def admin_get_banking_transactions(
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """List recent raw bank transactions recorded by sync/webhook."""
    stmt = select(BankTransaction).order_by(desc(BankTransaction.created_at)).limit(limit)
    res = await db.execute(stmt)
    txs = res.scalars().all()
    return ApiResponse(data=[BankTransactionItemResponse.model_validate(tx) for tx in txs])

@router.post("/payments/{id}/approve", response_model=ApiResponse[dict])
async def admin_approve_payment(
    id: int,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Payment).where(Payment.id == id).with_for_update())
    payment = res.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu nạp tiền.")
    if payment.status in {"SUCCESS", "COMPLETED"}:
        return ApiResponse(data={"status": "SUCCESS"}, message="Giao dịch này đã được duyệt trước đó.")

    u_res = await db.execute(select(User).where(User.id == payment.user_id).with_for_update())
    user = u_res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="Người dùng không tồn tại.")

    balance_before = user.balance
    user.balance += payment.amount
    payment.status = "COMPLETED"
    payment.completed_at = datetime.utcnow()

    tx = Transaction(
        user_id=user.id,
        type="DEPOSIT",
        amount=payment.amount,
        balance_before=balance_before,
        balance_after=user.balance,
        reference=payment.transaction_code,
        description=f"Admin {admin.username} duyệt nạp tiền: {payment.amount:,.0f}đ",
        status="SUCCESS",
        created_at=datetime.utcnow()
    )
    db.add(tx)

    notif = Notification(
        user_id=user.id,
        title="Nạp tiền thành công",
        message=f"Giao dịch {payment.transaction_code} ({payment.amount:,.0f}đ) đã được Admin duyệt và cộng vào ví.",
        type="SUCCESS",
        created_at=datetime.utcnow()
    )
    db.add(notif)

    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="APPROVE_PAYMENT",
        target_type="PAYMENT",
        target_id=str(payment.id),
        details=f"Duyệt nạp tiền {payment.amount:,.0f}đ cho user {user.username} ({payment.transaction_code})"
    )
    db.add(audit)
    await db.commit()
    return ApiResponse(
        data={"payment_id": payment.id, "amount": payment.amount, "user": user.username, "new_balance": user.balance},
        message="Đã duyệt nạp tiền thành công!"
    )

# 11. Transactions Management
@router.get("/transactions", response_model=ApiResponse[List[TransactionResponse]])
async def admin_list_transactions(
    type: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    start_date: Optional[str] = Query(None),
    end_date: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(Transaction).order_by(desc(Transaction.created_at))
    if type and type.upper() != "ALL":
        stmt = stmt.where(Transaction.type == type.upper())
    search = sanitize_search_query(search)
    if search:
        stmt = stmt.where((Transaction.description.ilike(f"%{search}%")) | (Transaction.reference.ilike(f"%{search}%")))
    if start_date:
        try:
            s_dt = datetime.fromisoformat(start_date)
            stmt = stmt.where(Transaction.created_at >= s_dt)
        except Exception:
            pass
    if end_date:
        try:
            e_dt = datetime.fromisoformat(end_date)
            stmt = stmt.where(Transaction.created_at <= e_dt)
        except Exception:
            pass
    stmt = stmt.offset((page - 1) * limit).limit(limit)
    res = await db.execute(stmt)
    txs = res.scalars().all()
    user_ids = {t.user_id for t in txs if t.user_id}
    users_map = {}
    if user_ids:
        u_res = await db.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
        users_map = {uid: uname for uid, uname in u_res.all()}

    out = []
    for t in txs:
        resp = TransactionResponse.model_validate(t)
        resp.username = users_map.get(t.user_id) or (f"user_{t.user_id}" if t.user_id else "system")
        out.append(resp)
    return ApiResponse(data=out)

# 12. Support Tickets
@router.get("/public-settings", response_model=ApiResponse[Dict[str, str]])
async def admin_get_public_settings(db: AsyncSession = Depends(get_db)):
    public_keys = [
        "site_name", "site_title", "site_description", "currency_symbol",
        "maintenance_mode", "registration_enabled",
        "zalo_url", "popup_enabled", "popup_title", "popup_content",
    ]
    res = await db.execute(select(SystemSetting).where(SystemSetting.key.in_(public_keys)))
    rows = res.scalars().all()
    settings_dict = {r.key: r.value for r in rows}
    defaults = {
        "site_name": "TangLike SMM",
        "site_title": "TangLike - SMM SaaS Panel Đa Dịch Vụ Mạng Xã Hội",
        "site_description": "Hệ thống tăng tương tác Facebook, TikTok, YouTube, Instagram tự động 24/7.",
        "currency_symbol": "đ",
        "maintenance_mode": "false",
        "registration_enabled": "true",
        "zalo_url": "https://zalo.me/0868133346",
        "popup_enabled": "false",
        "popup_title": "Thông Báo Từ Hệ Thống TangLike PRO",
        "popup_content": "Chào mừng quý khách đến với TangLike! Hệ thống nạp tiền tự động qua QR MB Bank và kích hoạt dịch vụ 24/7 siêu tốc.",
    }
    for k, v in defaults.items():
        if k not in settings_dict:
            settings_dict[k] = v
    return ApiResponse(data=settings_dict)

@router.get("/tickets", response_model=ApiResponse[List[SupportConversationResponse]])
async def admin_list_tickets(
    status: Optional[str] = Query(None),
    customer_type: Optional[str] = Query(None),
    product_status: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(SupportConversation).order_by(desc(SupportConversation.updated_at))
    if status and status.upper() != "ALL":
        if status.upper() == "HIDDEN":
            stmt = stmt.where((SupportConversation.status == "HIDDEN") | (SupportConversation.is_hidden == True))
        else:
            stmt = stmt.where(SupportConversation.status == status.upper(), (SupportConversation.is_hidden == False) | (SupportConversation.is_hidden == None))

    if customer_type and customer_type.upper() != "ALL":
        stmt = stmt.where(SupportConversation.customer_type == customer_type.upper())
    if product_status and product_status.upper() != "ALL":
        stmt = stmt.where(SupportConversation.product_status == product_status.upper())

    res = await db.execute(stmt)
    convs = res.scalars().all()
    output = []
    for c in convs:
        m_stmt = select(SupportMessage).where(SupportMessage.conversation_id == c.id).order_by(desc(SupportMessage.created_at)).limit(1)
        m_res = await db.execute(m_stmt)
        last_m = m_res.scalar_one_or_none()

        user_obj = None
        if c.user_id and c.user_id != 0:
            u_res = await db.execute(select(User).where(User.id == c.user_id))
            user_obj = u_res.scalar_one_or_none()

        user_balance = float(user_obj.balance) if user_obj else 0.0
        user_email = user_obj.email if user_obj else c.guest_email
        user_phone = user_obj.phone if user_obj else None
        user_full_name = user_obj.full_name if user_obj else c.guest_name
        last_active = user_obj.last_login_at or user_obj.updated_at if user_obj else None

        # Check online: active in last 15 minutes (900 seconds)
        is_online = False
        if last_active:
            delta = (datetime.utcnow() - last_active).total_seconds()
            if delta < 900:
                is_online = True

        # Total deposited & orders
        total_dep = 0.0
        total_ord = 0
        if c.user_id and c.user_id != 0:
            dep_res = await db.execute(
                select(func.sum(Payment.amount)).where(
                    Payment.user_id == c.user_id,
                    Payment.status == "COMPLETED"
                )
            )
            total_dep = float(dep_res.scalar() or 0.0)

            ord_res = await db.execute(
                select(func.count(Order.id)).where(Order.user_id == c.user_id)
            )
            total_ord = int(ord_res.scalar() or 0)

        # Keyword filtering if q
        q = sanitize_search_query(q)
        if q:
            term = q.lower().strip()
            name_match = (
                (user_full_name and term in user_full_name.lower()) or
                (user_obj and term in user_obj.username.lower()) or
                (c.guest_name and term in c.guest_name.lower())
            )
            msg_match = (
                (last_m and term in last_m.message.lower()) or
                (term in c.subject.lower())
            )
            email_match = (user_email and term in user_email.lower())
            if not (name_match or msg_match or email_match):
                continue

        default_customer_type = "GUEST" if (not c.user_id or c.user_id == 0) else ("VIP" if total_dep >= 1000000 else "REGULAR")
        output.append(SupportConversationResponse(
            id=c.id,
            user_id=c.user_id if (c.user_id and c.user_id != 0) else None,
            username=user_obj.username if user_obj else (c.guest_name or "Khách vãng lai"),
            user_name=user_obj.username if user_obj else (c.guest_name or "Khách vãng lai"),
            user_full_name=user_full_name,
            user_email=user_email,
            user_phone=user_phone,
            user_balance=user_balance,
            total_deposited=total_dep,
            total_orders=total_ord,
            is_online=is_online,
            last_active_at=last_active,
            subject=c.subject,
            status=c.status,
            assigned_admin=c.assigned_admin,
            customer_type=c.customer_type or default_customer_type,
            product_status=c.product_status or "PENDING",
            tags=c.tags or "",
            guest_name=c.guest_name,
            guest_email=c.guest_email,
            created_at=c.created_at,
            updated_at=c.updated_at,
            last_message=last_m.message if last_m else None,
            last_message_at=last_m.created_at if last_m else None,
            is_hidden=bool(getattr(c, "is_hidden", False) or c.status == "HIDDEN")
        ))
    return ApiResponse(data=output)

@router.post("/tickets/bulk-delete", response_model=ApiResponse[int])
async def admin_bulk_delete_tickets(
    payload: BulkTicketActionRequest,
    db: AsyncSession = Depends(get_db)
):
    if not payload.ticket_ids:
        return ApiResponse(data=0, message="Không có cuộc trò chuyện nào được chọn.")
    await db.execute(delete(SupportMessage).where(SupportMessage.conversation_id.in_(payload.ticket_ids)))
    res = await db.execute(delete(SupportConversation).where(SupportConversation.id.in_(payload.ticket_ids)))
    await db.commit()
    count = res.rowcount if res.rowcount is not None and res.rowcount > 0 else len(payload.ticket_ids)
    return ApiResponse(data=count, message=f"Đã xóa thành công {count} cuộc trò chuyện!")

@router.post("/tickets/delete-all", response_model=ApiResponse[int])
async def admin_delete_all_tickets(
    db: AsyncSession = Depends(get_db)
):
    await db.execute(delete(SupportMessage))
    res = await db.execute(delete(SupportConversation))
    await db.commit()
    count = res.rowcount if res.rowcount is not None else 0
    return ApiResponse(data=count, message="Đã xóa toàn bộ các cuộc trò chuyện và tin nhắn thành công!")

@router.post("/tickets/bulk-status", response_model=ApiResponse[int])
async def admin_bulk_update_ticket_status(
    payload: BulkTicketStatusRequest,
    db: AsyncSession = Depends(get_db)
):
    if not payload.ticket_ids:
        return ApiResponse(data=0, message="Không có cuộc trò chuyện nào được chọn.")
    values = {"updated_at": datetime.utcnow()}
    if payload.status:
        values["status"] = payload.status.upper()
    if payload.customer_type:
        values["customer_type"] = payload.customer_type.upper()
    if payload.product_status:
        values["product_status"] = payload.product_status.upper()
    if payload.is_hidden is not None:
        values["is_hidden"] = payload.is_hidden
        if payload.is_hidden:
            values["status"] = "HIDDEN"
    res = await db.execute(
        update(SupportConversation)
        .where(SupportConversation.id.in_(payload.ticket_ids))
        .values(**values)
    )
    await db.commit()
    count = res.rowcount if res.rowcount is not None and res.rowcount > 0 else len(payload.ticket_ids)
    return ApiResponse(data=count, message=f"Đã cập nhật trạng thái {count} cuộc trò chuyện!")

@router.put("/tickets/{id}/status", response_model=ApiResponse[bool])
async def admin_update_ticket_status(
    id: int,
    payload: TicketStatusUpdate,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(SupportConversation).where(SupportConversation.id == id))
    conv = res.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện.")
    if payload.status:
        conv.status = payload.status.upper()
    if payload.customer_type is not None:
        conv.customer_type = payload.customer_type.upper()
    if payload.product_status is not None:
        conv.product_status = payload.product_status.upper()
    if payload.tags is not None:
        conv.tags = payload.tags
    conv.updated_at = datetime.utcnow()
    await db.commit()
    return ApiResponse(data=True, message="Đã cập nhật thông tin cuộc trò chuyện thành công!")

@router.delete("/tickets/{id}", response_model=ApiResponse[bool])
async def admin_delete_ticket(
    id: int,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(SupportConversation).where(SupportConversation.id == id))
    conv = res.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện.")
    await db.execute(delete(SupportMessage).where(SupportMessage.conversation_id == id))
    await db.delete(conv)
    await db.commit()
    return ApiResponse(data=True, message="Đã xóa cuộc trò chuyện và toàn bộ tin nhắn thành công!")

@router.put("/tickets/{id}/toggle-hide", response_model=ApiResponse[bool])
async def admin_toggle_hide_ticket(
    id: int,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(SupportConversation).where(SupportConversation.id == id))
    conv = res.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Không tìm thấy cuộc trò chuyện.")
    
    current_hidden = bool(getattr(conv, "is_hidden", False) or conv.status == "HIDDEN")
    new_hidden = not current_hidden
    conv.is_hidden = new_hidden
    if new_hidden:
        conv.status = "HIDDEN"
    elif conv.status == "HIDDEN":
        conv.status = "OPEN"
    conv.updated_at = datetime.utcnow()
    await db.commit()
    msg = "Đã ẩn cuộc trò chuyện thành công!" if new_hidden else "Đã bỏ ẩn cuộc trò chuyện!"
    return ApiResponse(data=new_hidden, message=msg)

@router.delete("/tickets/messages/{message_id}", response_model=ApiResponse[bool])
async def admin_delete_ticket_message(
    message_id: int,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(SupportMessage).where(SupportMessage.id == message_id))
    msg = res.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Không tìm thấy tin nhắn.")
    await db.delete(msg)
    await db.commit()
    return ApiResponse(data=True, message="Đã xóa tin nhắn thành công!")

@router.put("/tickets/messages/{message_id}/toggle-hide", response_model=ApiResponse[bool])
async def admin_toggle_hide_ticket_message(
    message_id: int,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(SupportMessage).where(SupportMessage.id == message_id))
    msg = res.scalar_one_or_none()
    if not msg:
        raise HTTPException(status_code=404, detail="Không tìm thấy tin nhắn.")
    current_hidden = bool(getattr(msg, "is_hidden", False))
    msg.is_hidden = not current_hidden
    await db.commit()
    info = "Đã ẩn tin nhắn!" if msg.is_hidden else "Đã hiển thị lại tin nhắn!"
    return ApiResponse(data=bool(msg.is_hidden), message=info)

@router.post("/tickets/messages/bulk-delete", response_model=ApiResponse[int])
async def admin_bulk_delete_messages(
    payload: BulkMessageActionRequest,
    db: AsyncSession = Depends(get_db)
):
    if not payload.message_ids:
        return ApiResponse(data=0, message="Không có tin nhắn nào được chọn.")
    res = await db.execute(delete(SupportMessage).where(SupportMessage.id.in_(payload.message_ids)))
    await db.commit()
    count = res.rowcount if res.rowcount is not None and res.rowcount > 0 else len(payload.message_ids)
    return ApiResponse(data=count, message=f"Đã xóa thành công {count} tin nhắn!")

@router.post("/tickets/{id}/clear-messages", response_model=ApiResponse[int])
async def admin_clear_conversation_messages(
    id: int,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(delete(SupportMessage).where(SupportMessage.conversation_id == id))
    await db.commit()
    count = res.rowcount if res.rowcount is not None else 0
    return ApiResponse(data=count, message="Đã xóa toàn bộ tin nhắn trong cuộc trò chuyện này!")

@router.post("/tickets/messages/clear-all", response_model=ApiResponse[int])
async def admin_clear_all_system_messages(
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(delete(SupportMessage))
    await db.commit()
    count = res.rowcount if res.rowcount is not None else 0
    return ApiResponse(data=count, message="Đã xóa toàn bộ tin nhắn trong toàn hệ thống!")

# 13. System Settings
@router.get("/settings", response_model=ApiResponse[Dict[str, str]])
async def admin_get_settings(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SystemSetting).where(SystemSetting.key.in_(PUBLIC_SYSTEM_SETTING_KEYS)))
    rows = res.scalars().all()
    settings_dict = {r.key: r.value for r in rows}
    return ApiResponse(data=settings_dict)

@router.put("/settings", response_model=ApiResponse[Dict[str, str]])
async def admin_update_settings(
    payload: SystemSettingsUpdateRequest,
    request: Request,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    unsupported = set(payload.settings) - PUBLIC_SYSTEM_SETTING_KEYS
    if unsupported:
        raise HTTPException(
            status_code=400,
            detail="Cài đặt không được phép hoặc chứa dữ liệu nhạy cảm: " + ", ".join(sorted(unsupported)),
        )
    if "zalo_url" in payload.settings:
        zalo_val = str(payload.settings["zalo_url"] or "").strip()
        if zalo_val and not is_safe_presentation_url(zalo_val):
            raise HTTPException(
                status_code=400,
                detail="zalo_url không hợp lệ hoặc chứa scheme nguy hiểm (XSS detected)."
            )
    changed_keys = []
    for k, v in payload.settings.items():
        res = await db.execute(select(SystemSetting).where(SystemSetting.key == k))
        setting = res.scalar_one_or_none()
        str_val = str(v)
        if setting:
            if setting.value != str_val:
                changed_keys.append(f"{k}: '{setting.value}' -> '{str_val}'")
                setting.value = str_val
                setting.updated_at = datetime.utcnow()
        else:
            changed_keys.append(f"{k}: (mới) '{str_val}'")
            new_s = SystemSetting(key=k, value=str_val, description="Tùy chỉnh hệ thống", updated_at=datetime.utcnow())
            db.add(new_s)

    if changed_keys:
        client_ip = request.client.host if request.client else None
        details_str = f"Thay đổi ({len(changed_keys)} mục): " + "; ".join(changed_keys[:4])
        if len(changed_keys) > 4:
            details_str += f"... (+{len(changed_keys) - 4} mục)"
        audit = AuditLog(
            user_id=admin.id,
            username=admin.username,
            action="UPDATE_SYSTEM_SETTINGS",
            target_type="SYSTEM",
            ip_address=client_ip,
            details=details_str
        )
        db.add(audit)
    await db.commit()

    if "ttc_rate_divider" in payload.settings or "ttc_markup_percent" in payload.settings:
        from app.routers.admin_providers import admin_recalculate_ttc_prices
        try:
            await admin_recalculate_ttc_prices(admin=admin, db=db)
        except Exception as e:
            logger.warning(f"Auto-recalculate TTC prices failed: {e}")
    res_all = await db.execute(select(SystemSetting).where(SystemSetting.key.in_(PUBLIC_SYSTEM_SETTING_KEYS)))
    all_rows = res_all.scalars().all()
    return ApiResponse(data={r.key: r.value for r in all_rows}, message="Đã lưu cài đặt hệ thống thành công.")

class TelegramSettingsUpdateRequest(BaseModel):
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    telegram_notifications: Optional[str] = None

@router.get("/settings/telegram", response_model=ApiResponse[dict])
async def admin_get_telegram_settings(
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    from app.config.settings import settings as app_settings
    res = await db.execute(select(SystemSetting).where(
        SystemSetting.key.in_(["telegram_bot_token", "telegram_admin_chat_id", "telegram_notifications"])
    ))
    rows = {r.key: r.value for r in res.scalars().all()}
    
    raw_token = rows.get("telegram_bot_token") or app_settings.TELEGRAM_BOT_TOKEN or ""
    chat_id = rows.get("telegram_admin_chat_id") or app_settings.TELEGRAM_ADMIN_CHAT_ID or ""
    enabled = rows.get("telegram_notifications", "true")
    
    masked_token = ""
    if raw_token:
        if len(raw_token) > 8:
            masked_token = f"{raw_token[:4]}••••••••{raw_token[-4:]}"
        else:
            masked_token = "••••••••"
            
    return ApiResponse(data={
        "bot_token": masked_token,
        "is_configured": bool(raw_token and chat_id),
        "chat_id": chat_id,
        "telegram_notifications": enabled,
    })

@router.put("/settings/telegram", response_model=ApiResponse[dict])
async def admin_update_telegram_settings(
    payload: TelegramSettingsUpdateRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    if payload.bot_token is not None:
        clean_tok = payload.bot_token.strip()
        if clean_tok and not clean_tok.startswith("••••") and "••••" not in clean_tok:
            res = await db.execute(select(SystemSetting).where(SystemSetting.key == "telegram_bot_token"))
            s = res.scalar_one_or_none()
            if s:
                s.value = clean_tok
                s.updated_at = datetime.utcnow()
            else:
                db.add(SystemSetting(key="telegram_bot_token", value=clean_tok, description="Telegram Bot Token", updated_at=datetime.utcnow()))

    if payload.chat_id is not None:
        clean_chat = payload.chat_id.strip()
        res = await db.execute(select(SystemSetting).where(SystemSetting.key == "telegram_admin_chat_id"))
        s = res.scalar_one_or_none()
        if s:
            s.value = clean_chat
            s.updated_at = datetime.utcnow()
        else:
            db.add(SystemSetting(key="telegram_admin_chat_id", value=clean_chat, description="Telegram Admin Chat ID", updated_at=datetime.utcnow()))

    if payload.telegram_notifications is not None:
        res = await db.execute(select(SystemSetting).where(SystemSetting.key == "telegram_notifications"))
        s = res.scalar_one_or_none()
        if s:
            s.value = str(payload.telegram_notifications).lower()
            s.updated_at = datetime.utcnow()
        else:
            db.add(SystemSetting(key="telegram_notifications", value=str(payload.telegram_notifications).lower(), description="Bật thông báo Telegram", updated_at=datetime.utcnow()))

    db.add(AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="UPDATE_TELEGRAM_SETTINGS",
        target_type="SYSTEM",
        details="Cập nhật cấu hình Telegram Bot & Admin Chat ID"
    ))
    await db.commit()

    return ApiResponse(data={"success": True}, message="Cập nhật cấu hình Telegram thành công.")

@router.post("/settings/test-telegram", response_model=ApiResponse[dict])
async def admin_test_telegram(
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    from app.notifications.telegram import TelegramNotifier
    bot_token, chat_id = await TelegramNotifier.get_telegram_config()
    if not bot_token or not chat_id:
        raise HTTPException(
            status_code=400,
            detail="Chưa cấu hình Telegram Bot Token hoặc Chat ID. Vui lòng nhập thông tin trước khi kiểm tra."
        )
    test_msg = (
        f"🔔 <b>THÔNG BÁO KIỂM TRA TỪ TANGLIKE</b>\n\n"
        f"✅ Kết nối Telegram Bot thành công!\n"
        f"👤 Admin: <b>{admin.username}</b>\n"
        f"⏰ Thời gian: <b>{datetime.utcnow().strftime('%H:%M:%S - %d/%m/%Y')}</b>\n\n"
        f"💬 Hệ thống Live Chat trên website đã sẵn sàng chuyển tiếp tin nhắn trực tiếp tới Telegram này!"
    )
    ok = await TelegramNotifier.send_message(test_msg)
    if not ok:
        raise HTTPException(
            status_code=400,
            detail="Không thể gửi tin nhắn qua Telegram Bot. Vui lòng kiểm tra lại Bot Token và Chat ID (đảm bảo bạn đã bấm /start với bot)."
        )
    return ApiResponse(data={"sent": True}, message="Gửi tin nhắn thử nghiệm tới Telegram thành công!")

# 13.1 Data Retention & Automated Cleanup
@router.get("/maintenance/cleanup-stats", response_model=ApiResponse[dict])
async def admin_get_cleanup_stats(
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import and_
    now = datetime.utcnow()

    # Get settings
    st_res = await db.execute(select(SystemSetting).where(SystemSetting.key.in_([
        "cleanup_auto_enabled",
        "retention_audit_logs_days",
        "retention_job_runs_days",
        "retention_price_sync_days",
        "retention_reset_tokens_days",
        "retention_read_notifications_days",
        "retention_stale_payments_days",
        "cleanup_last_run",
        "cleanup_last_summary",
    ])))
    st_dict = {s.key: s.value for s in st_res.scalars().all()}

    audit_days = int(st_dict.get("retention_audit_logs_days", 60))
    job_days = int(st_dict.get("retention_job_runs_days", 30))
    price_days = int(st_dict.get("retention_price_sync_days", 60))
    token_days = int(st_dict.get("retention_reset_tokens_days", 7))
    notif_days = int(st_dict.get("retention_read_notifications_days", 30))
    pay_days = int(st_dict.get("retention_stale_payments_days", 14))

    # Total counts in DB
    total_audits = (await db.execute(select(func.count(AuditLog.id)))).scalar() or 0
    total_jobs = (await db.execute(select(func.count(JobRun.id)))).scalar() or 0
    total_price_sync = (await db.execute(select(func.count(PriceSyncLog.id)))).scalar() or 0
    total_tokens = (await db.execute(select(func.count(PasswordResetToken.id)))).scalar() or 0
    total_read_notifs = (await db.execute(select(func.count(Notification.id)).where(Notification.is_read.is_(True)))).scalar() or 0
    total_pending_pays = (await db.execute(select(func.count(Payment.id)).where(Payment.status == "PENDING"))).scalar() or 0

    # Eligible for cleanup based on current retention
    exp_audits = (await db.execute(select(func.count(AuditLog.id)).where(AuditLog.created_at < now - timedelta(days=audit_days)))).scalar() or 0
    exp_jobs = (await db.execute(select(func.count(JobRun.id)).where(JobRun.completed_at < now - timedelta(days=job_days)))).scalar() or 0
    exp_price = (await db.execute(select(func.count(PriceSyncLog.id)).where(PriceSyncLog.created_at < now - timedelta(days=price_days)))).scalar() or 0
    exp_tokens = (await db.execute(select(func.count(PasswordResetToken.id)).where(PasswordResetToken.expires_at < now - timedelta(days=token_days)))).scalar() or 0
    exp_notifs = (await db.execute(select(func.count(Notification.id)).where(and_(Notification.is_read.is_(True), Notification.created_at < now - timedelta(days=notif_days))))).scalar() or 0
    exp_pays = (await db.execute(select(func.count(Payment.id)).where(and_(Payment.status == "PENDING", Payment.created_at < now - timedelta(days=pay_days))))).scalar() or 0

    return ApiResponse(data={
        "settings": {
            "cleanup_auto_enabled": st_dict.get("cleanup_auto_enabled", "true").lower() == "true",
            "retention_audit_logs_days": audit_days,
            "retention_job_runs_days": job_days,
            "retention_price_sync_days": price_days,
            "retention_reset_tokens_days": token_days,
            "retention_read_notifications_days": notif_days,
            "retention_stale_payments_days": pay_days,
            "cleanup_last_run": st_dict.get("cleanup_last_run"),
            "cleanup_last_summary": st_dict.get("cleanup_last_summary", "Chưa chạy dọn dẹp lần nào"),
        },
        "totals": {
            "audit_logs": total_audits,
            "job_runs": total_jobs,
            "price_sync_logs": total_price_sync,
            "expired_tokens": total_tokens,
            "read_notifications": total_read_notifs,
            "pending_payments": total_pending_pays,
        },
        "cleanable": {
            "audit_logs": exp_audits,
            "job_runs": exp_jobs,
            "price_sync_logs": exp_price,
            "expired_tokens": exp_tokens,
            "read_notifications": exp_notifs,
            "pending_payments": exp_pays,
            "total_cleanable": exp_audits + exp_jobs + exp_price + exp_tokens + exp_notifs + exp_pays,
        }
    })

@router.post("/maintenance/cleanup-now", response_model=ApiResponse[dict])
async def admin_trigger_cleanup_now(
    payload: Optional[CleanupNowRequest] = None,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import and_
    now = datetime.utcnow()

    # Load defaults from settings
    st_res = await db.execute(select(SystemSetting).where(SystemSetting.key.in_([
        "retention_audit_logs_days",
        "retention_job_runs_days",
        "retention_price_sync_days",
        "retention_reset_tokens_days",
        "retention_read_notifications_days",
        "retention_stale_payments_days",
    ])))
    st_dict = {s.key: s.value for s in st_res.scalars().all()}

    audit_days = (payload.audit_logs_days if payload and payload.audit_logs_days is not None else int(st_dict.get("retention_audit_logs_days", 60)))
    job_days = (payload.job_runs_days if payload and payload.job_runs_days is not None else int(st_dict.get("retention_job_runs_days", 30)))
    price_days = (payload.price_sync_days if payload and payload.price_sync_days is not None else int(st_dict.get("retention_price_sync_days", 60)))
    token_days = (payload.reset_tokens_days if payload and payload.reset_tokens_days is not None else int(st_dict.get("retention_reset_tokens_days", 7)))
    notif_days = (payload.read_notifications_days if payload and payload.read_notifications_days is not None else int(st_dict.get("retention_read_notifications_days", 30)))
    pay_days = (payload.stale_payments_days if payload and payload.stale_payments_days is not None else int(st_dict.get("retention_stale_payments_days", 14)))

    reset_cutoff = now - timedelta(days=max(1, token_days))
    job_cutoff = now - timedelta(days=max(1, job_days))
    audit_cutoff = now - timedelta(days=max(1, audit_days))
    price_cutoff = now - timedelta(days=max(1, price_days))
    notif_cutoff = now - timedelta(days=max(1, notif_days))
    pay_cutoff = now - timedelta(days=max(1, pay_days))

    del_tokens = (await db.execute(delete(PasswordResetToken).where(PasswordResetToken.expires_at < reset_cutoff))).rowcount
    del_verif = (await db.execute(delete(EmailVerificationToken).where(EmailVerificationToken.expires_at < reset_cutoff))).rowcount
    del_jobs = (await db.execute(delete(JobRun).where(JobRun.completed_at < job_cutoff))).rowcount
    del_audits = (await db.execute(delete(AuditLog).where(AuditLog.created_at < audit_cutoff))).rowcount
    del_prices = (await db.execute(delete(PriceSyncLog).where(PriceSyncLog.created_at < price_cutoff))).rowcount
    del_notifs = (await db.execute(delete(Notification).where(and_(Notification.is_read.is_(True), Notification.created_at < notif_cutoff)))).rowcount
    del_pays = (await db.execute(delete(Payment).where(and_(Payment.status == "PENDING", Payment.created_at < pay_cutoff)))).rowcount

    total_deleted = del_tokens + del_verif + del_jobs + del_audits + del_prices + del_notifs + del_pays
    summary_str = f"Đã dọn dẹp {total_deleted} bản ghi rác ({del_audits} kiểm toán, {del_jobs} jobs, {del_notifs} thông báo, {del_tokens + del_verif} tokens, {del_pays} giao dịch treo)"

    # Persist last run status
    for k, v in [("cleanup_last_run", now.isoformat()), ("cleanup_last_summary", summary_str)]:
        s_obj = (await db.execute(select(SystemSetting).where(SystemSetting.key == k))).scalar_one_or_none()
        if s_obj:
            s_obj.value = v
            s_obj.updated_at = now
        else:
            db.add(SystemSetting(key=k, value=v, description="Nhật ký dọn dẹp rác tự động", updated_at=now))

    db.add(AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="DATA_CLEANUP_MANUAL",
        target_type="SYSTEM",
        details=summary_str
    ))
    await db.commit()

    return ApiResponse(
        data={
            "total_deleted": total_deleted,
            "cleaned": {
                "audit_logs": del_audits,
                "job_runs": del_jobs,
                "price_sync_logs": del_prices,
                "reset_tokens": del_tokens,
                "verification_tokens": del_verif,
                "read_notifications": del_notifs,
                "stale_pending_payments": del_pays,
            },
            "executed_at": now.isoformat(),
            "summary": summary_str,
        },
        message=summary_str
    )

# 14. Referrals
@router.get("/referrals", response_model=ApiResponse[list])
async def admin_list_referrals(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Referral).order_by(desc(Referral.created_at)).limit(100))
    refs = res.scalars().all()
    output = []
    for r in refs:
        u1 = (await db.execute(select(User).where(User.id == r.referrer_id))).scalar_one_or_none()
        u2 = (await db.execute(select(User).where(User.id == r.referred_user_id))).scalar_one_or_none()
        output.append({
            "id": r.id,
            "referrer_id": r.referrer_id,
            "referrer_username": u1.username if u1 else "N/A",
            "referred_user_id": r.referred_user_id,
            "referred_username": u2.username if u2 else "N/A",
            "commission": r.commission,
            "status": r.status,
            "created_at": r.created_at
        })
    return ApiResponse(data=output)

# 15. Sub-sites
@router.get("/sub-sites", response_model=ApiResponse[list])
async def admin_list_sub_sites(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(SubSite).order_by(desc(SubSite.created_at)))
    sites = res.scalars().all()
    output = []
    for s in sites:
        user = (await db.execute(select(User).where(User.id == s.user_id))).scalar_one_or_none()
        output.append({
            "id": s.id,
            "user_id": s.user_id,
            "username": user.username if user else "N/A",
            "domain": s.domain,
            "site_name": s.site_name,
            "markup_percent": s.markup_percent,
            "status": s.status,
            "created_at": s.created_at
        })
    return ApiResponse(data=output)

@router.put("/sub-sites/{id}/status", response_model=ApiResponse[bool])
async def admin_update_sub_site_status(
    id: int,
    payload: SubSiteStatusUpdate,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(SubSite).where(SubSite.id == id))
    site = res.scalar_one_or_none()
    if not site:
        raise HTTPException(status_code=404, detail="Không tìm thấy site con.")
    site.status = payload.status.upper()
    await db.commit()
    return ApiResponse(data=True, message=f"Đã chuyển trạng thái site con sang {site.status}")

# 16. Broadcast Notification
@router.post("/notifications/broadcast", response_model=ApiResponse[dict])
async def admin_broadcast_notification(
    payload: BroadcastNotificationRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(User)
    if payload.target_role:
        stmt = stmt.where(User.role == payload.target_role.upper())
    res = await db.execute(stmt)
    users = res.scalars().all()
    count = 0
    for u in users:
        notif = Notification(
            user_id=u.id,
            title=payload.title,
            message=payload.message,
            type=payload.type.upper(),
            created_at=datetime.utcnow()
        )
        db.add(notif)
        count += 1
    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="BROADCAST_NOTIFICATION",
        target_type="NOTIFICATION",
        details=f"Gửi thông báo '{payload.title}' tới {count} người dùng"
    )
    db.add(audit)
    await db.commit()
    return ApiResponse(data={"count": count}, message=f"Đã gửi thông báo tới {count} người dùng thành công.")
