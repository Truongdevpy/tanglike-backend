from app.utils.security import validate_target_link
from app.schemas.common import ApiResponse, ApiErrorResponse, ErrorDetail
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator, model_validator

# User Schemas
class UserRegister(BaseModel):
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=6, max_length=72)
    confirm_password: str = Field(..., min_length=6, max_length=72)
    full_name: Optional[str] = ""
    phone: Optional[str] = None
    referral_code: Optional[str] = None

class UserLogin(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=128)
    remember_me: bool = False

class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=10, max_length=2048)

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=6, max_length=72)

class ResetPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordConfirmRequest(BaseModel):
    token: str = Field(..., min_length=32, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=72)

class EmailVerificationConfirmRequest(BaseModel):
    token: str = Field(..., min_length=32, max_length=256)

class UserProfileUpdate(BaseModel):
    full_name: Optional[str] = None
    phone: Optional[str] = None
    avatar: Optional[str] = None

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    email_verified: bool
    full_name: str
    phone: Optional[str]
    avatar: Optional[str]
    role: str
    balance: float
    status: str
    is_pro: bool = False
    referral_code: str
    created_at: datetime
    last_login_at: Optional[datetime]

    @model_validator(mode="after")
    def check_admin_pro(self):
        if self.role in ["ADMIN", "PRO"]:
            self.is_pro = True
        return self

    model_config = ConfigDict(from_attributes=True)

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserResponse

# Category Schemas
class StatusToggleRequest(BaseModel):
    status: str

class BulkStatusRequest(BaseModel):
    ids: List[int]
    status: str

class CategoryCreate(BaseModel):
    name: str
    slug: str
    platform: str
    icon: str = "Layers"
    description: Optional[str] = None
    status: str = "ACTIVE"
    sort_order: int = 0

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    slug: Optional[str] = None
    platform: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    sort_order: Optional[int] = None

class CategoryResponse(BaseModel):
    id: int
    name: str
    slug: str
    platform: str
    icon: str
    description: Optional[str] = None
    status: str
    sort_order: int
    created_at: datetime
    service_count: Optional[int] = 0
    is_ttc: Optional[bool] = False

    model_config = ConfigDict(from_attributes=True)

# Provider Schemas
class ProviderCreate(BaseModel):
    name: str
    provider_type: str = "generic_smm"
    base_url: str
    api_key: str

class ProviderUpdate(BaseModel):
    name: Optional[str] = None
    provider_type: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    status: Optional[str] = None

class ProviderResponse(BaseModel):
    id: int
    name: str
    provider_type: str
    base_url: str
    status: str
    balance: float
    currency: Optional[str] = "USD"
    last_sync: Optional[datetime]
    created_at: datetime
    api_key_masked: Optional[str] = None
    services_count: Optional[int] = 0
    mappings_count: Optional[int] = 0

    model_config = ConfigDict(from_attributes=True)

# Service Schemas
class ServiceCreate(BaseModel):
    category_id: int
    provider_id: Optional[int] = None
    external_service_id: Optional[str] = None
    name: str
    description: str = ""
    platform: str
    service_type: str = "default"
    price: float
    dealer_price: Optional[float] = 0.0
    provider_price: float = 0.0
    min_quantity: int = 100
    max_quantity: int = 100000
    dripfeed_enabled: bool = False
    refill_enabled: bool = False
    cancel_enabled: bool = False
    status: str = "ACTIVE"
    sort_order: int = 0

class ServiceUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    platform: Optional[str] = None
    service_type: Optional[str] = None
    price: Optional[float] = None
    provider_price: Optional[float] = None
    min_quantity: Optional[int] = None
    max_quantity: Optional[int] = None
    dripfeed_enabled: Optional[bool] = None
    refill_enabled: Optional[bool] = None
    cancel_enabled: Optional[bool] = None
    status: Optional[str] = None
    sort_order: Optional[int] = None
    category_id: Optional[int] = None
    provider_id: Optional[int] = None

class ServiceResponse(BaseModel):
    id: int
    category_id: int
    provider_id: Optional[int] = None
    external_service_id: Optional[str] = None
    name: str
    description: str
    platform: str
    service_type: str
    price: float
    provider_price: Optional[float] = None
    min_quantity: int
    max_quantity: int
    dripfeed_enabled: bool
    refill_enabled: bool
    cancel_enabled: bool
    status: str
    sort_order: int
    category_name: Optional[str] = None
    server_tag: Optional[str] = None
    is_ttc: Optional[bool] = False

    model_config = ConfigDict(from_attributes=True)

# Order Schemas
class OrderCreate(BaseModel):
    service_id: int
    link: str = Field(..., min_length=3, max_length=2000, description="Đường dẫn hoặc ID bài viết")

    @field_validator("link")
    @classmethod
    def check_link_safety(cls, v: str) -> str:
        if not validate_target_link(v):
            raise ValueError("Định dạng liên kết đích không hợp lệ hoặc chứa ký tự bị cấm.")
        return v.strip()
    quantity: int = Field(..., gt=0, description="Số lượng phải lớn hơn 0")
    coupon_code: Optional[str] = None
    comments: Optional[str] = Field(None, description="Danh sách nội dung bình luận (mỗi dòng 1 nội dung)")
    reaction: Optional[str] = Field(None, description="Loại cảm xúc (LIKE, LOVE, CARE, HAHA, WOW, SAD, ANGRY)")
    speed: Optional[str] = Field(None, description="Tốc độ tăng tương tác")
    custom_data: Optional[Dict[str, Any]] = Field(None, description="Dữ liệu cấu hình bổ sung (VIP like, server...)")

class MassOrderItem(BaseModel):
    service_id: int
    link: str = Field(..., min_length=3, max_length=2000)
    quantity: int = Field(..., gt=0)

    @field_validator("link")
    @classmethod
    def check_link_safety(cls, v: str) -> str:
        if not validate_target_link(v):
            raise ValueError("Định dạng liên kết đích không hợp lệ hoặc chứa ký tự bị cấm.")
        return v.strip()

class MassOrderCreate(BaseModel):
    orders: List[MassOrderItem] = Field(..., min_length=1, max_length=100)

class DripFeedOrderCreate(BaseModel):
    service_id: int
    link: str = Field(..., min_length=3, max_length=2000)

    @field_validator("link")
    @classmethod
    def check_link_safety(cls, v: str) -> str:
        if not validate_target_link(v):
            raise ValueError("Định dạng liên kết đích không hợp lệ hoặc chứa ký tự bị cấm.")
        return v.strip()
    runs: int = Field(..., ge=2, le=100)
    quantity_per_run: int = Field(..., gt=0)
    interval_minutes: int = Field(..., ge=5)

class OrderResponse(BaseModel):
    id: int
    user_id: int
    service_id: int
    service_name: Optional[str] = None
    platform: Optional[str] = None
    refill_enabled: Optional[bool] = False
    link: str
    quantity: int
    start_count: int
    remains: int
    price: float
    provider_price: Optional[float] = None
    provider_cost: Optional[float] = None
    profit: Optional[float] = None
    status: str
    provider_status: Optional[str]
    error_message: Optional[str]
    is_dripfeed: bool
    dripfeed_runs: Optional[int]
    dripfeed_interval: Optional[int]
    created_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)

# Payment Schemas
class PaymentCreate(BaseModel):
    amount: float = Field(..., gt=0)
    payment_method: str = "VIETQR"

class PaymentResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    amount: float
    payment_method: str
    transaction_code: str
    status: str
    qr_url: Optional[str] = None
    bank_name: Optional[str] = None
    bank_account_no: Optional[str] = None
    bank_account_holder: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class BankingConfigResponse(BaseModel):
    enabled: bool = False
    api_type: str = "thueapi"
    bank_name: str = "MB Bank"
    bank_code: str = "MBBANK"
    bank_bin: str = "970422"
    account_number: str = ""
    account_name: str = ""
    has_token: bool = False
    token_masked: str = ""
    internal_mb_username: str = ""
    has_internal_password: bool = False
    internal_password_masked: str = ""
    content_prefix: str = "NAP"
    min_deposit: int = 10000
    vietqr_template: str = "compact2"
    has_cron_secret: bool = False
    cron_secret_masked: str = ""
    cron_secret: str = ""
    vietqr_preview_url: str = ""

class BankingConfigUpdateRequest(BaseModel):
    enabled: Optional[bool] = None
    api_type: Optional[str] = None
    bank_name: Optional[str] = None
    bank_code: Optional[str] = None
    bank_bin: Optional[str] = None
    account_number: Optional[str] = None
    account_name: Optional[str] = None
    token: Optional[str] = None
    clear_token: Optional[bool] = False
    internal_mb_username: Optional[str] = None
    internal_mb_password: Optional[str] = None
    clear_internal_mb_password: Optional[bool] = False
    content_prefix: Optional[str] = None
    min_deposit: Optional[int] = None
    vietqr_template: Optional[str] = None
    cron_secret: Optional[str] = None
    clear_cron_secret: Optional[bool] = False

class BankingStatsResponse(BaseModel):
    total: int = 0
    credited_count: int = 0
    credited_amount: float = 0.0
    ignored_count: int = 0
    failed_count: int = 0

class BankTransactionItemResponse(BaseModel):
    id: int
    provider: str
    bank_transaction_id: str
    amount: float
    content: str
    status: str
    error_message: Optional[str] = None
    occurred_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class WebhookPayload(BaseModel):
    gateway: str
    transaction_code: str
    amount: float
    signature: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None

# Transaction Schema
class TransactionResponse(BaseModel):
    id: int
    user_id: int
    username: Optional[str] = None
    type: str
    amount: float
    balance_before: float
    balance_after: float
    reference: Optional[str]
    description: str
    status: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# Support Schemas
class SupportConversationCreate(BaseModel):
    subject: str
    initial_message: str

class SupportMessageCreate(BaseModel):
    message: str
    attachment: Optional[str] = None

class SupportMessageResponse(BaseModel):
    id: int
    conversation_id: int
    sender_id: Optional[int] = None
    sender_name: str
    sender_role: str
    message: str
    attachment: Optional[str] = None
    is_hidden: Optional[bool] = False
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class SupportConversationResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    username: Optional[str] = None
    user_name: Optional[str] = None
    user_full_name: Optional[str] = None
    user_email: Optional[str] = None
    user_phone: Optional[str] = None
    user_balance: Optional[float] = None
    total_deposited: Optional[float] = None
    total_orders: Optional[int] = None
    is_online: Optional[bool] = None
    last_active_at: Optional[datetime] = None
    subject: str
    status: str
    assigned_admin: Optional[int] = None
    customer_type: Optional[str] = "REGULAR"
    product_status: Optional[str] = "PENDING"
    tags: Optional[str] = ""
    guest_name: Optional[str] = None
    guest_email: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    last_message: Optional[str] = None
    last_message_at: Optional[datetime] = None
    is_hidden: Optional[bool] = False

    model_config = ConfigDict(from_attributes=True)

# Coupon Schemas
class CouponCreate(BaseModel):
    code: str
    type: str = "PERCENT"
    value: float
    min_amount: float = 0.0
    max_discount: float = 0.0
    usage_limit: int = 100
    expires_at: Optional[datetime] = None

class CouponUpdate(BaseModel):
    value: Optional[float] = None
    type: Optional[str] = None
    min_amount: Optional[float] = None
    max_discount: Optional[float] = None
    usage_limit: Optional[int] = None
    expires_at: Optional[datetime] = None
    status: Optional[str] = None

class CouponResponse(BaseModel):
    id: int
    code: str
    type: str
    value: float
    min_amount: float
    max_discount: float
    usage_limit: int
    used_count: int
    status: str

    model_config = ConfigDict(from_attributes=True)

# Referral Schema
class ReferralStatsResponse(BaseModel):
    referral_code: str
    referral_link: str
    commission_rate: float
    total_referred: int
    total_commission: float
    this_month_commission: float

# Notification Schema
class NotificationResponse(BaseModel):
    id: int
    title: str
    message: str
    type: str
    is_read: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# Audit Log Schema
class AuditLogResponse(BaseModel):
    id: int
    user_id: Optional[int]
    username: Optional[str]
    action: str
    target_type: Optional[str]
    target_id: Optional[str]
    ip_address: Optional[str]
    details: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

# Admin Stat Schema
class DashboardStats(BaseModel):
    total_users: int
    active_users: int
    total_orders: int
    completed_orders: int
    pending_orders: int
    processing_orders: int
    revenue: float
    profit: float
    provider_cost: Optional[float] = 0.0
    profit_percent: Optional[float] = 0.0
    markup_percent: Optional[float] = 0.0
    open_tickets: int
    user_balance: Optional[float] = None

# TTC Action Schemas
class TTCBoostRequest(BaseModel):
    service_id: str
    orders: Dict[str, Any]

class ProviderOrderStatusRequest(BaseModel):
    order_id: Optional[str] = None
    order_ids: Optional[List[str]] = None
    action: Optional[str] = "status"

class TTCMultiStatusRequest(BaseModel):
    order_ids: List[str]

class TTCMultiCancelRequest(BaseModel):
    order_ids: List[str]

class TTCLoginRequest(BaseModel):
    access_token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

class SubSiteCreate(BaseModel):
    domain: str = Field(..., min_length=3, max_length=255)
    site_name: str = Field(..., min_length=2, max_length=100)
    markup_percent: float = Field(default=20.0, ge=0.0, le=500.0)
class DeactivateAccountRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=128)
    reason: Optional[str] = None


# Provider Sync & Mapping Schemas
class ProviderTestConnectionRequest(BaseModel):
    name: Optional[str] = None
    base_url: str
    api_key: str
    provider_type: Optional[str] = "generic_smm"

class ProviderTestConnectionResponse(BaseModel):
    success: bool
    balance: Optional[float] = None
    currency: Optional[str] = "USD"
    error: Optional[str] = None

class ProviderCatalogItem(BaseModel):
    service_id: str
    name: str
    type: Optional[str] = "Default"
    category: str
    platform: str
    rate: float
    min: int
    max: int
    refill: bool = False
    cancel: bool = False
    is_imported: bool = False
    local_service_id: Optional[int] = None
    local_price: Optional[float] = None
    local_dealer_price: Optional[float] = None

class ImportServiceItem(BaseModel):
    external_service_id: str
    name: str
    category: str
    type: Optional[str] = "default"
    platform: Optional[str] = None
    rate: float
    min: int
    max: int
    refill: bool = False
    cancel: bool = False
    markup_type: str = "PERCENT"  # PERCENT or FIXED
    markup_value: float = 30.0
    dealer_markup_type: str = "PERCENT"
    dealer_markup_value: float = 15.0
    auto_round: bool = True
    round_type: str = "nearest"  # nearest, ceil, floor
    round_unit: float = 10.0
    selling_price: float
    dealer_price: float
    target_category_id: Optional[int] = None

class ImportServicesRequest(BaseModel):
    services: List[ImportServiceItem]

class BulkMarkupPreviewRequest(BaseModel):
    items: List[Dict[str, Any]]
    markup_type: str = "PERCENT"
    markup_value: float = 30.0
    dealer_markup_type: str = "PERCENT"
    dealer_markup_value: float = 15.0
    auto_round: bool = True
    round_type: str = "nearest"
    round_unit: float = 10.0

class ProviderMappingResponse(BaseModel):
    id: int
    provider_id: int
    provider_name: Optional[str] = None
    service_id: Optional[int] = None
    service_name: Optional[str] = None
    external_service_id: str
    external_name: Optional[str] = None
    external_category: Optional[str] = None
    original_rate: float
    markup_type: str
    markup_value: float
    dealer_markup_type: str
    dealer_markup_value: float
    auto_round: bool
    round_type: str
    round_unit: float
    selling_price: float
    dealer_price: float
    min_quantity: int
    max_quantity: int
    refill_enabled: bool
    cancel_enabled: bool
    sync_status: str
    last_synced_at: Optional[datetime] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class UpdateMappingRequest(BaseModel):
    markup_type: Optional[str] = None
    markup_value: Optional[float] = None
    dealer_markup_type: Optional[str] = None
    dealer_markup_value: Optional[float] = None
    auto_round: Optional[bool] = None
    round_type: Optional[str] = None
    round_unit: Optional[float] = None
    selling_price: Optional[float] = None
    dealer_price: Optional[float] = None
    min_quantity: Optional[int] = None
    max_quantity: Optional[int] = None
    refill_enabled: Optional[bool] = None
    cancel_enabled: Optional[bool] = None
    sync_status: Optional[str] = None

class PriceSyncLogResponse(BaseModel):
    id: int
    provider_id: int
    provider_name: Optional[str] = None
    service_id: Optional[int] = None
    external_service_id: str
    service_name: str
    old_rate: float
    new_rate: float
    old_price: float
    new_price: float
    change_percent: float
    alert_triggered: bool
    status: str
    notes: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
