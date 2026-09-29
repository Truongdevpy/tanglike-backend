import secrets

import pytest

from httpx import AsyncClient, ASGITransport

from app.main import app



@pytest.mark.asyncio

async def test_banking_config_requires_admin():

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        # 1. Unauthenticated gets 401

        res_unauth = await ac.get("/api/v1/admin/banking/config")

        assert res_unauth.status_code == 401



        # 2. Regular user gets 403

        demo_login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})

        demo_token = demo_login.json()["data"]["access_token"]

        res_user = await ac.get("/api/v1/admin/banking/config", headers={"Authorization": f"Bearer {demo_token}"})

        assert res_user.status_code == 403



@pytest.mark.asyncio

async def test_admin_get_and_update_banking_config():

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})

        admin_token = admin_login.json()["data"]["access_token"]

        headers = {"Authorization": f"Bearer {admin_token}"}



        # 1. Get initial config

        res = await ac.get("/api/v1/admin/banking/config", headers=headers)

        assert res.status_code == 200

        cfg = res.json()["data"]

        assert "enabled" in cfg

        assert "api_type" in cfg

        assert "bank_name" in cfg

        assert "account_number" in cfg

        assert "vietqr_preview_url" in cfg



        # 2. Validation check: enabling without STK should fail

        fail_update = await ac.put(

            "/api/v1/admin/banking/config",

            json={"enabled": True, "account_number": "", "account_name": ""},

            headers=headers

        )

        assert fail_update.status_code == 400



        # 3. Successful update with credentials

        update_res = await ac.put(

            "/api/v1/admin/banking/config",

            json={

                "enabled": True,

                "api_type": "thueapi",

                "bank_name": "MB Bank",

                "bank_code": "MBBANK",

                "bank_bin": "970422",

                "account_number": "0002406200504",

                "account_name": "NGUYEN MINH",

                "token": "test_token_secret_123",

                "content_prefix": "NAP",

                "min_deposit": 20000,

                "vietqr_template": "compact2"

            },

            headers=headers

        )

        assert update_res.status_code == 200

        updated = update_res.json()["data"]

        assert updated["enabled"] is True

        assert updated["account_number"] == "0002406200504"

        assert updated["account_name"] == "NGUYEN MINH"

        assert updated["min_deposit"] == 20000

        assert updated["has_token"] is True

        assert "test_token_secret_123" not in updated["token_masked"] # Must be masked!

        assert "0002406200504" in updated["vietqr_preview_url"]



        # 4. Check banking stats

        stats_res = await ac.get("/api/v1/admin/banking/stats", headers=headers)

        assert stats_res.status_code == 200

        stats = stats_res.json()["data"]

        assert "total" in stats

        assert "credited_count" in stats



        # 5. Check banking transactions list

        tx_res = await ac.get("/api/v1/admin/banking/transactions", headers=headers)

        assert tx_res.status_code == 200

        assert isinstance(tx_res.json()["data"], list)



        # 6. Check public bank-info endpoint

        pub_res = await ac.get("/api/v1/payments/bank-info")

        assert pub_res.status_code == 200

        pub_info = pub_res.json()["data"]

        assert pub_info["account_number"] == "0002406200504"

        assert pub_info["account_name"] == "NGUYEN MINH"

        assert pub_info["min_deposit"] == 20000



        # 7. Check user deposit uses new config

        demo_login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})

        demo_token = demo_login.json()["data"]["access_token"]

        dep_res = await ac.post(

            "/api/v1/payments/deposit",

            json={"amount": 50000, "payment_method": "VIETQR"},

            headers={"Authorization": f"Bearer {demo_token}"}

        )

        assert dep_res.status_code == 200

        dep_data = dep_res.json()["data"]

        assert dep_data["bank_account_no"] == "0002406200504"

        assert dep_data["bank_account_holder"] == "NGUYEN MINH"

        assert "0002406200504" in dep_data["qr_url"]



        # 8. Check test deposit below min_deposit is rejected

        dep_fail = await ac.post(

            "/api/v1/payments/deposit",

            json={"amount": 15000, "payment_method": "VIETQR"},

            headers={"Authorization": f"Bearer {demo_token}"}

        )

        assert dep_fail.status_code == 400





@pytest.mark.asyncio

async def test_cron_bank_sync_endpoint():

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})

        admin_token = admin_login.json()["data"]["access_token"]

        headers = {"Authorization": f"Bearer {admin_token}"}



        # Set a cron_secret

        await ac.put(

            "/api/v1/admin/banking/config",

            json={"cron_secret": "my_super_secret_cron_key"},

            headers=headers

        )



        # 1. Calling cron without secret gets 403

        res_no_sec = await ac.get("/api/v1/payments/cron/bank-sync")

        assert res_no_sec.status_code == 403



        # 2. Calling cron with wrong secret gets 403

        res_wrong = await ac.get("/api/v1/payments/cron/bank-sync?secret=wrong")

        assert res_wrong.status_code == 403



        # 3. Calling cron with correct secret succeeds

        res_ok = await ac.get("/api/v1/payments/cron/bank-sync?secret=my_super_secret_cron_key")

        assert res_ok.status_code == 200

        assert res_ok.json()["success"] is True



        # 4. Calling via header X-Bank-Cron-Secret succeeds

        res_header = await ac.post(

            "/api/v1/payments/cron/bank-sync",

            headers={"X-Bank-Cron-Secret": "my_super_secret_cron_key"}

        )

        assert res_header.status_code == 200



@pytest.mark.asyncio

async def test_auto_match_deposit_by_username_content():

    from app.database.session import AsyncSessionLocal

    from app.models.all import User

    from app.payments.service import PaymentService

    from sqlalchemy import select



    async with AsyncSessionLocal() as db:

        # Get demo user balance before

        u_res = await db.execute(select(User).where(User.username == "demo"))

        demo_user = u_res.scalar_one()

        bal_before = float(demo_user.balance)



        # Incoming bank transaction directly transferred with memo "NAP DEMO"

        service = PaymentService(db)

        res = await service.process_webhook(

            code="NAP DEMO CHUYEN KHOAN",

            amount=75000,

            gateway_reference=f"REF_TEST_{secrets.token_hex(8)}",

            raw_data={"test": True}

        )

        assert res["status"] == "success"



        # Check user balance after

        u_after = await db.execute(select(User).where(User.username == "demo"))

        user_after = u_after.scalar_one()

        assert round(float(user_after.balance), 2) == round(bal_before + 75000, 2)



@pytest.mark.asyncio
async def test_admin_banking_test_fetch_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Unauthenticated -> 401
        res_unauth = await ac.post("/api/v1/admin/banking/test-fetch", json={})
        assert res_unauth.status_code == 401

        # 2. Regular user -> 403
        demo_login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        assert demo_login.status_code == 200
        demo_token = demo_login.json()["data"]["access_token"]
        res_user = await ac.post(
            "/api/v1/admin/banking/test-fetch",
            headers={"Authorization": f"Bearer {demo_token}"},
            json={}
        )
        assert res_user.status_code == 403

        # 3. Admin user -> 200
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        assert admin_login.status_code == 200
        admin_token = admin_login.json()["data"]["access_token"]
        res_admin = await ac.post(
            "/api/v1/admin/banking/test-fetch",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"account_number": "0868133346", "bank_code": "MBBANK"}
        )
        assert res_admin.status_code == 200
        body = res_admin.json()
        assert body["success"] is True
        assert "data" in body
        assert "total_found" in body["data"]
        assert "transactions" in body["data"]
        assert isinstance(body["data"]["transactions"], list)

@pytest.mark.asyncio
async def test_bank_cron_response_structure_and_aliases():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Ensure a known secret is configured
        await ac.put(
            "/api/v1/admin/banking/config",
            json={"cron_secret": "test_secret_12345"},
            headers=headers
        )

        for endpoint in ["/api/v1/payments/cron/bank-sync", "/api/bank-cron.php", "/bank-cron.php"]:
            res = await ac.get(f"{endpoint}?secret=test_secret_12345")
            assert res.status_code == 200
            data = res.json()
            assert data.get("ok") is True
            assert "message" in data
            assert "processed" in data
            assert "credited" in data
            assert "ignored" in data
            assert "failed" in data
            assert "duplicates" in data
            assert data.get("success") is True
