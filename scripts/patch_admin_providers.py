with open("backend/app/routers/admin_providers.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Update imports
old_imp = """from app.schemas.all import (
    ApiResponse, ProviderCreate, ProviderUpdate, ProviderResponse,
    ProviderTestConnectionRequest, ProviderTestConnectionResponse,
    ProviderCatalogItem, ImportServicesRequest, BulkMarkupPreviewRequest,
    ProviderMappingResponse, UpdateMappingRequest, PriceSyncLogResponse
)"""

new_imp = """from app.schemas.all import (
    ApiResponse, ProviderCreate, ProviderUpdate, ProviderResponse,
    ProviderTestConnectionRequest, ProviderTestConnectionResponse,
    ProviderCatalogItem, ImportServicesRequest, BulkMarkupPreviewRequest,
    ProviderMappingResponse, UpdateMappingRequest, PriceSyncLogResponse,
    TTCBoostRequest, TTCMultiStatusRequest, TTCMultiCancelRequest, TTCLoginRequest
)"""

code = code.replace(old_imp, new_imp)

# 2. Update format_provider_response
old_format = """    resp.services_count = services_cnt
    resp.mappings_count = mappings_cnt
    return resp"""

new_format = """    resp.services_count = services_cnt
    resp.mappings_count = mappings_cnt
    is_ttc = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
    resp.currency = "XU" if is_ttc else ("USD" if (provider.provider_type or "").lower() == "thmxh" else "VND")
    return resp"""

code = code.replace(old_format, new_format)

# 3. Update admin_test_connection_saved
old_test = """    try:
        if hasattr(adapter, "test_connection"):
            conn_res = await adapter.test_connection()
            if conn_res.get("success"):
                provider.balance = float(conn_res.get("balance", 0.0))
                provider.last_sync = datetime.utcnow()
                await db.commit()
            return ApiResponse(data=ProviderTestConnectionResponse(
                success=conn_res.get("success", False),
                balance=conn_res.get("balance"),
                currency=conn_res.get("currency", "USD"),
                error=conn_res.get("error")
            ))
        else:
            bal = await adapter.get_balance()
            provider.balance = bal
            provider.last_sync = datetime.utcnow()
            await db.commit()
            return ApiResponse(data=ProviderTestConnectionResponse(
                success=True,
                balance=bal,
                currency="USD"
            ))"""

new_test = """    is_ttc = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
    default_curr = "XU" if is_ttc else ("USD" if (provider.provider_type or "").lower() == "thmxh" else "VND")

    try:
        if hasattr(adapter, "test_connection"):
            conn_res = await adapter.test_connection()
            if conn_res.get("success"):
                provider.balance = float(conn_res.get("balance", 0.0))
                provider.last_sync = datetime.utcnow()
                await db.commit()
            return ApiResponse(data=ProviderTestConnectionResponse(
                success=conn_res.get("success", False),
                balance=conn_res.get("balance"),
                currency=conn_res.get("currency", default_curr),
                error=conn_res.get("error")
            ))
        else:
            bal = await adapter.get_balance()
            provider.balance = bal
            provider.last_sync = datetime.utcnow()
            await db.commit()
            return ApiResponse(data=ProviderTestConnectionResponse(
                success=True,
                balance=bal,
                currency=default_curr
            ))"""

code = code.replace(old_test, new_test)

# 4. Add TTC endpoints at bottom
ttc_endpoints = """

# --- TuongTacCheo Dedicated Management Endpoints ---

@provider_router.post("/providers/{id}/ttc-boost", response_model=ApiResponse[dict])
async def admin_ttc_boost_orders(
    id: int,
    payload: TTCBoostRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    \"\"\"Tăng tốc độ chạy đơn hàng (Action: boost) trên TuongTacCheo.\"\"\"
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")
    
    adapter = ProviderManager.get_provider(provider)
    if hasattr(adapter, "boost_multiple_orders"):
        result = await adapter.boost_multiple_orders(payload.service_id, payload.orders)
        return ApiResponse(data=result, message="Đã gửi yêu cầu tăng tốc độ đơn sang TuongTacCheo.")
    raise HTTPException(status_code=400, detail="Nhà cung cấp này không hỗ trợ lệnh boost.")


@provider_router.post("/providers/{id}/ttc-multi-status", response_model=ApiResponse[dict])
async def admin_ttc_multi_status(
    id: int,
    payload: TTCMultiStatusRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    \"\"\"Tra cứu trạng thái nhiều đơn hàng cùng lúc từ TuongTacCheo (Action: status + orders).\"\"\"
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")
    
    adapter = ProviderManager.get_provider(provider)
    if hasattr(adapter, "get_multiple_orders_status"):
        result = await adapter.get_multiple_orders_status(payload.order_ids)
        return ApiResponse(data=result, message=f"Đã tra cứu thông tin {len(payload.order_ids)} đơn hàng từ TuongTacCheo.")
    raise HTTPException(status_code=400, detail="Nhà cung cấp này không hỗ trợ tra cứu đa đơn.")


@provider_router.post("/providers/{id}/ttc-multi-cancel", response_model=ApiResponse[Any])
async def admin_ttc_multi_cancel(
    id: int,
    payload: TTCMultiCancelRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    \"\"\"Gửi yêu cầu hủy nhiều đơn hàng cùng lúc sang TuongTacCheo (Action: cancel + orders).\"\"\"
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")
    
    adapter = ProviderManager.get_provider(provider)
    if hasattr(adapter, "cancel_multiple_orders"):
        result = await adapter.cancel_multiple_orders(payload.order_ids)
        return ApiResponse(data=result, message=f"Đã gửi lệnh hủy {len(payload.order_ids)} đơn sang TuongTacCheo.")
    raise HTTPException(status_code=400, detail="Nhà cung cấp này không hỗ trợ hủy hàng loạt.")


@provider_router.post("/providers/{id}/ttc-login", response_model=ApiResponse[dict])
async def admin_ttc_test_login(
    id: int,
    payload: TTCLoginRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    \"\"\"Đăng nhập hoặc xác minh tài khoản TuongTacCheo (qua Access Token hoặc Username/Password).\"\"\"
    from app.providers.tuongtaccheo import TuongTacCheoProvider
    from app.config.settings import settings
    
    if payload.access_token:
        res = await TuongTacCheoProvider.login_by_access_token(payload.access_token)
        return ApiResponse(data=res, message="Kết quả đăng nhập bằng TTC Access Token.")
    
    username = payload.username or getattr(settings, "TTC_USERNAME", "truongdvmmo8")
    password = payload.password or getattr(settings, "TTC_PASSWORD", "Xuantruong@1412")
    res = await TuongTacCheoProvider.login_by_credentials(username, password)
    return ApiResponse(data=res, message="Kết quả đăng nhập tài khoản TuongTacCheo.")
"""

code += ttc_endpoints

with open("backend/app/routers/admin_providers.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Updated backend/app/routers/admin_providers.py successfully!")