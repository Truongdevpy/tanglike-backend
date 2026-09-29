import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
import sys
import os

sys.path.insert(0, os.path.abspath('backend'))

from app.main import app
from app.database.session import engine, Base

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
async def test_health_check():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/health")
        assert res.status_code == 200
        assert res.json()["status"] == "healthy"

@pytest.mark.asyncio
async def test_auth_register_and_login():
    uid = uuid.uuid4().hex[:6]
    uname = f"user_{uid}"
    email = f"{uname}@example.com"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_data = {
            "username": uname,
            "email": email,
            "password": "password123",
            "confirm_password": "password123",
            "full_name": f"User {uid}"
        }
        res = await ac.post("/api/v1/auth/register", json=reg_data)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        token = data["data"]["access_token"]
        assert token is not None

        login_res = await ac.post("/api/v1/auth/login", json={
            "username": uname,
            "password": "password123"
        })
        assert login_res.status_code == 200
        assert login_res.json()["success"] is True

        me_res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me_res.status_code == 200
        assert me_res.json()["data"]["username"] == uname

@pytest.mark.asyncio
async def test_services_and_categories():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        cat_res = await ac.get("/api/v1/services/categories")
        assert cat_res.status_code == 200
        categories = cat_res.json()["data"]
        assert len(categories) > 0

        srv_res = await ac.get("/api/v1/services")
        assert srv_res.status_code == 200
        services = srv_res.json()["data"]
        assert len(services) > 0

@pytest.mark.asyncio
async def test_deposit_and_webhook():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]

        dep_res = await ac.post(
            "/api/v1/payments/deposit",
            json={"amount": 100000, "payment_method": "VIETQR"},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert dep_res.status_code == 200
        dep_data = dep_res.json()["data"]
        tx_code = dep_data["transaction_code"]
        assert "NAP" in tx_code

        # Simulate bank payment webhook
        sim_res = await ac.post(
            "/api/v1/payments/simulate-webhook",
            params={"transaction_code": tx_code, "amount": 100000},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert sim_res.status_code == 200
        assert sim_res.json()["success"] is True

        # Duplicate webhook test (Idempotency)
        dup_res = await ac.post(
            "/api/v1/payments/simulate-webhook",
            params={"transaction_code": tx_code, "amount": 100000},
            headers={"Authorization": f"Bearer {token}"}
        )
        assert dup_res.status_code == 200
        assert dup_res.json()["data"]["status"] == "already_processed"

@pytest.mark.asyncio
async def test_order_creation_and_balance_deduction():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]

        srv_res = await ac.get("/api/v1/services")
        service_id = next(s["id"] for s in srv_res.json()["data"] if s.get("min_quantity", 10) <= 100 <= s.get("max_quantity", 100000))

        order_res = await ac.post(
            "/api/v1/orders",
            json={
                "service_id": service_id,
                "link": "https://facebook.com/post/123456",
                "quantity": 100
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        assert order_res.status_code == 200
        order_data = order_res.json()["data"]
        assert order_data["id"] is not None
        assert order_data["status"] in ["PROCESSING", "PENDING", "COMPLETED"]

@pytest.mark.asyncio
async def test_insufficient_balance():
    uid = uuid.uuid4().hex[:6]
    uname = f"broke_{uid}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        reg_res = await ac.post("/api/v1/auth/register", json={
            "username": uname,
            "email": f"{uname}@example.com",
            "password": "password123",
            "confirm_password": "password123"
        })
        assert reg_res.status_code == 200
        token = reg_res.json()["data"]["access_token"]

        srv_res = await ac.get("/api/v1/services")
        service_id = next(s["id"] for s in srv_res.json()["data"] if s.get("min_quantity", 10) <= 100 <= s.get("max_quantity", 100000))

        order_res = await ac.post(
            "/api/v1/orders",
            json={
                "service_id": service_id,
                "link": "https://facebook.com/post/123456",
                "quantity": 10000
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        assert order_res.status_code == 400
        assert "Số dư không đủ" in order_res.json()["error"]["message"]

@pytest.mark.asyncio
async def test_admin_dashboard_and_balance_adjustment():
    uid = uuid.uuid4().hex[:6]
    uname = f"target_{uid}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create target user
        reg_res = await ac.post("/api/v1/auth/register", json={
            "username": uname,
            "email": f"{uname}@example.com",
            "password": "password123",
            "confirm_password": "password123"
        })
        user_id = reg_res.json()["data"]["user"]["id"]

        # Login admin
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]

        # Get dashboard stats
        stats_res = await ac.get("/api/v1/admin/dashboard-stats", headers={"Authorization": f"Bearer {admin_token}"})
        assert stats_res.status_code == 200
        assert stats_res.json()["data"]["total_users"] > 0

        # Adjust balance of target user
        adj_res = await ac.put(
            f"/api/v1/admin/users/{user_id}/balance",
            json={"amount": 50000, "description": "Thưởng thành viên mới"},
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert adj_res.status_code == 200
        assert adj_res.json()["data"]["balance"] == 50000.0

@pytest.mark.asyncio
async def test_user_api_with_api_key():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]
        generated = await ac.post("/api/v1/auth/generate-api-key", headers={"Authorization": f"Bearer {token}"})
        api_key = generated.json()["data"]

        bal_res = await ac.get("/api/v1/user-api/balance", headers={"X-API-KEY": api_key})
        assert bal_res.status_code == 200
        assert "balance" in bal_res.json()

        srv_res = await ac.get("/api/v1/user-api/services", headers={"X-API-KEY": api_key})
        assert srv_res.status_code == 200
        assert len(srv_res.json()) > 0

        orders_res = await ac.get("/api/v1/user-api/orders", headers={"X-API-KEY": api_key})
        assert orders_res.status_code == 200
        assert "orders" in orders_res.json()

@pytest.mark.asyncio
async def test_rbac_user_forbidden_from_admin():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]

        # Regular user attempts to access admin endpoint
        res = await ac.get("/api/v1/admin/dashboard-stats", headers={"Authorization": f"Bearer {token}"})
        assert res.status_code == 403

@pytest.mark.asyncio
async def test_mass_order_and_drip_feed():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]

        srv_res = await ac.get("/api/v1/services")
        service_id = next(s["id"] for s in srv_res.json()["data"] if s.get("min_quantity", 10) <= 100 <= s.get("max_quantity", 100000))

        # 1. Mass order
        mass_res = await ac.post(
            "/api/v1/orders/mass",
            json={
                "orders": [
                    {"service_id": service_id, "link": "https://facebook.com/post/101", "quantity": 100},
                    {"service_id": service_id, "link": "https://facebook.com/post/102", "quantity": 100}
                ]
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        assert mass_res.status_code == 200
        mass_data = mass_res.json()["data"]
        assert mass_data["total"] == 2
        assert mass_data["success"] >= 1

        # 2. Drip feed order
        drip_res = await ac.post(
            "/api/v1/orders/drip-feed",
            json={
                "service_id": service_id,
                "link": "https://facebook.com/post/202",
                "runs": 5,
                "quantity_per_run": 20,
                "interval_minutes": 15
            },
            headers={"Authorization": f"Bearer {token}"}
        )
        assert drip_res.status_code == 200
        drip_data = drip_res.json()["data"]
        assert drip_data["quantity"] == 100
        assert drip_data["is_dripfeed"] is True

@pytest.mark.asyncio
async def test_admin_category_crud_and_settings():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Create Category
        uid = uuid.uuid4().hex[:4]
        cat_create = await ac.post("/api/v1/admin/categories", json={
            "name": f"Threads Tương Tác {uid}",
            "slug": f"threads-{uid}",
            "platform": "Threads",
            "icon": "Layers",
            "sort_order": 99
        }, headers=headers)
        assert cat_create.status_code == 200
        cat_id = cat_create.json()["data"]["id"]

        # 2. Update Category
        cat_update = await ac.put(f"/api/v1/admin/categories/{cat_id}", json={
            "name": f"Threads VIP {uid}"
        }, headers=headers)
        assert cat_update.status_code == 200
        assert cat_update.json()["data"]["name"] == f"Threads VIP {uid}"

        # 3. Delete Category
        cat_del = await ac.delete(f"/api/v1/admin/categories/{cat_id}", headers=headers)
        assert cat_del.status_code == 200

        # 4. Settings GET and PUT
        settings_get = await ac.get("/api/v1/admin/settings", headers=headers)
        assert settings_get.status_code == 200
        assert "site_name" in settings_get.json()["data"]

        settings_put = await ac.put("/api/v1/admin/settings", json={
            "settings": {"site_name": "TangLike SMM Enterprise 2026"}
        }, headers=headers)
        assert settings_put.status_code == 200
        assert settings_put.json()["data"]["site_name"] == "TangLike SMM Enterprise 2026"

@pytest.mark.asyncio
async def test_admin_refund_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        user_token = l_res.json()["data"]["access_token"]

        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]

        # Place an order
        srv_res = await ac.get("/api/v1/services")
        service_id = next(s["id"] for s in srv_res.json()["data"] if s.get("min_quantity", 10) <= 100 <= s.get("max_quantity", 100000))
        order_res = await ac.post(
            "/api/v1/orders",
            json={"service_id": service_id, "link": "https://facebook.com/post/999", "quantity": 100},
            headers={"Authorization": f"Bearer {user_token}"}
        )
        assert order_res.status_code == 200
        order_id = order_res.json()["data"]["id"]

        # Admin refunds order
        ref_res = await ac.post(
            f"/api/v1/admin/refund/{order_id}",
            json={"reason": "Kiểm thử hoàn tiền tự động"},
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        assert ref_res.status_code == 200
        assert ref_res.json()["data"]["status"] == "success"
        assert ref_res.json()["data"]["order_id"] == order_id
