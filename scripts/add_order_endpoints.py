with open("backend/app/routers/admin.py", "r", encoding="utf-8") as f:
    text = f.read()

new_order_endpoints = """

@router.post("/orders/{id}/sync-status", response_model=ApiResponse[OrderResponse])
async def admin_sync_order_status(id: int, db: AsyncSession = Depends(get_db)):
    \"\"\"Đồng bộ trạng thái đơn hàng trực tiếp từ nhà cung cấp (action=status).\"\"\"
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
    \"\"\"Thử lại gửi đơn sang nhà cung cấp nếu trước đó bị lỗi kết nối.\"\"\"
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
        await db.commit()
        await db.refresh(order)
        return ApiResponse(data=OrderResponse.model_validate(order), message="Đã gửi lại đơn sang nhà cung cấp thành công!")
    except Exception as e:
        order.error_message = str(e)
        await db.commit()
        raise HTTPException(status_code=400, detail=f"Lỗi gửi đơn sang nhà cung cấp: {str(e)}")

@router.post("/orders/{id}/refill-action", response_model=ApiResponse[dict])
async def admin_order_refill_action(id: int, db: AsyncSession = Depends(get_db)):
    \"\"\"Gửi yêu cầu bảo hành (refill) sang nhà cung cấp.\"\"\"
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
    \"\"\"Hủy đơn hàng tại nhà cung cấp và hoàn tiền cho thành viên.\"\"\"
    res = await db.execute(select(Order).where(Order.id == id))
    order = res.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")

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
    u_res = await db.execute(select(User).where(User.id == order.user_id))
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
"""

if "admin_sync_order_status" not in text:
    target = "@router.get(\"/payments\""
    idx = text.find(target)
    if idx != -1:
        text = text[:idx] + new_order_endpoints + "\n\n" + text[idx:]
    else:
        text += new_order_endpoints

with open("backend/app/routers/admin.py", "w", encoding="utf-8") as f:
    f.write(text)

import ast
ast.parse(open("backend/app/routers/admin.py", "r", encoding="utf-8").read())
print("Order admin endpoints added and verified!")