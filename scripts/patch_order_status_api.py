import sys

# 1. Update backend/app/schemas/all.py
with open("backend/app/schemas/all.py", "r", encoding="utf-8") as f:
    schema_code = f.read()

target_schema = "class TTCMultiStatusRequest(BaseModel):"
new_schema = """class ProviderOrderStatusRequest(BaseModel):
    order_id: Optional[str] = None
    order_ids: Optional[List[str]] = None
    action: Optional[str] = "status"

class TTCMultiStatusRequest(BaseModel):"""

if "ProviderOrderStatusRequest" not in schema_code:
    schema_code = schema_code.replace(target_schema, new_schema, 1)
    with open("backend/app/schemas/all.py", "w", encoding="utf-8") as f:
        f.write(schema_code)
    print("Added ProviderOrderStatusRequest to schemas/all.py")
else:
    print("ProviderOrderStatusRequest already in schemas")

# 2. Update backend/app/routers/admin_providers.py
with open("backend/app/routers/admin_providers.py", "r", encoding="utf-8") as f:
    router_code = f.read()

# Add import
if "ProviderOrderStatusRequest" not in router_code:
    router_code = router_code.replace(
        "TTCMultiStatusRequest, TTCMultiCancelRequest, TTCLoginRequest",
        "TTCMultiStatusRequest, TTCMultiCancelRequest, TTCLoginRequest, ProviderOrderStatusRequest"
    )

endpoint_code = '''
@provider_router.post("/providers/check-all-balances", response_model=ApiResponse[List[dict]])
async def admin_check_all_balances(
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Kiem tra va cap nhat so du, so xu, trang thai toan bo API provider."""
    import time
    res = await db.execute(select(Provider).where(Provider.status == "ACTIVE").order_by(Provider.id))
    providers = res.scalars().all()
    results = []
    for p in providers:
        ProviderManager.clear_cache(p.id)
        adapter = ProviderManager.get_provider(p)
        is_ttc = (p.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (p.name or "").lower()
        currency = "XU" if is_ttc else ("USD" if (p.provider_type or "").lower() == "thmxh" else "VND")
        
        start_t = time.time()
        status_online = False
        error_msg = None
        new_balance = p.balance or 0.0
        
        try:
            if hasattr(adapter, "test_connection"):
                conn = await adapter.test_connection()
                if conn.get("success"):
                    status_online = True
                    new_balance = float(conn.get("balance", 0.0))
                    currency = conn.get("currency", currency)
                    p.balance = new_balance
                    p.last_sync = datetime.utcnow()
                else:
                    error_msg = conn.get("error")
            else:
                bal = await adapter.get_balance()
                new_balance = float(bal)
                status_online = True
                p.balance = new_balance
                p.last_sync = datetime.utcnow()
        except Exception as e:
            error_msg = str(e)
            
        latency_ms = int((time.time() - start_t) * 1000)
        results.append({
            "id": p.id,
            "name": p.name,
            "provider_type": p.provider_type,
            "currency": currency,
            "balance": new_balance,
            "status": "ONLINE" if status_online else "OFFLINE",
            "latency_ms": latency_ms,
            "error": error_msg,
            "last_sync": p.last_sync.isoformat() if p.last_sync else None
        })
        
    await db.commit()
    return ApiResponse(data=results, message="Da kiem tra va cap nhat so du toan bo nha cung cap.")


@provider_router.post("/providers/{id}/order-status", response_model=ApiResponse[Any])
async def admin_provider_order_status(
    id: int,
    payload: ProviderOrderStatusRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Tra cuu truc tiep trang thai don hang tu API (THMXH, TTC, Generic SMM)."""
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Khong tim thay nha cung cap.")
        
    adapter = ProviderManager.get_provider(provider)
    action = (payload.action or "status").lower()
    
    # Neu tra cuu nhieu don hang
    if payload.order_ids and len(payload.order_ids) > 1:
        if action == "cancel" and hasattr(adapter, "cancel_multiple_orders"):
            result = await adapter.cancel_multiple_orders(payload.order_ids)
            return ApiResponse(data=result, message=f"Da gui lenh huy {len(payload.order_ids)} don sang API.")
        elif action == "refill_status" and hasattr(adapter, "get_multiple_refill_status"):
            result = await adapter.get_multiple_refill_status(payload.order_ids)
            return ApiResponse(data=result, message=f"Ket qua tra cuu refill {len(payload.order_ids)} don hang.")
        elif hasattr(adapter, "get_multiple_orders_status"):
            result = await adapter.get_multiple_orders_status(payload.order_ids)
            return ApiResponse(data=result, message=f"Ket qua tra cuu trang thai {len(payload.order_ids)} don tu API.")
        else:
            raise HTTPException(status_code=400, detail="Provider khong ho tro tra cuu da don.")
            
    # Tra cuu 1 don hang
    oid = payload.order_id or (payload.order_ids[0] if payload.order_ids else None)
    if not oid:
        raise HTTPException(status_code=400, detail="Vui long nhap ma don hang (Order ID).")
        
    oid = str(oid).strip()
    if action == "cancel":
        if hasattr(adapter, "cancel_order"):
            result = await adapter.cancel_order(oid)
            return ApiResponse(data=result, message=f"Ket qua huy don #{oid}")
        raise HTTPException(status_code=400, detail="Provider khong ho tro huy don.")
    elif action == "refill_status":
        if hasattr(adapter, "get_refill_status"):
            result = await adapter.get_refill_status(oid)
            return ApiResponse(data=result, message=f"Trang thai refill don #{oid}")
        raise HTTPException(status_code=400, detail="Provider khong ho tro tra cuu refill.")
    elif action == "refill":
        if hasattr(adapter, "refill_order"):
            result = await adapter.refill_order(oid)
            return ApiResponse(data=result, message=f"Yeu cau refill don #{oid}")
        raise HTTPException(status_code=400, detail="Provider khong ho tro refill.")
    else:
        status_obj = await adapter.get_order_status(oid)
        is_ttc = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
        curr = "XU" if is_ttc else ("USD" if (provider.provider_type or "").lower() == "thmxh" else "VND")
        data = {
            "order_id": status_obj.order_id,
            "status": status_obj.status,
            "start_count": status_obj.start_count,
            "remains": status_obj.remains,
            "currency": curr,
            "error_message": status_obj.error_message
        }
        return ApiResponse(data=data, message=f"Tra cuu trang thai don #{oid} thanh cong tu API.")
'''

if "/providers/check-all-balances" not in router_code:
    # Insert before the last endpoints
    router_code += "\n" + endpoint_code
    with open("backend/app/routers/admin_providers.py", "w", encoding="utf-8") as f:
        f.write(router_code)
    print("Added /providers/check-all-balances and /providers/{id}/order-status to admin_providers.py")
else:
    print("Endpoints already present in admin_providers.py")
