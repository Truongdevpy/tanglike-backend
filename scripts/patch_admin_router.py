with open("backend/app/routers/admin.py", "r", encoding="utf-8") as f:
    code = f.read()

# 1. Replace get_dashboard_stats
old_stats = """# 1. Admin Dashboard Stats
@router.get("/dashboard-stats", response_model=ApiResponse[DashboardStats])
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    user_count = (await db.execute(select(func.count(User.id)))).scalar() or 0
    active_users = (await db.execute(select(func.count(User.id)).where(User.status == "ACTIVE"))).scalar() or 0
    
    order_count = (await db.execute(select(func.count(Order.id)))).scalar() or 0
    completed_orders = (await db.execute(select(func.count(Order.id)).where(Order.status == "COMPLETED"))).scalar() or 0
    pending_orders = (await db.execute(select(func.count(Order.id)).where(Order.status == "PENDING"))).scalar() or 0
    processing_orders = (await db.execute(select(func.count(Order.id)).where(Order.status == "PROCESSING"))).scalar() or 0

    revenue = (await db.execute(select(func.sum(Order.price)).where(Order.status != "REFUNDED"))).scalar() or 0.0
    open_tickets = (await db.execute(select(func.count(SupportConversation.id)).where(SupportConversation.status == "OPEN"))).scalar() or 0
    total_user_balance = (await db.execute(select(func.sum(User.balance)))).scalar() or 0.0

    return ApiResponse(
        data=DashboardStats(
            total_users=user_count,
            active_users=active_users,
            total_orders=order_count,
            completed_orders=completed_orders,
            pending_orders=pending_orders,
            processing_orders=processing_orders,
            revenue=float(revenue),
            profit=round(float(revenue) * 0.35, 2), 
            open_tickets=open_tickets,
            user_balance=float(total_user_balance)
        )
    )"""

new_stats = """# 1. Admin Dashboard Stats
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
    \"\"\"Xóa dữ liệu thử nghiệm/mô phỏng, bảo toàn tài khoản Admin và đưa số liệu về thực tế 100%.\"\"\"
    await db.execute(delete(User).where(User.username != "admin"))
    await db.execute(delete(Order))
    await db.execute(delete(Transaction))
    await db.execute(delete(Payment))
    await db.execute(delete(RefillRequest))
    await db.execute(delete(SupportMessage))
    await db.execute(delete(SupportConversation))
    await db.execute(delete(Provider).where(Provider.id >= 3))
    await db.execute(delete(PasswordResetToken))
    await db.execute(delete(EmailVerificationToken))
    await db.execute(delete(JobRun))
    await db.execute(delete(AuditLog))
    await db.execute(delete(PriceSyncLog))
    await db.commit()
    return ApiResponse(data={"status": "cleaned"}, message="Đã xóa toàn bộ dữ liệu mẫu. Hệ thống đã sạch và sẵn sàng hoạt động với số liệu thật!")"""

if old_stats in code:
    code = code.replace(old_stats, new_stats)
    print("Replaced get_dashboard_stats!")
else:
    print("WARNING: old_stats not found in code")

# 2. Update admin_list_orders to attach provider_price, provider_cost, profit
old_orders = """    service_ids = {o.service_id for o in orders}
    services_map = {}
    if service_ids:
        s_res = await db.execute(select(Service).where(Service.id.in_(service_ids)))
        for s in s_res.scalars().all():
            services_map[s.id] = (s.name, s.platform)

    output = []
    for o in orders:
        ord_resp = OrderResponse.model_validate(o)
        if o.service_id in services_map:
            name, plat = services_map[o.service_id]
            ord_resp.service_name = name
            ord_resp.platform = plat
        output.append(ord_resp)"""

new_orders = """    service_ids = {o.service_id for o in orders}
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
        output.append(ord_resp)"""

if old_orders in code:
    code = code.replace(old_orders, new_orders)
    print("Replaced admin_list_orders!")
else:
    print("WARNING: old_orders not found in code")

with open("backend/app/routers/admin.py", "w", encoding="utf-8") as f:
    f.write(code)

print("Saved backend/app/routers/admin.py")