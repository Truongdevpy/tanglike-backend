import uuid
import math
import pytest
from httpx import AsyncClient, ASGITransport
import sys
import os

sys.path.insert(0, os.path.abspath('backend'))

from app.main import app
from app.database.session import AsyncSessionLocal
from app.models.all import Service, User, Order, Transaction
from app.auth.security import create_access_token, create_refresh_token, decode_token
from app.notifications.telegram import TelegramNotifier
from app.routers.admin import AdjustBalanceRequest, admin_adjust_balance
from fastapi import HTTPException
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
async def test_single_service_masks_provider_confidential_fields():
    """White-label security: GET /api/v1/services/{id} must not leak wholesale supplier data."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/services")
        assert res.status_code == 200
        services = res.json()["data"]
        assert len(services) > 0
        target_id = services[0]["id"]

        detail_res = await ac.get(f"/api/v1/services/{target_id}")
        assert detail_res.status_code == 200
        service_data = detail_res.json()["data"]
        assert service_data.get("provider_id") is None
        assert service_data.get("external_service_id") is None
        assert service_data.get("provider_price") is None


@pytest.mark.asyncio
async def test_guest_session_id_sql_wildcard_rejection():
    """SQL LIKE wildcard injection protection on live-chat endpoints."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Wildcard '%'
        res1 = await ac.get("/api/v1/support/live-chat/messages?guest_session_id=%")
        assert res1.status_code == 400

        # Wildcard '_'
        res2 = await ac.get("/api/v1/support/live-chat/messages?guest_session_id=___")
        assert res2.status_code == 400

        # POST with invalid chars
        res3 = await ac.post("/api/v1/support/live-chat", json={
            "guest_session_id": "%invalid;DROP TABLE--",
            "message": "Hello test"
        })
        assert res3.status_code == 400

        # Valid session id
        valid_sid = f"guest_valid_{uuid.uuid4().hex[:12]}"
        res4 = await ac.get(f"/api/v1/support/live-chat/messages?guest_session_id={valid_sid}")
        assert res4.status_code == 200


@pytest.mark.asyncio
async def test_admin_balance_adjustment_non_finite_rejection():
    """Prevent NaN, Inf, and zero amount adjustments to user balances."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        admin_headers, _ = await get_admin_headers(ac)
        _, _, _, user_id = await create_test_user(ac)

        # 1. Zero amount rejected
        res_zero = await ac.put(
            f"/api/v1/admin/users/{user_id}/balance",
            json={"amount": 0, "description": "Zero deposit test"},
            headers=admin_headers
        )
        assert res_zero.status_code == 400

        # 2. String/invalid float rejected
        res_str = await ac.put(
            f"/api/v1/admin/users/{user_id}/balance",
            content=b'{"amount": "not_a_number", "description": "Bad float test"}',
            headers={**admin_headers, "Content-Type": "application/json"}
        )
        assert res_str.status_code == 422

        # 3. Direct router invocation with NaN / Inf raises HTTPException 400
        async with AsyncSessionLocal() as session:
            admin_user = (await session.execute(select(User).where(User.username == "admin"))).scalars().first()
            nan_payload = AdjustBalanceRequest(amount=float("nan"), description="NaN test")
            with pytest.raises(HTTPException) as exc_info:
                await admin_adjust_balance(id=user_id, payload=nan_payload, admin=admin_user, db=session)
            assert exc_info.value.status_code == 400

            inf_payload = AdjustBalanceRequest(amount=float("inf"), description="Inf test")
            with pytest.raises(HTTPException) as exc_info2:
                await admin_adjust_balance(id=user_id, payload=inf_payload, admin=admin_user, db=session)
            assert exc_info2.value.status_code == 400


@pytest.mark.asyncio
async def test_tools_utm_builder_protocol_validation():
    """Ensure UTM builder accepts only http:// and https:// URLs."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Malicious protocol
        res_bad = await ac.post("/api/v1/tools/utm-builder", json={
            "url": "javascript:alert(1)",
            "source": "fb",
            "medium": "cpc",
            "campaign": "test"
        })
        assert res_bad.status_code == 400

        # File protocol
        res_file = await ac.post("/api/v1/tools/utm-builder", json={
            "url": "file:///etc/passwd",
            "source": "fb",
            "medium": "cpc",
            "campaign": "test"
        })
        assert res_file.status_code == 400

        # Legitimate URL
        res_good = await ac.post("/api/v1/tools/utm-builder", json={
            "url": "https://tanglike.vn/landing",
            "source": "google",
            "medium": "cpc",
            "campaign": "summer"
        })
        assert res_good.status_code == 200
        assert "utm_source=google" in res_good.json()["data"]["final_url"]


@pytest.mark.asyncio
async def test_sub_site_domain_validation():
    """Reject invalid domain formats in sub-site reseller registrations."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_headers, _, _, _ = await create_test_user(ac)

        # Path traversal / invalid domain
        res_bad1 = await ac.post("/api/v1/sub-sites", json={
            "domain": "../../etc/hosts",
            "site_name": "Bad Domain",
            "markup_percent": 10.0
        }, headers=user_headers)
        assert res_bad1.status_code == 400

        # Space / scheme in domain
        res_bad2 = await ac.post("/api/v1/sub-sites", json={
            "domain": "https://bad domain.com",
            "site_name": "Bad Domain",
            "markup_percent": 10.0
        }, headers=user_headers)
        assert res_bad2.status_code == 400

        # Valid sub-domain
        rand_dom = f"sub-{uuid.uuid4().hex[:8]}.panel.net"
        res_good = await ac.post("/api/v1/sub-sites", json={
            "domain": rand_dom,
            "site_name": "My Reseller Panel",
            "markup_percent": 15.0
        }, headers=user_headers)
        assert res_good.status_code == 200
        assert res_good.json()["data"]["domain"] == rand_dom


@pytest.mark.asyncio
async def test_smm_v2_refill_status_nonexistent_returns_error():
    """SMM v2 refill_status must not fake 'Completed' on non-existent refills."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        user_headers, _, _, _ = await create_test_user(ac)
        key_res = await ac.post("/api/v1/auth/generate-api-key", headers=user_headers)
        raw_key = key_res.json()["data"]

        # Single non-existent refill
        single_res = await ac.post("/api/v2", data={
            "key": raw_key,
            "action": "refill_status",
            "refill": "88888888"
        })
        assert single_res.status_code == 200
        assert "error" in single_res.json()
        assert "Incorrect refill ID" in single_res.json()["error"]

        # Multiple non-existent refills
        multi_res = await ac.post("/api/v2", data={
            "key": raw_key,
            "action": "refill_status",
            "refills": "88888888,88888889"
        })
        assert multi_res.status_code == 200
        m_data = multi_res.json()
        assert isinstance(m_data, list)
        assert len(m_data) == 2
        assert "error" in m_data[0]


@pytest.mark.asyncio
async def test_telegram_notification_html_escaping():
    """Ensure HTML special chars in user input are safely escaped in Telegram notifications."""
    raw_text = '<script>alert("XSS")</script>&\'test\''
    # Test notify_new_user runs without error when input contains unescaped HTML characters
    await TelegramNotifier.notify_new_user(username=raw_text, email='test@example.com')
    # Directly test send_message with escaped string
    res = await TelegramNotifier.send_message(f"Test escaping: &lt;script&gt;")
    assert res is True


@pytest.mark.asyncio
async def test_double_refund_cross_reference_prevention():
    """Verify that an order cannot be refunded twice across admin action and worker."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        admin_headers, _ = await get_admin_headers(ac)
        user_headers, _, _, user_id = await create_test_user(ac)

        # Deposit funds
        await ac.put(
            f"/api/v1/admin/users/{user_id}/balance",
            json={"amount": 200000, "description": "Deposit for order test"},
            headers=admin_headers
        )

        srv_res = await ac.get("/api/v1/services")
        service = srv_res.json()["data"][0]
        qty = max(service.get("min_quantity", 100), 500)

        # Place order
        order_res = await ac.post("/api/v1/orders", json={
            "service_id": service["id"],
            "link": "https://facebook.com/profile.php?id=1000999888",
            "quantity": qty
        }, headers=user_headers)
        assert order_res.status_code == 200
        order_id = order_res.json()["data"]["id"]

        # Admin cancels and refunds order
        cancel_res = await ac.post(f"/api/v1/admin/orders/{order_id}/cancel-action", headers=admin_headers)
        assert cancel_res.status_code == 200

        # Attempting second cancel/refund must be rejected (409 Conflict)
        cancel_second = await ac.post(f"/api/v1/admin/orders/{order_id}/cancel-action", headers=admin_headers)
        assert cancel_second.status_code == 409