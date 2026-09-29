# Step 1: imports and format_provider_response
with open("backend/app/routers/admin.py", "r", encoding="utf-8") as f:
    text = f.read()

old_models_import = """from app.models.all import (
    User, Service, Category, Provider, Order, Payment,
    Transaction, Coupon, SupportConversation, SupportMessage, AuditLog, SystemSetting,
    Notification, Referral, SubSite, RefillRequest
)"""

new_models_import = """from app.models.all import (
    User, Service, Category, Provider, Order, Payment,
    Transaction, Coupon, SupportConversation, SupportMessage, AuditLog, SystemSetting,
    Notification, Referral, SubSite, RefillRequest,
    ProviderServiceMapping, PriceSyncLog
)
from app.services.pricing_service import (
    calculate_single_price, calculate_service_prices, generate_markup_previews
)"""

if old_models_import in text:
    text = text.replace(old_models_import, new_models_import, 1)

old_schemas_import = """from app.schemas.all import (
    ApiResponse, UserResponse, ServiceCreate, ServiceUpdate, ServiceResponse,
    CategoryCreate, CategoryUpdate, CategoryResponse, ProviderCreate,
    PaymentResponse, TransactionResponse, SupportConversationResponse,
    ProviderUpdate, ProviderResponse, OrderResponse, CouponCreate, CouponResponse,
    AuditLogResponse, DashboardStats
)"""

new_schemas_import = """from app.schemas.all import (
    ApiResponse, UserResponse, ServiceCreate, ServiceUpdate, ServiceResponse,
    CategoryCreate, CategoryUpdate, CategoryResponse, ProviderCreate,
    PaymentResponse, TransactionResponse, SupportConversationResponse,
    ProviderUpdate, ProviderResponse, OrderResponse, CouponCreate, CouponResponse,
    AuditLogResponse, DashboardStats,
    ProviderTestConnectionRequest, ProviderTestConnectionResponse,
    ProviderCatalogItem, ImportServicesRequest, BulkMarkupPreviewRequest,
    ProviderMappingResponse, UpdateMappingRequest, PriceSyncLogResponse
)"""

if old_schemas_import in text:
    text = text.replace(old_schemas_import, new_schemas_import, 1)

with open("backend/app/routers/admin.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Imports updated successfully")
