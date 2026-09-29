# -*- coding: utf-8 -*-
"""
Dedicated test suite for PHẦN 6: CHE GIẤU NGUỒN CUNG CẤP & CHỐNG GIAN LẬN
Covers:
- 6.1: White-label / Proxy (mask provider_id, external_service_id, provider_price from public/user catalog)
- 6.2: Access Control (role check at backend: regular user cannot access admin API)
- 6.3: Anti-spam / Rate limiting (login brute force blocks with HTTP 429 and Retry-After)
- 6.4: Anti-privilege escalation (regular user cannot change roles, admin cannot demote self)
- 6.5: Anti-data leakage / IDOR (User B cannot view or cancel User A's orders)
- 6.7: Anti-payment bypass & atomic deduction (client-supplied prices ignored, atomic wallet check)
- 6.8: Anti-price manipulation (strict DB rate calculation, coupon validation)
"""

import pytest
import os
import sys
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select

sys.path.insert(0, os.path.abspath("backend"))

from app.main import app
from app.database.session import AsyncSessionLocal
from app.models.all import User, Service, Order, Coupon, Provider
from app.providers.manager import ProviderManager
from app.providers.base import ProviderInterface


class MockProvider(ProviderInterface):
    async def test_connection(self):
        return {"success": True, "balance": 999999}

    async def get_balance(self):
        return {"balance": 999999, "currency": "VND"}

    async def get_services(self):
        return []

    async def create_order(self, service_id, link, quantity, **kwargs):
        return {"order_id": "MOCK-123456"}

    async def get_order_status(self, order_id):
        return {"status": "processing", "charge": 0, "remains": 0}

    async def refill_order(self, order_id):
        return {"refill_id": "REFILL-999"}

    async def cancel_order(self, order_id):
        return {"status": "canceled"}


@pytest.fixture(autouse=True)
def mock_all_providers(monkeypatch):
    """Ensure provider calls never attempt live HTTP during test runs."""
    mock = MockProvider()
    monkeypatch.setattr(ProviderManager, "get_provider", lambda _: mock)


@pytest.mark.asyncio
async def test_part6_client_cannot_tamper_order_price_or_rate():
    """6.7 & 6.8: Backend MUST ignore client-supplied price/rate and charge strictly from DB."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        assert l_res.status_code == 200
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}", "X-Bypass-Rate-Limit": "true"}

        # Fetch service #1 rate from DB
        async with AsyncSessionLocal() as db:
            s_row = (await db.execute(select(Service).where(Service.id == 1))).scalar_one()
            srv_rate = float(s_row.price)
            # Give demo user enough balance for test
            u_row = (await db.execute(select(User).where(User.username == "demo"))).scalar_one()
            u_row.balance = 500000.0
            await db.commit()

        # Attacker sends payload attempting to override price to 1 VNĐ
        tampered_payload = {
            "service_id": 1,
            "link": "https://facebook.com/testpost",
            "quantity": 1000,
            "price": 1.0,         # Attempted exploit: 1đ instead of real price
            "rate": 0.001,        # Attempted exploit: fake unit rate
            "total_price": 1.0,   # Attempted exploit: fake total
            "discount": 999999.0  # Attempted exploit: fake discount
        }

        res = await ac.post("/api/v1/orders", json=tampered_payload, headers=headers)
        assert res.status_code == 200, res.text
        data = res.json()["data"]

        # Server MUST have computed expected price: (1000 / 1000) * srv_rate = srv_rate
        expected_price = round(srv_rate, 2)
        assert abs(data["price"] - expected_price) < 0.05, f"Expected {expected_price}, got {data['price']}"


@pytest.mark.asyncio
async def test_part6_unauthorized_user_cannot_access_admin_api():
    """6.2: Role check at backend - regular users and guests CANNOT access admin endpoints."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Guest without token
        guest_res = await ac.get("/api/v1/admin/users", headers={"X-Bypass-Rate-Limit": "true"})
        assert guest_res.status_code in [401, 403]

        # 2. Regular user (demo)
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        user_token = l_res.json()["data"]["access_token"]
        user_headers = {"Authorization": f"Bearer {user_token}", "X-Bypass-Rate-Limit": "true"}

        # Attempt to access user management
        u_res = await ac.get("/api/v1/admin/users", headers=user_headers)
        assert u_res.status_code == 403

        # Attempt to access providers configuration
        p_res = await ac.get("/api/v1/admin/providers", headers=user_headers)
        assert p_res.status_code == 403

        # Attempt to manually change wallet balance
        b_res = await ac.put("/api/v1/admin/users/1/balance", json={"amount": 999999, "action": "ADD"}, headers=user_headers)
        assert b_res.status_code == 403


@pytest.mark.asyncio
async def test_part6_admin_cannot_accidentally_demote_themselves():
    """6.4: Prevent admin lockout by rejecting self-demotion from ADMIN role."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}", "X-Bypass-Rate-Limit": "true"}

        async with AsyncSessionLocal() as db:
            admin_user = (await db.execute(select(User).where(User.username == "admin"))).scalar_one()
            admin_id = admin_user.id

        # Try to demote self to USER
        res = await ac.put(f"/api/v1/admin/users/{admin_id}/role?role_val=USER", headers=headers)
        assert res.status_code == 400
        assert "tự hạ quyền" in res.text.lower()


@pytest.mark.asyncio
async def test_part6_idor_user_cannot_view_or_cancel_other_orders():
    """6.5: User B cannot view, cancel, or refill User A's order."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create an order under demo user (User A)
        l_res_a = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token_a = l_res_a.json()["data"]["access_token"]
        headers_a = {"Authorization": f"Bearer {token_a}", "X-Bypass-Rate-Limit": "true"}

        create_res = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "https://facebook.com/user_a_post", "quantity": 100},
            headers=headers_a
        )
        assert create_res.status_code == 200
        order_id = create_res.json()["data"]["id"]

        # Register or login User B
        reg_b = await ac.post(
            "/api/v1/auth/register",
            json={"username": "attacker_user", "password": "Password123@", "confirm_password": "Password123@", "email": "attacker@test.com", "full_name": "Attacker"},
            headers={"X-Bypass-Rate-Limit": "true"}
        )
        if reg_b.status_code == 200:
            token_b = reg_b.json()["data"]["access_token"]
        else:
            l_res_b = await ac.post("/api/v1/auth/login", json={"username": "attacker_user", "password": "Password123@"}, headers={"X-Bypass-Rate-Limit": "true"})
            token_b = l_res_b.json()["data"]["access_token"]
        headers_b = {"Authorization": f"Bearer {token_b}", "X-Bypass-Rate-Limit": "true"}

        # User B tries to view User A's order
        view_res = await ac.get(f"/api/v1/orders/{order_id}", headers=headers_b)
        assert view_res.status_code == 404

        # User B tries to cancel User A's order
        cancel_res = await ac.post(f"/api/v1/orders/{order_id}/cancel", headers=headers_b)
        assert cancel_res.status_code == 404

        # User B tries to refill User A's order
        refill_res = await ac.post(f"/api/v1/orders/{order_id}/refill", headers=headers_b)
        assert refill_res.status_code == 404


@pytest.mark.asyncio
async def test_part6_rate_limiter_blocks_spam_requests():
    """6.3: Rate limiter blocks repetitive requests with HTTP 429 and Retry-After header."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Send rapid requests with rate limit testing flag enabled
        headers = {"X-Test-Rate-Limit": "true"}
        hit_429 = False

        for i in range(25):
            res = await ac.post(
                "/api/v1/auth/login",
                json={"username": "nonexistent_bot", "password": f"wrongpass{i}"},
                headers=headers
            )
            if res.status_code == 429:
                hit_429 = True
                assert "Retry-After" in res.headers
                assert res.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
                break

        assert hit_429, "Expected rate limiter to trigger HTTP 429 on rapid login attempts"


@pytest.mark.asyncio
async def test_part6_white_label_service_catalog_sanitization():
    """6.1: Service catalog masks provider_id, external_service_id, and provider_price from public."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/v1/services", headers={"X-Bypass-Rate-Limit": "true"})
        assert res.status_code == 200
        services = res.json()["data"]
        assert len(services) > 0

        for s in services[:20]:
            # Provider sensitive internals must NOT be disclosed to the public/client
            assert s.get("provider_id") is None, f"provider_id leaked: {s}"
            assert s.get("external_service_id") is None, f"external_service_id leaked: {s}"
            assert s.get("provider_price") is None, f"provider_price leaked: {s}"
            # Name must not contain TTC or THMXH
            assert "TTC" not in s["name"]
            assert "THMXH" not in s["name"]


@pytest.mark.asyncio
async def test_part6_insufficient_balance_prevents_order_creation():
    """6.7: Atomic wallet check rejects order if balance is insufficient without charging."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register a dedicated user with zero balance to avoid touching demo user
        reg = await ac.post(
            "/api/v1/auth/register",
            json={"username": "broke_user", "password": "Password123@", "confirm_password": "Password123@", "email": "broke@test.com", "full_name": "Broke User"},
            headers={"X-Bypass-Rate-Limit": "true"}
        )
        if reg.status_code == 200:
            broke_token = reg.json()["data"]["access_token"]
        else:
            l_broke = await ac.post("/api/v1/auth/login", json={"username": "broke_user", "password": "Password123@"}, headers={"X-Bypass-Rate-Limit": "true"})
            broke_token = l_broke.json()["data"]["access_token"]
        broke_headers = {"Authorization": f"Bearer {broke_token}", "X-Bypass-Rate-Limit": "true"}

        # Place order for 1000 units costing >= 15đ
        res = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "https://facebook.com/insufficient_test", "quantity": 1000},
            headers=broke_headers
        )
        assert res.status_code == 400
        assert "Số dư không đủ" in res.text
