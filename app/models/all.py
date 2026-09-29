from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, Numeric, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import relationship, validates
from app.database.session import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    email_verified = Column(Boolean, default=False, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(100), nullable=False, default="")
    phone = Column(String(20), nullable=True)
    avatar = Column(String(255), nullable=True)
    role = Column(String(20), default="USER", nullable=False)  # USER, SUPPORT, ADMIN
    balance = Column(Numeric(18, 2), default=0, nullable=False)
    status = Column(String(20), default="ACTIVE", nullable=False)  # ACTIVE, BANNED, DEACTIVATED
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True)
    referral_code = Column(String(50), unique=True, index=True, nullable=False)
    referred_by_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    api_key = Column(String(100), unique=True, index=True, nullable=True)
    api_key_hash = Column(String(64), unique=True, index=True, nullable=True)
    api_key_prefix = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    token_version = Column(Integer, default=0, nullable=False)
    is_pro = Column(Boolean, default=False, nullable=False)

    orders = relationship("Order", back_populates="user", cascade="all, delete-orphan")
    transactions = relationship("Transaction", back_populates="user", cascade="all, delete-orphan")
    payments = relationship("Payment", back_populates="user", cascade="all, delete-orphan")
    notifications = relationship("Notification", back_populates="user", cascade="all, delete-orphan")

class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(100), unique=True, index=True, nullable=False)
    platform = Column(String(50), index=True, nullable=False)  # Facebook, TikTok, YouTube, Instagram, Google
    icon = Column(String(50), default="Layers", nullable=False)
    description = Column(Text, nullable=True)
    status = Column(String(20), default="ACTIVE", nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    services = relationship("Service", back_populates="category")

class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    provider_type = Column(String(50), default="generic_smm", nullable=False) # tuongtaccheo, generic_smm, mock
    base_url = Column(String(255), nullable=False)
    api_key_encrypted = Column(Text, nullable=True)
    status = Column(String(20), default="ACTIVE", nullable=False)
    balance = Column(Float, default=0.0, nullable=False)
    last_sync = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    services = relationship("Service", back_populates="provider")
    mappings = relationship("ProviderServiceMapping", back_populates="provider", cascade="all, delete-orphan")
    price_logs = relationship("PriceSyncLog", back_populates="provider", cascade="all, delete-orphan")

class Service(Base):
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    external_service_id = Column(String(100), nullable=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, default="", nullable=False)
    platform = Column(String(50), index=True, nullable=False)
    service_type = Column(String(50), default="default", nullable=False)
    price = Column(Float, nullable=False)  # Customer price per 1000
    dealer_price = Column(Float, default=0.0, nullable=False)  # Reseller price per 1000
    provider_price = Column(Float, default=0.0, nullable=False)  # Cost per 1000
    min_quantity = Column(Integer, default=100, nullable=False)
    max_quantity = Column(Integer, default=100000, nullable=False)
    dripfeed_enabled = Column(Boolean, default=False, nullable=False)
    refill_enabled = Column(Boolean, default=False, nullable=False)
    cancel_enabled = Column(Boolean, default=False, nullable=False)
    status = Column(String(20), default="ACTIVE", nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)
    deleted_at = Column(DateTime, nullable=True)
    sort_order = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    @validates("external_service_id")
    def validate_external_service_id(self, key, value):
        if value is not None:
            return str(value)
        return None

    category = relationship("Category", back_populates="services")
    provider = relationship("Provider", back_populates="services")
    orders = relationship("Order", back_populates="service")
    mapping = relationship("ProviderServiceMapping", back_populates="service", uselist=False, cascade="all, delete-orphan")
    price_logs = relationship("PriceSyncLog", back_populates="service", cascade="all, delete-orphan")

class Order(Base):
    __tablename__ = "orders"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), index=True, nullable=False)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=True)
    external_order_id = Column(String(100), index=True, nullable=True)
    link = Column(String(500), nullable=False)
    quantity = Column(Integer, nullable=False)
    start_count = Column(Integer, default=0, nullable=False)
    remains = Column(Integer, default=0, nullable=False)
    price = Column(Numeric(18, 2), nullable=False)  # Total charged
    status = Column(String(30), default="PENDING", index=True, nullable=False) # PENDING, PROCESSING, COMPLETED, PARTIAL, CANCELED, REFUNDED, FAILED
    provider_status = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    next_retry_at = Column(DateTime, nullable=True, index=True)
    is_dripfeed = Column(Boolean, default=False, nullable=False)
    dripfeed_runs = Column(Integer, nullable=True)
    dripfeed_interval = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    @validates("external_order_id")
    def validate_external_order_id(self, key, value):
        if value is not None:
            return str(value)
        return None

    user = relationship("User", back_populates="orders")
    service = relationship("Service", back_populates="orders")
    refills = relationship("RefillRequest", back_populates="order", cascade="all, delete-orphan")

class Transaction(Base):
    __tablename__ = "transactions"
    # A ledger event reference is its idempotency boundary. NULL remains allowed
    # for historical records that predate reference generation.
    __table_args__ = (Index("uq_transactions_reference", "reference", unique=True),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    type = Column(String(30), index=True, nullable=False)  # DEPOSIT, ORDER, REFUND, BONUS, ADJUSTMENT
    amount = Column(Numeric(18, 2), nullable=False)
    balance_before = Column(Numeric(18, 2), nullable=False)
    balance_after = Column(Numeric(18, 2), nullable=False)
    reference = Column(String(100), index=True, nullable=True)
    description = Column(String(255), nullable=False)
    status = Column(String(20), default="SUCCESS", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)

    user = relationship("User", back_populates="transactions")

class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    payment_method = Column(String(50), default="VIETQR", nullable=False) # VIETQR, SEPAY, BANK_TRANSFER
    transaction_code = Column(String(100), unique=True, index=True, nullable=False)
    # The bank/gateway transaction id is the idempotency boundary for deposits.
    gateway_reference = Column(String(100), unique=True, index=True, nullable=True)
    status = Column(String(20), default="PENDING", index=True, nullable=False) # PENDING, COMPLETED, FAILED, EXPIRED
    raw_payload = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    completed_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="payments")


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    token_hash = Column(String(64), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class BankTransaction(Base):
    """Immutable record of a transaction received from a bank data source."""
    __tablename__ = "bank_transactions"

    id = Column(Integer, primary_key=True, index=True)
    provider = Column(String(50), nullable=False, default="MBBANK")
    bank_transaction_id = Column(String(150), unique=True, index=True, nullable=False)
    amount = Column(Numeric(18, 2), nullable=False)
    content = Column(Text, nullable=False, default="")
    occurred_at = Column(DateTime, nullable=True)
    status = Column(String(30), nullable=False, default="PENDING", index=True)
    error_message = Column(Text, nullable=True)
    raw_payload = Column(Text, nullable=True)
    processed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    type = Column(String(30), default="INFO", nullable=False) # INFO, SUCCESS, WARNING, ERROR
    is_read = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    user = relationship("User", back_populates="notifications")

class SupportConversation(Base):
    __tablename__ = "support_conversations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=True)
    subject = Column(String(255), default="Hỗ trợ khách hàng", nullable=False)
    status = Column(String(30), default="OPEN", index=True, nullable=False) # OPEN, WAITING, RESOLVED, CLOSED
    assigned_admin = Column(Integer, ForeignKey("users.id"), nullable=True)
    customer_type = Column(String(50), default="REGULAR", nullable=True) # VIP, REGULAR, NEW, DISTRIBUTOR, GUEST
    product_status = Column(String(50), default="PENDING", nullable=True) # PENDING, PROCESSING, COMPLETED, ISSUE, REFUNDED
    tags = Column(String(255), default="", nullable=True)
    guest_name = Column(String(100), nullable=True)
    guest_email = Column(String(100), nullable=True)
    is_hidden = Column(Boolean, default=False, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    messages = relationship("SupportMessage", back_populates="conversation", cascade="all, delete-orphan")

class SupportMessage(Base):
    __tablename__ = "support_messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("support_conversations.id"), index=True, nullable=False)
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    sender_name = Column(String(100), nullable=False)
    sender_role = Column(String(20), default="USER", nullable=False)
    message = Column(Text, nullable=False)
    attachment = Column(String(255), nullable=True)
    is_hidden = Column(Boolean, default=False, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    conversation = relationship("SupportConversation", back_populates="messages")

class Coupon(Base):
    __tablename__ = "coupons"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    type = Column(String(20), default="PERCENT", nullable=False) # PERCENT, FIXED
    value = Column(Float, nullable=False)
    min_amount = Column(Float, default=0.0, nullable=False)
    max_discount = Column(Float, default=0.0, nullable=False)
    usage_limit = Column(Integer, default=100, nullable=False)
    used_count = Column(Integer, default=0, nullable=False)
    expires_at = Column(DateTime, nullable=True)
    status = Column(String(20), default="ACTIVE", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class Referral(Base):
    __tablename__ = "referrals"

    id = Column(Integer, primary_key=True, index=True)
    referrer_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    referred_user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    commission = Column(Float, default=0.0, nullable=False)
    status = Column(String(20), default="ACTIVE", nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=True)
    username = Column(String(100), nullable=True)
    action = Column(String(100), index=True, nullable=False)
    target_type = Column(String(50), nullable=True)
    target_id = Column(String(50), nullable=True)
    ip_address = Column(String(50), nullable=True)
    details = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)

    def __init__(self, **kwargs):
        if "target_id" in kwargs and kwargs["target_id"] is not None:
            kwargs["target_id"] = str(kwargs["target_id"])
        super().__init__(**kwargs)

    @validates("target_id")
    def validate_target_id(self, key, value):
        if value is not None:
            return str(value)
        return None


class JobRun(Base):
    """Durable worker execution history for operational monitoring."""
    __tablename__ = "job_runs"

    id = Column(Integer, primary_key=True, index=True)
    job_name = Column(String(100), nullable=False, index=True)
    status = Column(String(20), nullable=False, index=True)  # SUCCESS, FAILED, SKIPPED
    started_at = Column(DateTime, nullable=False, index=True)
    completed_at = Column(DateTime, nullable=False)
    duration_ms = Column(Integer, nullable=False)
    details = Column(Text, nullable=True)

class SystemSetting(Base):
    __tablename__ = "system_settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(100), unique=True, index=True, nullable=False)
    value = Column(Text, nullable=False)
    description = Column(String(255), nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class SubSite(Base):
    __tablename__ = "sub_sites"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    domain = Column(String(100), unique=True, index=True, nullable=False)
    site_name = Column(String(100), nullable=False)
    logo_url = Column(String(255), nullable=True)
    markup_percent = Column(Float, default=20.0, nullable=False)
    status = Column(String(20), default="PENDING", nullable=False) # PENDING, ACTIVE, SUSPENDED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

class RefillRequest(Base):
    __tablename__ = "refill_requests"

    id = Column(Integer, primary_key=True, index=True)
    order_id = Column(Integer, ForeignKey("orders.id"), index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), index=True, nullable=False)
    external_refill_id = Column(String(100), nullable=True)

    @validates("external_refill_id")
    def validate_external_refill_id(self, key, value):
        if value is not None:
            return str(value)
        return None

    status = Column(String(20), default="PENDING", nullable=False) # PENDING, PROCESSING, COMPLETED, REJECTED
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    order = relationship("Order", back_populates="refills")


class ProviderServiceMapping(Base):
    __tablename__ = "provider_service_mappings"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), index=True, nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), index=True, nullable=True)
    external_service_id = Column(String(100), index=True, nullable=False)

    @validates("external_service_id")
    def validate_external_service_id(self, key, value):
        if value is not None:
            return str(value)
        return ""

    external_name = Column(String(255), nullable=True)
    external_category = Column(String(255), nullable=True)
    original_rate = Column(Float, default=0.0, nullable=False)

    markup_type = Column(String(20), default="PERCENT", nullable=False)  # PERCENT or FIXED
    markup_value = Column(Float, default=30.0, nullable=False)
    dealer_markup_type = Column(String(20), default="PERCENT", nullable=False)
    dealer_markup_value = Column(Float, default=15.0, nullable=False)

    auto_round = Column(Boolean, default=True, nullable=False)
    round_type = Column(String(20), default="nearest", nullable=False)  # nearest, ceil, floor
    round_unit = Column(Float, default=10.0, nullable=False)  # 10, 50, 100, 1000

    selling_price = Column(Float, default=0.0, nullable=False)
    dealer_price = Column(Float, default=0.0, nullable=False)

    min_quantity = Column(Integer, default=50, nullable=False)
    max_quantity = Column(Integer, default=10000, nullable=False)
    refill_enabled = Column(Boolean, default=False, nullable=False)
    cancel_enabled = Column(Boolean, default=False, nullable=False)
    sync_status = Column(String(30), default="ACTIVE", nullable=False)  # ACTIVE, DISABLED, PRICE_CHANGED
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    provider = relationship("Provider", back_populates="mappings")
    service = relationship("Service", back_populates="mapping")


class PriceSyncLog(Base):
    __tablename__ = "price_sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), index=True, nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), index=True, nullable=True)
    external_service_id = Column(String(100), index=True, nullable=False)

    @validates("external_service_id")
    def validate_external_service_id(self, key, value):
        if value is not None:
            return str(value)
        return ""

    service_name = Column(String(255), nullable=False)
    old_rate = Column(Float, default=0.0, nullable=False)
    new_rate = Column(Float, default=0.0, nullable=False)
    old_price = Column(Float, default=0.0, nullable=False)
    new_price = Column(Float, default=0.0, nullable=False)
    change_percent = Column(Float, default=0.0, nullable=False)
    alert_triggered = Column(Boolean, default=False, nullable=False)
    status = Column(String(50), default="UPDATED", nullable=False)  # UPDATED, WARNING_PRICE_SPIKE, DISABLED, UNCHANGED
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)

    provider = relationship("Provider", back_populates="price_logs")
    service = relationship("Service", back_populates="price_logs")
