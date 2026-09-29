# -*- coding: utf-8 -*-
import pytest
import uuid
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.database.session import AsyncSessionLocal
from app.models.all import User, Coupon, Payment, Transaction, SystemSetting
from app.auth.security import hash_password
from app.payments.service import PaymentService
from sqlalchemy import select


@pytest.mark.asyncio
async def test_coupon_deletion_soft_and_hard():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Obtain admin token via login
        login_res = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        assert login_res.status_code == 200
        admin_token = login_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Test hard delete for unused coupon
        c_code_unused = f"UNUSED_{uuid.uuid4().hex[:6].upper()}"
        create_res = await client.post(
            "/api/v1/admin/coupons",
            json={
                "code": c_code_unused,
                "type": "PERCENT",
                "value": 15,
                "usage_limit": 100,
            },
            headers=headers,
        )
        assert create_res.status_code == 200
        coupon_id = create_res.json()["data"]["id"]

        # Delete unused coupon -> should be hard deleted
        del_res = await client.delete(f"/api/v1/admin/coupons/{coupon_id}", headers=headers)
        assert del_res.status_code == 200
        assert del_res.json()["data"]["action"] == "deleted"

        # Verify not in DB
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Coupon).where(Coupon.id == coupon_id))
            assert res.scalar_one_or_none() is None

        # 2. Test soft delete (deactivation) for coupon that was already used
        c_code_used = f"USED_{uuid.uuid4().hex[:6].upper()}"
        async with AsyncSessionLocal() as db:
            used_coupon = Coupon(
                code=c_code_used,
                type="PERCENT",
                value=20,
                used_count=3,
                usage_limit=100,
                status="ACTIVE",
            )
            db.add(used_coupon)
            await db.commit()
            await db.refresh(used_coupon)
            used_id = used_coupon.id

        del_used_res = await client.delete(f"/api/v1/admin/coupons/{used_id}", headers=headers)
        assert del_used_res.status_code == 200
        assert del_used_res.json()["data"]["action"] == "deactivated"
        assert del_used_res.json()["data"]["status"] == "INACTIVE"

        # Verify still in DB but marked INACTIVE
        async with AsyncSessionLocal() as db:
            res = await db.execute(select(Coupon).where(Coupon.id == used_id))
            c = res.scalar_one_or_none()
            assert c is not None
            assert c.status == "INACTIVE"


@pytest.mark.asyncio
async def test_payment_deposit_and_idempotency():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        uname = f"testuser_{uuid.uuid4().hex[:6]}"
        async with AsyncSessionLocal() as db:
            test_user = User(
                username=uname,
                email=f"{uname}@example.com",
                password_hash=hash_password("Pass123456@"),
                role="USER",
                referral_code=f"REF{uuid.uuid4().hex[:6].upper()}",
                balance=0.0,
                status="ACTIVE",
            )
            db.add(test_user)
            await db.commit()
            await db.refresh(test_user)
            uid = test_user.id

        # Process a deposit via PaymentService
        gw_ref = f"MB_TEST_{uuid.uuid4().hex[:8]}"
        async with AsyncSessionLocal() as db:
            service = PaymentService(db)
            res = await service.process_webhook(
                code=f"NAP {uname.upper()}",
                amount=50000.0,
                gateway_reference=gw_ref,
            )
            assert res["status"] == "success"

        # Check user balance is credited and status is COMPLETED
        async with AsyncSessionLocal() as db:
            u_res = await db.execute(select(User).where(User.id == uid))
            user_db = u_res.scalar_one()
            assert float(user_db.balance) == 50000.0

            # Verify Payment has status COMPLETED
            p_res = await db.execute(select(Payment).where(Payment.gateway_reference == gw_ref))
            payment = p_res.scalar_one()
            assert payment.status == "COMPLETED"

            # Verify Transaction record exists
            t_res = await db.execute(select(Transaction).where(Transaction.user_id == uid))
            tx = t_res.scalar_one()
            assert tx.type == "DEPOSIT"
            assert float(tx.amount) == 50000.0
            assert tx.status == "SUCCESS"

        # Replay webhook with identical gateway reference -> MUST BE IDEMPOTENT (No double spending)
        async with AsyncSessionLocal() as db:
            service = PaymentService(db)
            replay_res = await service.process_webhook(
                code=f"NAP {uname.upper()}",
                amount=50000.0,
                gateway_reference=gw_ref,
            )
            assert replay_res["status"] == "already_processed"

        # Balance must remain exactly 50000.0
        async with AsyncSessionLocal() as db:
            u_res = await db.execute(select(User).where(User.id == uid))
            user_db = u_res.scalar_one()
            assert float(user_db.balance) == 50000.0


@pytest.mark.asyncio
async def test_admin_payment_status_filter():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_res = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        assert login_res.status_code == 200
        admin_token = login_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Query payments with status=SUCCESS -> should match COMPLETED and SUCCESS
        res = await client.get("/api/v1/admin/payments?status=SUCCESS", headers=headers)
        assert res.status_code == 200
        payments = res.json()["data"]
        for p in payments:
            assert p["status"] in ("COMPLETED", "SUCCESS")


@pytest.mark.asyncio
async def test_public_settings_branding_keys():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/settings")
        assert res.status_code == 200
        data = res.json()["data"]
        assert "site_name" in data
        assert "site_title" in data
        assert "site_logo" in data
        assert "brand_color" in data
        assert "auth_banner_text" in data


@pytest.mark.asyncio
async def test_bank_cron_sync_auth_and_secret():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        login_res = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        assert login_res.status_code == 200
        admin_token = login_res.json()["data"]["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Calling as authenticated admin should succeed without secret
        res_admin = await client.get("/bank-cron.php", headers=admin_headers)
        assert res_admin.status_code == 200
        assert res_admin.json()["ok"] is True
