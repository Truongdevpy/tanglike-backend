import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.mark.asyncio
async def test_cron_sync_admin_bypass_and_json_responses():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Invalid secret returns 403 JSON
        res_bad = await ac.get("/api/v1/payments/cron/bank-sync?secret=invalid_secret_123")
        assert res_bad.status_code == 403
        data_bad = res_bad.json()
        assert data_bad.get("ok") is False or data_bad.get("success") is False

        # 2. Admin token allows access
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        assert admin_login.status_code == 200
        admin_token = admin_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Admin access without secret
        res_admin = await ac.get("/api/v1/payments/cron/bank-sync", headers=headers)
        assert res_admin.status_code == 200
        data_admin = res_admin.json()
        assert "ok" in data_admin
        assert "message" in data_admin

@pytest.mark.asyncio
async def test_payments_admin_bank_test_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Non-authenticated user rejected
        res_unauth = await ac.post("/api/v1/payments/admin/bank-test", json={})
        assert res_unauth.status_code == 401

        # Admin user allowed
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        assert admin_login.status_code == 200
        admin_token = admin_login.json()["data"]["access_token"]
        res_admin = await ac.post("/api/v1/payments/admin/bank-test", json={}, headers={"Authorization": f"Bearer {admin_token}"})
        assert res_admin.status_code == 200
        assert res_admin.json()["success"] is True
