from fastapi.staticfiles import StaticFiles
import os
import re
import unicodedata
from app.middleware.payload_limit import PayloadLimitMiddleware
from app.routers.users import router as users_router
from sqlalchemy import text
import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, Request, status, Query, Header, Depends
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.session import get_db
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config.settings import settings
from app.database.session import engine, Base, AsyncSessionLocal
from app.models.all import User, Category, Service, Provider, Coupon, SystemSetting
from app.auth.security import hash_api_key, hash_password, get_optional_current_user
from app.utils.crypto import encrypt_secret, decrypt_secret
from app.workers.order_worker import order_worker

# Routers
from app.routers.auth import router as auth_router
from app.routers.services import router as services_router
from app.routers.orders import router as orders_router
from app.routers.payments import router as payments_router
from app.routers.transactions import router as transactions_router
from app.routers.referrals import router as referrals_router
from app.routers.notifications import router as notifications_router
from app.routers.support import router as support_router
from app.routers.sub_sites import router as sub_sites_router
from app.routers.tools import router as tools_router
from app.routers.user_api import router as user_api_router
from app.routers.admin import router as admin_router
from app.routers.smm_v2 import router as smm_v2_router
from app.middleware.rate_limit import RateLimitMiddleware
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.middleware.maintenance import MaintenanceModeMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("tanglike")

async def seed_initial_data():
    async with AsyncSessionLocal() as db:
        # Demo identities are useful locally, but must never appear in production.
        is_production = settings.APP_ENV.lower() == "production"

        # 1. Explicitly configured bootstrap admin
        from sqlalchemy import select
        res = await db.execute(select(User).where(User.username == "admin"))
        existing_admin = res.scalars().first()
        if not existing_admin and is_production and settings.BOOTSTRAP_ADMIN_USERNAME:
            if not (settings.BOOTSTRAP_ADMIN_EMAIL and settings.BOOTSTRAP_ADMIN_PASSWORD):
                raise RuntimeError("BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD are required with BOOTSTRAP_ADMIN_USERNAME")
            admin_user = User(
                username=settings.BOOTSTRAP_ADMIN_USERNAME,
                email=settings.BOOTSTRAP_ADMIN_EMAIL,
                password_hash=hash_password(settings.BOOTSTRAP_ADMIN_PASSWORD),
                full_name="Administrator",
                role="ADMIN",
                balance=0.0,
                status="ACTIVE",
                referral_code=f"ADMIN{settings.BOOTSTRAP_ADMIN_USERNAME[:12].upper()}",
                created_at=datetime.utcnow(),
            )
            db.add(admin_user)
        elif not existing_admin and not is_production:
            admin_user = User(
                username="admin",
                email="admin@tanglike.vn",
                password_hash=hash_password("admin123456"),
                full_name="Quản Trị Viên",
                phone="0868133346",
                role="ADMIN",
                balance=10000000.0,
                status="ACTIVE",
                referral_code="ADMIN888",
                api_key_hash=hash_api_key("tang_admin_secret_key_prod_2026"),
                api_key_prefix="tang_admin_s",
                created_at=datetime.utcnow()
            )
            db.add(admin_user)
            logger.info("Created default admin user: admin")

        # 2. Demo client user (only if explicitly configured or on fresh install)
        res_demo = await db.execute(select(User).where(User.username == "demo"))
        if not res_demo.scalars().first() and (settings.APP_ENV.lower() == 'test' and os.getenv('SEED_DEMO_USER') == '1'):
            demo_user = User(
                username="demo",
                email="demo@tanglike.vn",
                password_hash=hash_password("demo123456"),
                full_name="Khách Hàng Mẫu",
                phone="0912345678",
                role="USER",
                balance=500000.0,
                status="ACTIVE",
                referral_code="DEMO999",
                api_key_hash=hash_api_key("tang_demo_client_api_key_2026"),
                api_key_prefix="tang_demo_c",
                created_at=datetime.utcnow()
            )
            db.add(demo_user)
            logger.info("Created default demo user: demo (Balance: 500,000đ)")

        # 3. Default Provider - TuongTacCheo
        p_res = await db.execute(select(Provider).where((Provider.name == "TuongTacCheo Core") | (Provider.provider_type == "tuongtaccheo")))
        provider = p_res.scalars().first()
        if not provider:
            provider = Provider(
                name="TuongTacCheo Core",
                provider_type="tuongtaccheo",
                base_url=settings.TTC_API_URL,
                api_key_encrypted=encrypt_secret(settings.TTC_API_KEY),
                status="ACTIVE",
                balance=2500000.0,
                created_at=datetime.utcnow()
            )
            db.add(provider)
            await db.flush()
        elif settings.TTC_API_KEY:
            # Rotate the configured provider credential without exposing it in
            # the database or API responses.
            provider.api_key_encrypted = encrypt_secret(settings.TTC_API_KEY)

        # 3.2. Default Provider - THMXH
        thmxh_res = await db.execute(select(Provider).where(Provider.provider_type == "thmxh"))
        thmxh_provider = thmxh_res.scalars().first()
        if not thmxh_provider:
            thmxh_provider = Provider(
                name="THMXH Provider (thmxh.com)",
                provider_type="thmxh",
                base_url=settings.THMXH_API_URL or "https://thmxh.com/api/v2",
                api_key_encrypted=encrypt_secret(settings.THMXH_API_KEY),
                status="ACTIVE",
                balance=100.84,
                created_at=datetime.utcnow()
            )
            db.add(thmxh_provider)
            await db.flush()
        elif settings.THMXH_API_KEY:
            thmxh_provider.api_key_encrypted = encrypt_secret(settings.THMXH_API_KEY)

        # 4. Default Categories
        categories_data = [
            {"name": "Facebook Tương Tác", "slug": "facebook", "platform": "Facebook", "icon": "Facebook", "sort_order": 1},
            {"name": "TikTok Tương Tác", "slug": "tiktok", "platform": "TikTok", "icon": "Video", "sort_order": 2},
            {"name": "Instagram Follow & Like", "slug": "instagram", "platform": "Instagram", "icon": "Instagram", "sort_order": 3},
            {"name": "YouTube View & Sub", "slug": "youtube", "platform": "YouTube", "icon": "Youtube", "sort_order": 4},
            {"name": "Google Đánh Giá 5 Sao", "slug": "google", "platform": "Google", "icon": "Globe", "sort_order": 5},
        ]
        created_cats = {}
        for c_data in categories_data:
            c_check = await db.execute(select(Category).where(Category.slug == c_data["slug"]))
            existing_c = c_check.scalars().first()
            if not existing_c:
                cat = Category(**c_data, created_at=datetime.utcnow())
                db.add(cat)
                await db.flush()
                created_cats[c_data["platform"]] = cat.id
            else:
                created_cats[c_data["platform"]] = existing_c.id

        # 5. Default Services - TuongTacCheo
        services_seed = [
            {
                "category_id": created_cats.get("Facebook", 1),
                "provider_id": provider.id,
                "external_service_id": "1",
                "name": "Facebook Like Bài Viết [Tốc Độ Cao - Lên Ngay]",
                "description": "Tăng like bài viết công khai, tốc độ 10k-50k like/ngày, bảo hành 30 ngày.",
                "platform": "Facebook",
                "service_type": "likes",
                "price": 14400.0,
                "dealer_price": 12500.0,
                "provider_price": 11100.0,
                "min_quantity": 50,
                "max_quantity": 50000,
                "refill_enabled": True,
                "sort_order": 1
            },
            {
                "category_id": created_cats.get("Facebook", 1),
                "provider_id": provider.id,
                "external_service_id": "24",
                "name": "Facebook Follow Profile [Sub Thật Người Dùng VN]",
                "description": "Tăng người theo dõi trang cá nhân, nick thật có avatar và hoạt động.",
                "platform": "Facebook",
                "service_type": "followers",
                "price": 18800.0,
                "dealer_price": 16500.0,
                "provider_price": 14400.0,
                "min_quantity": 100,
                "max_quantity": 100000,
                "refill_enabled": True,
                "sort_order": 2
            },
            {
                "category_id": created_cats.get("TikTok", 2),
                "provider_id": provider.id,
                "external_service_id": "27",
                "name": "TikTok Video Views [Siêu Tốc - Đề Xuất]",
                "description": "Tăng lượt xem video TikTok giúp đẩy đề xuất nhanh chóng.",
                "platform": "TikTok",
                "service_type": "views",
                "price": 3600.0,
                "dealer_price": 3100.0,
                "provider_price": 2800.0,
                "min_quantity": 500,
                "max_quantity": 1000000,
                "sort_order": 3
            },
            {
                "category_id": created_cats.get("TikTok", 2),
                "provider_id": provider.id,
                "external_service_id": "16",
                "name": "TikTok Follower Việt Nam [Đủ Điều Kiện Bật Kiếm Tiền]",
                "description": "Follower người Việt Nam thật, hỗ trợ tài khoản đạt 10.000 follower nhanh.",
                "platform": "TikTok",
                "service_type": "followers",
                "price": 18800.0,
                "dealer_price": 16500.0,
                "provider_price": 14400.0,
                "min_quantity": 100,
                "max_quantity": 50000,
                "refill_enabled": True,
                "sort_order": 4
            },
            {
                "category_id": created_cats.get("Instagram", 3),
                "provider_id": provider.id,
                "external_service_id": "15",
                "name": "Instagram Likes Bài Viết [Chất Lượng Cao]",
                "description": "Like bài post Instagram người dùng quốc tế & Việt Nam.",
                "platform": "Instagram",
                "service_type": "likes",
                "price": 14400.0,
                "dealer_price": 12500.0,
                "provider_price": 11100.0,
                "min_quantity": 50,
                "max_quantity": 30000,
                "sort_order": 5
            },
            {
                "category_id": created_cats.get("YouTube", 4),
                "provider_id": provider.id,
                "external_service_id": "28",
                "name": "YouTube Subscribers Kênh [Hỗ Trợ Bật Kiếm Tiền]",
                "description": "Subscribers chất lượng cao, không tụt, bảo hành 60 ngày trọn gói.",
                "platform": "YouTube",
                "service_type": "subscribers",
                "price": 36100.0,
                "dealer_price": 31500.0,
                "provider_price": 27800.0,
                "min_quantity": 50,
                "max_quantity": 10000,
                "refill_enabled": True,
                "sort_order": 6
            },
        ]
        for s_data in services_seed:
            s_check = await db.execute(
                select(Service).where(
                    Service.provider_id == provider.id,
                    Service.external_service_id == str(s_data["external_service_id"])
                )
            )
            existing_srv = s_check.scalars().first()
            if not existing_srv:
                srv = Service(**s_data, status="ACTIVE", created_at=datetime.utcnow())
                db.add(srv)
            else:
                existing_srv.name = s_data["name"]
                existing_srv.price = s_data["price"]
                existing_srv.dealer_price = s_data.get("dealer_price", round(s_data["price"] * 0.9, 2))
                existing_srv.provider_price = s_data["provider_price"]
                existing_srv.description = s_data["description"]
                existing_srv.min_quantity = s_data["min_quantity"]
                existing_srv.max_quantity = s_data["max_quantity"]

        # 5.2. Default Services - THMXH Provider
        thmxh_srv_check = await db.execute(select(Service).where(Service.provider_id == thmxh_provider.id))
        if not thmxh_srv_check.first():
            from app.providers.thmxh import THMXHProvider
            thmxh_adapter = THMXHProvider(
                api_url=thmxh_provider.base_url or settings.THMXH_API_URL,
                api_key=decrypt_secret(thmxh_provider.api_key_encrypted) or settings.THMXH_API_KEY
            )
            thmxh_items = await thmxh_adapter.get_services()
            for item in thmxh_items:
                cat_name = (item.category or f"{item.platform} Services").strip()
                cat_chk = await db.execute(select(Category).where(Category.name == cat_name, Category.platform == item.platform))
                existing_cat = cat_chk.scalars().first()
                if not existing_cat:
                    clean_slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", f"{item.platform}-{cat_name}").encode("ascii", "ignore").decode("utf-8").lower()).strip("-")[:55]
                    new_cat = Category(
                        name=cat_name,
                        slug=clean_slug or f"cat-{int(datetime.utcnow().timestamp())}",
                        platform=item.platform,
                        icon="Layers",
                        status="ACTIVE",
                        sort_order=5,
                        created_at=datetime.utcnow()
                    )
                    db.add(new_cat)
                    await db.flush()
                    cat_id = new_cat.id
                else:
                    cat_id = existing_cat.id

                new_srv = Service(
                    provider_id=thmxh_provider.id,
                    category_id=cat_id,
                    external_service_id=item.service_id,
                    name=item.name,
                    description="",
                    platform=item.platform,
                    service_type="default",
                    price=item.rate * 1.3,
                    provider_price=item.rate,
                    min_quantity=item.min,
                    max_quantity=item.max,
                    refill_enabled=item.refill,
                    cancel_enabled=item.cancel,
                    status="ACTIVE",
                    created_at=datetime.utcnow()
                )
                db.add(new_srv)
            logger.info(f"Seeded {len(thmxh_items)} THMXH services into database.")

        # 6. Default Welcome Coupon
        cp_check = await db.execute(select(Coupon).where(Coupon.code == "TANGLIKE10"))
        if not cp_check.scalars().first():
            welcome_coupon = Coupon(
                code="TANGLIKE10",
                type="PERCENT",
                value=10.0,
                min_amount=0.0,
                max_discount=50000.0,
                usage_limit=1000,
                status="ACTIVE",
                created_at=datetime.utcnow()
            )
            db.add(welcome_coupon)

        # 7. Default System Settings
        default_settings = [
            ("site_name", "TangLike SMM Pro", "Tên website hệ thống"),
            ("site_title", "TangLike - SMM SaaS Panel Đa Dịch Vụ Mạng Xã Hội", "Tiêu đề SEO website"),
            ("site_description", "Hệ thống tăng tương tác Facebook, TikTok, YouTube, Instagram tự động 24/7.", "Mô tả SEO"),
            ("currency", "VND", "Đơn vị tiền tệ chính"),
            ("currency_symbol", "đ", "Ký hiệu tiền tệ"),
            ("default_markup_percent", "30", "Tỷ lệ lợi nhuận mặc định khi đồng bộ (%)"),
            ("referral_commission_percent", "5", "Tỷ lệ hoa hồng giới thiệu (%)"),
            ("min_deposit_amount", "10000", "Số tiền nạp tối thiểu (VNĐ)"),
            ("maintenance_mode", "false", "Chế độ bảo trì hệ thống"),
            ("registration_enabled", "true", "Cho phép thành viên mới đăng ký"),
            ("telegram_notifications", "true", "Bật gửi cảnh báo Telegram cho Admin"),
            ("bank_name", "MB Bank (Ngân hàng TMCP Quân Đội)", "Tên ngân hàng nhận chuyển khoản"),
            ("bank_account_number", settings.BANK_ACCOUNT_NO, "Số tài khoản ngân hàng"),
            ("bank_account_holder", settings.BANK_ACCOUNT_HOLDER, "Tên chủ tài khoản"),
            ("vietqr_template", "compact", "Giao diện mã VietQR")
        ]
        for key, val, desc in default_settings:
            st_check = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
            if not st_check.scalars().first():
                db.add(SystemSetting(key=key, value=val, description=desc, updated_at=datetime.utcnow()))

        # Seed banking_config if not present
        bk_check = await db.execute(select(SystemSetting).where(SystemSetting.key == "banking_config"))
        if not bk_check.scalars().first():
            init_banking = {
                "enabled": False,
                "api_type": "thueapi",
                "bank_name": "MB Bank",
                "bank_code": "MBBANK",
                "bank_bin": "970422",
                "account_number": settings.BANK_ACCOUNT_NO or "",
                "account_name": settings.BANK_ACCOUNT_HOLDER or "",
                "token": settings.MB_BANK_API_TOKEN or "",
                "internal_mb_username": "",
                "internal_mb_password": "",
                "content_prefix": settings.DEPOSIT_CONTENT_PREFIX or "NAP",
                "min_deposit": int(settings.MIN_DEPOSIT_AMOUNT or 10000),
                "vietqr_template": "compact2",
                "cron_secret": "",
            }
            db.add(SystemSetting(
                key="banking_config",
                value=json.dumps(init_banking, ensure_ascii=False),
                description="Cấu hình banking MB Bank & nạp tiền tự động",
                updated_at=datetime.utcnow()
            ))

        await db.commit()
        logger.info("Database initial seed check completed successfully.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing TangLike SMM SaaS Backend...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database schema synchronized.")

    await seed_initial_data()

    worker_task = asyncio.create_task(order_worker.run_loop())

    yield

    order_worker.stop()
    worker_task.cancel()
    try:
        await worker_task
    except (asyncio.CancelledError, Exception):
        pass
    try:
        await engine.dispose()
    except (asyncio.CancelledError, BaseException) as exc:
        logger.debug("Engine disposal completed: %s", exc)
    logger.info("TangLike Backend shutdown complete.")

docs_url = None if settings.APP_ENV == "production" else "/docs"
redoc_url = None if settings.APP_ENV == "production" else "/redoc"
openapi_url = None if settings.APP_ENV == "production" else "/openapi.json"

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Hệ thống SMM SaaS Panel Đa Dịch Vụ - REST API & Provider Integration",
    lifespan=lifespan,
    docs_url=docs_url,
    redoc_url=redoc_url,
    openapi_url=openapi_url
)

# Rate limiting middleware
app.add_middleware(PayloadLimitMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(MaintenanceModeMiddleware)

# Secure CORS configuration
origins = settings.CORS_ORIGINS if settings.CORS_ORIGINS else ["http://localhost:3000"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https://.*tanglike.*\.vercel\.app$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Exception handlers according to spec
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {
                "code": f"HTTP_{exc.status_code}",
                "message": exc.detail
            }
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    errors = exc.errors()
    first_error = errors[0]["msg"] if errors else "Dữ liệu gửi lên không hợp lệ."
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": first_error
            }
        }
    )

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled Exception on {request.url.path}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Đã xảy ra lỗi hệ thống. Vui lòng thử lại sau."
            }
        }
    )

# Include Routers under /api/v1
api_v1_prefix = "/api/v1"
app.include_router(auth_router, prefix=api_v1_prefix)
app.include_router(services_router, prefix=api_v1_prefix)
app.include_router(orders_router, prefix=api_v1_prefix)
app.include_router(payments_router, prefix=api_v1_prefix)
app.include_router(transactions_router, prefix=api_v1_prefix)
app.include_router(referrals_router, prefix=api_v1_prefix)
app.include_router(notifications_router, prefix=api_v1_prefix)
app.include_router(support_router, prefix=api_v1_prefix)
app.include_router(sub_sites_router, prefix=api_v1_prefix)
app.include_router(tools_router, prefix=api_v1_prefix)
app.include_router(user_api_router, prefix=api_v1_prefix)
app.include_router(admin_router, prefix=api_v1_prefix)
app.include_router(users_router, prefix=api_v1_prefix)

# Include SMM Protocol v2 Router
app.include_router(smm_v2_router)

@app.get("/api/bank-cron.php", tags=["Payments & Auto Deposit"])
@app.post("/api/bank-cron.php", tags=["Payments & Auto Deposit"])
@app.get("/bank-cron.php", tags=["Payments & Auto Deposit"])
@app.post("/bank-cron.php", tags=["Payments & Auto Deposit"])
async def alias_bank_cron_sync(
    secret: Optional[str] = Query(None, description="Secret token cho Cron"),
    x_secret: Optional[str] = Header(None, alias="X-Bank-Cron-Secret"),
    optional_user: Optional[User] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db)
):
    from app.routers.payments import bank_cron_sync
    return await bank_cron_sync(secret=secret, x_secret=x_secret, optional_user=optional_user, db=db)

static_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "static"))
os.makedirs(os.path.join(static_dir, "uploads"), exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get(f"{api_v1_prefix}/settings", tags=["Public Settings"])
async def get_public_settings():
    from app.schemas.all import ApiResponse
    from sqlalchemy import select
    async with AsyncSessionLocal() as db:
        public_keys = [
            "site_name", "site_title", "site_description", "currency_symbol",
            "site_logo", "favicon_url", "brand_color", "primary_color", "auth_banner_text", "meta_keywords",
            "maintenance_mode", "registration_enabled",
            "zalo_url", "popup_enabled", "popup_title", "popup_content",
            "thmxh_usd_rate", "usd_rate",
        ]
        res = await db.execute(select(SystemSetting).where(SystemSetting.key.in_(public_keys)))
        rows = res.scalars().all()
        settings_dict = {r.key: r.value for r in rows}
        defaults = {
            "site_name": "TangLike SMM",
            "site_title": "TangLike - SMM SaaS Panel Đa Dịch Vụ Mạng Xã Hội",
            "site_description": "Hệ thống tăng tương tác Facebook, TikTok, YouTube, Instagram tự động 24/7.",
            "currency_symbol": "đ",
            "site_logo": "",
            "favicon_url": "",
            "brand_color": "#10B981",
            "primary_color": "#10B981",
            "auth_banner_text": "Hệ thống tăng tương tác tự động 24/7 hàng đầu Việt Nam",
            "meta_keywords": "tang like, tăng follow, tang sub, dich vu mang xa hoi, smm panel",
            "maintenance_mode": "false",
            "registration_enabled": "true",
            "zalo_url": "https://zalo.me/0868133346",
            "popup_enabled": "false",
            "popup_title": "Thông Báo Từ Hệ Thống TangLike PRO",
            "popup_content": "Chào mừng quý khách đến với TangLike! Hệ thống nạp tiền tự động qua QR MB Bank và kích hoạt dịch vụ 24/7 siêu tốc.",
            "thmxh_usd_rate": "28000",
            "usd_rate": "28000",
        }
        for k, v in defaults.items():
            if k not in settings_dict:
                settings_dict[k] = v
        return ApiResponse(data=settings_dict)

@app.get("/health", tags=["Health"])
async def health_check():
    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "time": datetime.utcnow().isoformat()
    }

@app.get("/health/ready", tags=["Health"])
async def readiness_check():
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        db_status = "connected"
    except Exception as e:
        logger.error(f"Readiness DB probe failed: {e}")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "not_ready",
                "database": "disconnected",
                "error": "Database connection error",
                "time": datetime.utcnow().isoformat()
            }
        )

    return {
        "status": "ready",
        "database": db_status,
        "worker": "running" if getattr(order_worker, "is_running", True) else "stopped",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "time": datetime.utcnow().isoformat()
    }
