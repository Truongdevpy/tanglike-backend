import uuid
import pytest
from httpx import AsyncClient, ASGITransport
import sys
import os

sys.path.insert(0, os.path.abspath('backend'))

from app.main import app
from app.database.session import AsyncSessionLocal
from app.models.all import Service, User
from sqlalchemy import select

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

async def get_admin_headers(ac: AsyncClient) -> tuple[dict, int]:
    res = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "password123"})
    if res.status_code != 200:
        res = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
    data = res.json()["data"]
    token = data["access_token"]
    user_id = data["user"]["id"]
    return {"Authorization": f"Bearer {token}"}, user_id

async def create_test_user(ac: AsyncClient) -> tuple[dict, str, str, int]:
    uid = uuid.uuid4().hex[:6]
    uname = f"sec_user_{uid}"
    pwd = "Password123@"
    reg_data = {
        "username": uname,
        "email": f"{uname}@example.com",
        "password": pwd,
        "confirm_password": pwd,
        "full_name": f"Security Test {uid}"
    }
    res = await ac.post("/api/v1/auth/register", json=reg_data)
    data = res.json()["data"]
    token = data["access_token"]
    user_id = data["user"]["id"]
    return {"Authorization": f"Bearer {token}"}, uname, pwd, user_id

@pytest.mark.asyncio
async def test_admin_change_user_status_crash_fix():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        admin_headers, admin_id = await get_admin_headers(ac)
        user_headers, uname, pwd, user_id = await create_test_user(ac)

        # 1. Admin cannot ban themselves
        res_self = await ac.put(f"/api/v1/admin/users/{admin_id}/status?status_val=BANNED", headers=admin_headers)
        assert res_self.status_code == 400
        msg = res_self.json().get("error", {}).get("message", "")
        assert "Không thể tự khóa tài khoản của chính mình." in msg

        # 2. Admin bans another user -> works without 500 NameError
        res_ban = await ac.put(f"/api/v1/admin/users/{user_id}/status?status_val=BANNED", headers=admin_headers)
        assert res_ban.status_code == 200
        assert res_ban.json()["data"]["status"] == "BANNED"

        # 3. Banned user cannot use their old token
        res_check = await ac.get("/api/v1/users/me", headers=user_headers)
        assert res_check.status_code in [401, 403]

@pytest.mark.asyncio
async def test_admin_toggle_pro_crash_fix():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        admin_headers, admin_id = await get_admin_headers(ac)
        user_headers, uname, pwd, user_id = await create_test_user(ac)

        # 1. Admin cannot revoke their own PRO
        res_self = await ac.put(f"/api/v1/admin/users/{admin_id}/pro?is_pro=false", headers=admin_headers)
        assert res_self.status_code == 400
        msg = res_self.json().get("error", {}).get("message", "")
        assert "Tài khoản Quản trị viên luôn có quyền PRO." in msg

        # 2. Admin toggles PRO on another user -> works without 500 NameError
        res_pro = await ac.put(f"/api/v1/admin/users/{user_id}/pro?is_pro=true", headers=admin_headers)
        assert res_pro.status_code == 200
        assert res_pro.json()["data"]["is_pro"] is True

@pytest.mark.asyncio
async def test_admin_delete_user_crash_fix():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        admin_headers, admin_id = await get_admin_headers(ac)
        user_headers, uname, pwd, user_id = await create_test_user(ac)

        # 1. Admin cannot delete themselves
        res_self = await ac.delete(f"/api/v1/admin/users/{admin_id}", headers=admin_headers)
        assert res_self.status_code == 400
        msg = res_self.json().get("error", {}).get("message", "")
        assert "Không thể tự xóa tài khoản Quản trị viên của chính mình." in msg

        # 2. Admin deletes another user -> works without 500 NameError
        res_del = await ac.delete(f"/api/v1/admin/users/{user_id}", headers=admin_headers)
        assert res_del.status_code == 200

        # Deleted user session is revoked
        res_check = await ac.get("/api/v1/users/me", headers=user_headers)
        assert res_check.status_code in [401, 403]

@pytest.mark.asyncio
async def test_user_api_and_smm_v2_filter_deleted_services():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_headers, uname, pwd, user_id = await create_test_user(ac)

        # Generate API key for user via auth route
        key_res = await ac.post("/api/v1/auth/generate-api-key", headers=user_headers)
        assert key_res.status_code == 200
        raw_key = key_res.json()["data"]

        # Mark a service as is_deleted = True in DB
        async with AsyncSessionLocal() as db:
            s_res = await db.execute(select(Service).where(Service.status == "ACTIVE").limit(1))
            srv = s_res.scalar_one_or_none()
            assert srv is not None
            deleted_srv_id = srv.id
            srv.is_deleted = True
            await db.commit()

        try:
            # 1. Check User REST API: /api/v1/user-api/services
            api_headers = {"X-API-KEY": raw_key}
            res_user_api = await ac.get("/api/v1/user-api/services", headers=api_headers)
            assert res_user_api.status_code == 200
            services_data = res_user_api.json()
            returned_ids = [s["service"] for s in services_data]
            assert deleted_srv_id not in returned_ids

            # 2. Check SMM v2 API: /api/v2 action=services
            res_smm = await ac.post("/api/v2", json={"key": raw_key, "action": "services"})
            assert res_smm.status_code == 200
            smm_services = res_smm.json()
            smm_returned_ids = [s["service"] for s in smm_services]
            assert deleted_srv_id not in smm_returned_ids
        finally:
            # Revert service back
            async with AsyncSessionLocal() as db:
                s_res = await db.execute(select(Service).where(Service.id == deleted_srv_id))
                srv = s_res.scalar_one_or_none()
                if srv:
                    srv.is_deleted = False
                    await db.commit()

@pytest.mark.asyncio
async def test_order_service_rejects_malicious_links():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_headers, uname, pwd, user_id = await create_test_user(ac)

        # Deposit balance for user to test order placement
        admin_headers, _ = await get_admin_headers(ac)
        adj_res = await ac.put(f"/api/v1/admin/users/{user_id}/balance", json={"amount": 100000, "description": "Test Deposit"}, headers=admin_headers)
        assert adj_res.status_code == 200

        key_res = await ac.post("/api/v1/auth/generate-api-key", headers=user_headers)
        raw_key = key_res.json()["data"]

        # Get an active service
        srv_res = await ac.get("/api/v1/services")
        active_srv = srv_res.json()["data"][0]
        srv_id = active_srv["id"]

        # 1. Test javascript: protocol injection via user-api
        res_malicious = await ac.post(
            "/api/v1/user-api/order",
            headers={"X-API-KEY": raw_key},
            json={"service": srv_id, "link": "javascript:alert(1)", "quantity": 100}
        )
        assert res_malicious.status_code == 400
        msg1 = res_malicious.json().get("error", {}).get("message", "")
        assert "Đường liên kết không hợp lệ" in msg1

        # 2. Test CRLF injection via user-api
        res_crlf = await ac.post(
            "/api/v1/user-api/order",
            headers={"X-API-KEY": raw_key},
            json={"service": srv_id, "link": "https://example.com/test\r\nX-Injected: true", "quantity": 100}
        )
        assert res_crlf.status_code == 400
        msg2 = res_crlf.json().get("error", {}).get("message", "")
        assert "Đường liên kết không hợp lệ" in msg2

        # 3. Test SMM v2 action=add with malicious link
        res_smm_malicious = await ac.post(
            "/api/v2",
            json={"key": raw_key, "action": "add", "service": srv_id, "link": "data:text/html,<script>alert(1)</script>", "quantity": 100}
        )
        assert res_smm_malicious.status_code == 200
        assert "error" in res_smm_malicious.json()
        assert "Đường liên kết không hợp lệ" in res_smm_malicious.json()["error"]

@pytest.mark.asyncio
async def test_user_deactivation_invalidates_tokens():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_headers, uname, pwd, user_id = await create_test_user(ac)

        # Deactivate user account
        deact_res = await ac.post(
            "/api/v1/users/me/deactivate",
            headers=user_headers,
            json={"password": pwd, "reason": "Privacy test"}
        )
        assert deact_res.status_code == 200
        assert deact_res.json()["data"] is True

        # Existing JWT token must now be rejected
        res_after = await ac.get("/api/v1/users/me", headers=user_headers)
        assert res_after.status_code in [401, 403]
