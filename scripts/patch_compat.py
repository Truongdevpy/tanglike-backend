with open("backend/app/routers/admin_providers.py", "r", encoding="utf-8") as f:
    text = f.read()

old_compat = """@provider_router.post("/providers/{id}/sync", response_model=ApiResponse[dict])
async def admin_sync_provider_compat(id: int, db: AsyncSession = Depends(get_db)):
    return await admin_sync_provider_mappings(id=id, alert_threshold_percent=15.0, db=db)"""

new_compat = """@provider_router.post("/providers/{id}/sync", response_model=ApiResponse[dict])
async def admin_sync_provider_compat(id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")

    ProviderManager.clear_cache(id)
    adapter = ProviderManager.get_provider(provider)
    services_data = await adapter.get_services()
    bal = await adapter.get_balance()
    provider.balance = bal
    provider.last_sync = datetime.utcnow()

    # Ensure default category
    c_res = await db.execute(select(Category).limit(1))
    default_cat = c_res.scalar_one_or_none()
    if not default_cat:
        default_cat = Category(name="Mạng Xã Hội", slug="mang-xa-hoi", platform="Facebook", icon="Layers")
        db.add(default_cat)
        await db.flush()

    synced_count = 0
    for item in services_data:
        s_res = await db.execute(select(Service).where(
            Service.provider_id == provider.id,
            Service.external_service_id == str(item.service_id),
            Service.is_deleted == False
        ))
        existing_srv = s_res.scalar_one_or_none()

        selling_price = round(item.rate * 1.3, 2)
        dealer_price = round(item.rate * 1.15, 2)

        if existing_srv:
            existing_srv.name = item.name
            existing_srv.provider_price = item.rate
            existing_srv.price = selling_price
            existing_srv.dealer_price = dealer_price
            existing_srv.min_quantity = item.min
            existing_srv.max_quantity = item.max
            srv_obj = existing_srv
        else:
            new_srv = Service(
                provider_id=provider.id,
                category_id=default_cat.id,
                external_service_id=str(item.service_id),
                name=item.name,
                description=f"Dịch vụ từ {provider.name}",
                platform=item.platform or "Facebook",
                service_type="default",
                price=selling_price,
                dealer_price=dealer_price,
                provider_price=item.rate,
                min_quantity=item.min,
                max_quantity=item.max,
                refill_enabled=item.refill,
                cancel_enabled=item.cancel,
                status="ACTIVE",
                created_at=datetime.utcnow()
            )
            db.add(new_srv)
            await db.flush()
            srv_obj = new_srv

        # Ensure mapping
        m_res = await db.execute(select(ProviderServiceMapping).where(
            ProviderServiceMapping.provider_id == provider.id,
            ProviderServiceMapping.external_service_id == str(item.service_id)
        ))
        m = m_res.scalar_one_or_none()
        if not m:
            m = ProviderServiceMapping(
                provider_id=provider.id,
                service_id=srv_obj.id,
                external_service_id=str(item.service_id),
                external_name=item.name,
                external_category=item.category,
                original_rate=item.rate,
                markup_type="PERCENT",
                markup_value=30.0,
                dealer_markup_type="PERCENT",
                dealer_markup_value=15.0,
                auto_round=True,
                round_type="nearest",
                round_unit=10.0,
                selling_price=selling_price,
                dealer_price=dealer_price,
                min_quantity=item.min,
                max_quantity=item.max,
                refill_enabled=item.refill,
                cancel_enabled=item.cancel,
                sync_status="ACTIVE",
                last_synced_at=datetime.utcnow(),
                created_at=datetime.utcnow()
            )
            db.add(m)
        else:
            m.original_rate = item.rate
            m.selling_price = selling_price
            m.dealer_price = dealer_price
            m.last_synced_at = datetime.utcnow()

        synced_count += 1

    invalidate_service_cache()
    await db.commit()
    return ApiResponse(
        data={"synced": synced_count, "balance": bal},
        message=f"Đã đồng bộ {synced_count} dịch vụ từ {provider.name} thành công!"
    )"""

text = text.replace(old_compat, new_compat, 1)

with open("backend/app/routers/admin_providers.py", "w", encoding="utf-8") as f:
    f.write(text)

import ast
ast.parse(open("backend/app/routers/admin_providers.py", "r", encoding="utf-8").read())
print("Compat sync updated and verified!")