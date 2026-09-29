import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
import sys
import os

sys.path.insert(0, os.path.abspath('backend'))

from app.main import app
from app.database.session import engine, Base, AsyncSessionLocal
from app.models.all import Order

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.main import seed_initial_data
    await seed_initial_data()
    yield

@pytest.mark.asyncio
async def test_thmxh_service_list_and_catalog():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/v1/services")
        assert res.status_code == 200
        data = res.json()["data"]
        # Verify non-TTC (PRO tier) services are present and white-labeled
        thmxh_services = [s for s in data if s.get("server_tag") == "PRO" or not s.get("is_ttc")]
        assert len(thmxh_services) >= 1

@pytest.mark.asyncio
async def test_thmxh_order_placement_and_balance_deduction():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Login demo user
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        assert l_res.status_code == 200
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. Get user balance before
        me_before = await ac.get("/api/v1/auth/me", headers=headers)
        bal_before = me_before.json()["data"]["balance"]

        # 3. Find a THMXH service supporting refill
        srv_res = await ac.get("/api/v1/services")
        thmxh_srv = next((s for s in srv_res.json()["data"] if (s.get("server_tag") == "PRO" or not s.get("is_ttc")) and s.get("refill_enabled") and s.get("min_quantity", 10) <= 100 <= s.get("max_quantity", 100000)), None)
        if not thmxh_srv:
            thmxh_srv = next((s for s in srv_res.json()["data"] if (s.get("server_tag") == "PRO" or not s.get("is_ttc"))), srv_res.json()["data"][0])
            # Enable refill on the chosen service for testing
            async with AsyncSessionLocal() as session:
                from app.models.all import Service
                db_srv = await session.get(Service, thmxh_srv["id"])
                db_srv.refill_enabled = True
                await session.commit()
        assert thmxh_srv is not None

        # 4. Place order
        qty = max(thmxh_srv.get("min_quantity", 100), 100)
        expected_price = (qty / 1000.0) * thmxh_srv["price"]
        order_res = await ac.post(
            "/api/v1/orders",
            json={
                "service_id": thmxh_srv["id"],
                "link": "https://facebook.com/profile.php?id=1000888999",
                "quantity": qty
            },
            headers=headers
        )
        assert order_res.status_code == 200
        order_data = order_res.json()["data"]
        assert order_data["id"] is not None
        assert order_data["status"] in ["PROCESSING", "PENDING", "COMPLETED"]

        # 5. Verify balance was deducted
        me_after = await ac.get("/api/v1/auth/me", headers=headers)
        bal_after = me_after.json()["data"]["balance"]
        assert round(bal_before - bal_after, 2) == round(order_data["price"], 2)

        # 6. Test Refill (requires COMPLETED status)
        async with AsyncSessionLocal() as session:
            o = await session.get(Order, order_data["id"])
            o.status = "COMPLETED"
            await session.commit()

        refill_res = await ac.post(f"/api/v1/orders/{order_data['id']}/refill", headers=headers)
        assert refill_res.status_code == 200
        assert refill_res.json()["success"] is True

        # 7. Test Cancel and refund (on a processing order)
        async with AsyncSessionLocal() as session:
            o = await session.get(Order, order_data["id"])
            o.status = "PROCESSING"
            await session.commit()

        cancel_res = await ac.post(f"/api/v1/orders/{order_data['id']}/cancel", headers=headers)
        assert cancel_res.status_code == 200
        assert cancel_res.json()["success"] is True

        # Verify balance refunded
        me_refunded = await ac.get("/api/v1/auth/me", headers=headers)
        bal_refunded = me_refunded.json()["data"]["balance"]
        assert round(bal_refunded, 2) == round(bal_before, 2)

@pytest.mark.asyncio
async def test_smm_v2_standard_protocol_full_suite():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        assert login.status_code == 200
        generated = await ac.post(
            "/api/v1/auth/generate-api-key",
            headers={"Authorization": f"Bearer {login.json()['data']['access_token']}"},
        )
        demo_api_key = generated.json()["data"]

        # 1. Action: balance
        bal_res = await ac.post("/api/v2", data={"key": demo_api_key, "action": "balance"})
        assert bal_res.status_code == 200
        bal_data = bal_res.json()
        assert "balance" in bal_data
        assert bal_data["currency"] == "VND"

        # 2. Action: services
        srv_res = await ac.post("/api/v2", data={"key": demo_api_key, "action": "services"})
        assert srv_res.status_code == 200
        services = srv_res.json()
        assert isinstance(services, list)
        assert len(services) > 0
        assert "rate" in services[0]
        assert "service" in services[0]

        target_service = next((s["service"] for s in services if s.get("refill") and int(s.get("min", 10)) <= 100 <= int(s.get("max", 100000))), None)
        if not target_service:
            target_service = services[0]["service"]
            async with AsyncSessionLocal() as session:
                from app.models.all import Service
                db_srv = await session.get(Service, int(target_service))
                db_srv.refill_enabled = True
                await session.commit()

        # 3. Action: add (Place order via SMM v2 protocol)
        add_res = await ac.post("/api/v2", data={
            "key": demo_api_key,
            "action": "add",
            "service": target_service,
            "link": "https://facebook.com/smm-v2-test-post",
            "quantity": 100
        })
        assert add_res.status_code == 200
        add_data = add_res.json()
        assert "order" in add_data
        order_id = add_data["order"]

        # 4. Action: status (Single order status)
        st_res = await ac.post("/api/v2", data={
            "key": demo_api_key,
            "action": "status",
            "order": order_id
        })
        assert st_res.status_code == 200
        st_data = st_res.json()
        assert "status" in st_data
        assert "charge" in st_data

        # 5. Action: status (Multiple orders status)
        multi_st = await ac.post("/api/v2", data={
            "key": demo_api_key,
            "action": "status",
            "orders": f"{order_id},999999"
        })
        assert multi_st.status_code == 200
        multi_data = multi_st.json()
        assert str(order_id) in multi_data
        assert "999999" in multi_data

        # 6. Action: refill
        async with AsyncSessionLocal() as session:
            o = await session.get(Order, int(order_id))
            o.status = "COMPLETED"
            await session.commit()

        refill_res = await ac.post("/api/v2", data={
            "key": demo_api_key,
            "action": "refill",
            "order": order_id
        })
        assert refill_res.status_code == 200
        assert "refill" in refill_res.json()

        # 7. Action: refill_status
        ref_st_res = await ac.post("/api/v2", data={
            "key": demo_api_key,
            "action": "refill_status",
            "refill": str(order_id)
        })
        assert ref_st_res.status_code == 200
        assert ref_st_res.json().get("status") in ("Completed", "Pending")

        # 8. Action: cancel
        cancel_res = await ac.post("/api/v2", data={
            "key": demo_api_key,
            "action": "cancel",
            "orders": str(order_id)
        })
        assert cancel_res.status_code == 200
        assert len(cancel_res.json()) >= 1

@pytest.mark.asyncio
async def test_admin_provider_sync_thmxh():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Get providers list
        p_res = await ac.get("/api/v1/admin/providers", headers=headers)
        assert p_res.status_code == 200
        providers = p_res.json()["data"]
        thmxh_p = next((p for p in providers if p["provider_type"] == "thmxh"), None)
        assert thmxh_p is not None

        # Sync THMXH provider
        sync_res = await ac.post(f"/api/v1/admin/providers/{thmxh_p['id']}/sync", headers=headers)
        assert sync_res.status_code == 200
        sync_data = sync_res.json()["data"]
        assert sync_data["synced"] >= 1
