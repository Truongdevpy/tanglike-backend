import uuid
import pytest
from httpx import AsyncClient, ASGITransport
import sys
import os

sys.path.insert(0, os.path.abspath('backend'))

from app.main import app
from app.utils.network import async_retry

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

async def get_admin_headers(ac: AsyncClient) -> dict:
    res = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "password123"})
    if res.status_code != 200:
        res = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
    token = res.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}

async def create_test_user(ac: AsyncClient) -> tuple[dict, str, str]:
    uid = uuid.uuid4().hex[:6]
    uname = f"gdpr_{uid}"
    pwd = "password123"
    reg_data = {
        "username": uname,
        "email": f"{uname}@example.com",
        "password": pwd,
        "confirm_password": pwd,
        "full_name": f"GDPR User {uid}"
    }
    res = await ac.post("/api/v1/auth/register", json=reg_data)
    token = res.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}, uname, pwd

@pytest.mark.asyncio
async def test_health_readiness_probe():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/health/ready")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "ready"
        assert data["database"] == "connected"

@pytest.mark.asyncio
async def test_payload_limit_middleware_blocks_oversized_requests():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Simulate payload exceeding 10MB limit via content-length header
        headers = {"content-length": "15000000"}  # ~15MB
        res = await ac.post("/api/v1/auth/login", headers=headers, json={"username": "x", "password": "y"})
        assert res.status_code == 413
        assert res.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"

@pytest.mark.asyncio
async def test_gdpr_data_export():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers, uname, _ = await create_test_user(ac)
        res = await ac.get("/api/v1/users/me/export-data", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        export = data["data"]
        assert export["compliance"] == "GDPR Article 20 (Data Portability)"
        assert export["user_profile"]["username"] == uname
        assert "orders" in export
        assert "transactions" in export
        assert "payments" in export

@pytest.mark.asyncio
async def test_gdpr_account_deactivation_and_erasure():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        headers, uname, pwd = await create_test_user(ac)

        # 1. Deactivate with wrong password -> 400
        bad_res = await ac.post("/api/v1/users/me/deactivate", headers=headers, json={"password": "wrong"})
        assert bad_res.status_code == 400

        # 2. Deactivate with correct password -> 200
        ok_res = await ac.post("/api/v1/users/me/deactivate", headers=headers, json={"password": pwd, "reason": "Moving abroad"})
        assert ok_res.status_code == 200
        assert ok_res.json()["data"] is True

        # 3. Subsequent authenticated calls must be rejected (403 Forbidden)
        me_res = await ac.get("/api/v1/auth/me", headers=headers)
        assert me_res.status_code == 403

        # 4. Subsequent login must be rejected (403 Forbidden)
        login_res = await ac.post("/api/v1/auth/login", json={"username": uname, "password": pwd})
        assert login_res.status_code == 403

@pytest.mark.asyncio
async def test_soft_delete_service_and_catalog_filtering():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_hdrs = await get_admin_headers(ac)

        # 1. Create a new service
        srv_name = f"Test Soft Delete {uuid.uuid4().hex[:4]}"
        create_res = await ac.post("/api/v1/admin/services", headers=admin_hdrs, json={
            "category_id": 1,
            "name": srv_name,
            "description": "Temp service for soft delete test",
            "platform": "Facebook",
            "service_type": "likes",
            "price": 20.0,
            "min_quantity": 50,
            "max_quantity": 1000
        })
        assert create_res.status_code == 200
        srv_id = create_res.json()["data"]["id"]

        # 2. Verify service is visible in public catalog
        list_res = await ac.get("/api/v1/services")
        names = [s["name"] for s in list_res.json()["data"]]
        assert srv_name in names

        # 3. Admin soft-deletes service
        del_res = await ac.delete(f"/api/v1/admin/services/{srv_id}", headers=admin_hdrs)
        assert del_res.status_code == 200

        # 4. Public catalog no longer returns the soft-deleted service
        list_after = await ac.get("/api/v1/services")
        names_after = [s["name"] for s in list_after.json()["data"]]
        assert srv_name not in names_after

        # 5. Direct lookup returns 404
        direct_res = await ac.get(f"/api/v1/services/{srv_id}")
        assert direct_res.status_code == 404

@pytest.mark.asyncio
async def test_network_async_retry_resilience():
    call_attempts = 0
    import httpx

    async def transient_mock_call():
        nonlocal call_attempts
        call_attempts += 1
        if call_attempts < 3:
            raise httpx.ConnectError("Simulated DNS timeout")
        return {"status": "success", "recovered_on": call_attempts}

    res = await async_retry(transient_mock_call, max_retries=3, initial_delay=0.05)
    assert res["status"] == "success"
    assert call_attempts == 3
