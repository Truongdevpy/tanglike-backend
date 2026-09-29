import sys
import os
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

sys.path.insert(0, os.path.abspath("backend"))

from app.main import app
from app.config.settings import settings
from app.database.session import AsyncSessionLocal, engine, Base
from app.models.all import User, Service, Category, Order

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

async def create_user_with_api_key(ac: AsyncClient, prefix: str = "smm_tester") -> tuple[str, str, int]:
    uid = uuid.uuid4().hex[:6]
    username = f"{prefix}_{uid}"
    email = f"{username}@example.com"
    pwd = "TestPassword123!"
    reg = await ac.post("/api/v1/auth/register", json={
        "username": username,
        "email": email,
        "password": pwd,
        "confirm_password": pwd,
        "full_name": "SMM Tester"
    })
    assert reg.status_code == 200
    token = reg.json()["data"]["access_token"]
    user_id = reg.json()["data"]["user"]["id"]

    # Generate API key
    key_res = await ac.post(
        "/api/v1/auth/generate-api-key",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert key_res.status_code == 200
    api_key = key_res.json()["data"]
    return api_key, token, user_id

@pytest.mark.asyncio
async def test_smm_missing_api_key_handling():
    """Verify missing API key handling across SMM v2 and User REST API."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. SMM v2 missing key
        res_smm = await ac.post("/api/v2", data={"action": "services"})
        assert res_smm.status_code == 200
        assert res_smm.json() == {"error": "Incorrect request: Missing key"}

        # 2. User REST API missing header
        res_user_api = await ac.get("/api/v1/user-api/services")
        assert res_user_api.status_code == 401

@pytest.mark.asyncio
async def test_smm_invalid_api_key_handling():
    """Verify invalid API key handling across SMM v2 and User REST API."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        fake_key = "tang_invalid_fake_key_that_does_not_exist_00"

        # 1. SMM v2 invalid key
        res_smm = await ac.post("/api/v2", data={"key": fake_key, "action": "balance"})
        assert res_smm.status_code == 200
        assert res_smm.json() == {"error": "API key invalid"}

        # 2. User REST API invalid key
        res_user_api = await ac.get("/api/v1/user-api/balance", headers={"X-API-KEY": fake_key})
        assert res_user_api.status_code == 401

@pytest.mark.asyncio
async def test_smm_malformed_and_oversized_api_key_rejected():
    """Verify that oversized, undersized, and malformed API keys are safely rejected."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Oversized (>128 chars)
        oversized = "tang_" + ("A" * 150)
        res_smm_over = await ac.post("/api/v2", data={"key": oversized, "action": "services"})
        assert res_smm_over.status_code == 200
        assert res_smm_over.json() == {"error": "API key invalid"}

        res_api_over = await ac.get("/api/v1/user-api/services", headers={"X-API-KEY": oversized})
        assert res_api_over.status_code == 401

        # Undersized (<16 chars)
        undersized = "tang_short"
        res_smm_under = await ac.post("/api/v2", data={"key": undersized, "action": "services"})
        assert res_smm_under.status_code == 200
        assert res_smm_under.json() == {"error": "API key invalid"}

        res_api_under = await ac.get("/api/v1/user-api/services", headers={"X-API-KEY": undersized})
        assert res_api_under.status_code == 401

@pytest.mark.asyncio
async def test_smm_api_key_revocation_lifecycle():
    """Verify that revoking and regenerating API keys immediately invalidates prior keys."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        api_key, token, _ = await create_user_with_api_key(ac, "revocation")
        bearer_headers = {"Authorization": f"Bearer {token}"}

        # Verify key works initially
        chk = await ac.post("/api/v2", data={"key": api_key, "action": "balance"})
        assert chk.status_code == 200
        assert "balance" in chk.json()

        chk_api = await ac.get("/api/v1/user-api/balance", headers={"X-API-KEY": api_key})
        assert chk_api.status_code == 200

        # Revoke API key
        revoke_res = await ac.delete("/api/v1/auth/api-key", headers=bearer_headers)
        assert revoke_res.status_code == 200
        assert revoke_res.json()["data"] is True

        # Prior key must now fail
        dead_smm = await ac.post("/api/v2", data={"key": api_key, "action": "balance"})
        assert dead_smm.status_code == 200
        assert dead_smm.json() == {"error": "API key invalid"}

        dead_api = await ac.get("/api/v1/user-api/balance", headers={"X-API-KEY": api_key})
        assert dead_api.status_code == 401

        # Generate a new key and verify it works while old remains invalid
        new_gen = await ac.post("/api/v1/auth/generate-api-key", headers=bearer_headers)
        assert new_gen.status_code == 200
        new_key = new_gen.json()["data"]
        assert new_key != api_key

        works_smm = await ac.post("/api/v2", data={"key": new_key, "action": "balance"})
        assert works_smm.status_code == 200
        assert "balance" in works_smm.json()

        old_still_dead = await ac.post("/api/v2", data={"key": api_key, "action": "balance"})
        assert old_still_dead.json() == {"error": "API key invalid"}

@pytest.mark.asyncio
async def test_smm_api_key_cannot_access_admin_or_jwt_endpoints():
    """Verify that API keys cannot authenticate against admin endpoints or web session routes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Admin login
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        # Generate admin API key
        admin_key_res = await ac.post(
            "/api/v1/auth/generate-api-key",
            headers={"Authorization": f"Bearer {admin_token}"}
        )
        admin_api_key = admin_key_res.json()["data"]

        # Attempt 1: Passing X-API-KEY to admin endpoint
        res1 = await ac.get("/api/v1/admin/users", headers={"X-API-KEY": admin_api_key})
        assert res1.status_code == 401

        # Attempt 2: Passing API key in Authorization Bearer
        res2 = await ac.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {admin_api_key}"})
        assert res2.status_code == 401

        # Attempt 3: Passing API key to web order endpoint
        res3 = await ac.post("/api/v1/orders", headers={"X-API-KEY": admin_api_key}, json={
            "service_id": 1,
            "link": "https://facebook.com/test",
            "quantity": 100
        })
        assert res3.status_code == 401

@pytest.mark.asyncio
async def test_smm_cross_user_isolation_idor_blocked():
    """Verify User A cannot view, refill, or cancel User B's orders via SMM API or REST API."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        key_a, token_a, user_a_id = await create_user_with_api_key(ac, "user_a")
        key_b, token_b, user_b_id = await create_user_with_api_key(ac, "user_b")

        # Give User A balance to place an order
        async with AsyncSessionLocal() as session:
            db_user_a = await session.get(User, user_a_id)
            db_user_a.balance = 500000.0
            await session.commit()

        # Find an active service
        srv_res = await ac.get("/api/v1/user-api/services", headers={"X-API-KEY": key_a})
        assert srv_res.status_code == 200
        active_srv = srv_res.json()[0]
        srv_id = active_srv["service"]
        qty = max(int(active_srv.get("min", 100)), 100)

        # User A places order
        order_res = await ac.post(
            "/api/v1/user-api/order",
            headers={"X-API-KEY": key_a},
            json={"service": srv_id, "link": "https://facebook.com/order-a", "quantity": qty}
        )
        assert order_res.status_code == 200
        order_id = order_res.json()["order"]

        # 1. User B tries to view User A's order via /user-api/order/{id}
        res_view_b = await ac.get(f"/api/v1/user-api/order/{order_id}", headers={"X-API-KEY": key_b})
        assert res_view_b.status_code == 404

        # 2. User B tries to view User A's order via SMM v2 single status
        res_smm_st = await ac.post("/api/v2", data={"key": key_b, "action": "status", "order": order_id})
        assert res_smm_st.status_code == 200
        assert res_smm_st.json() == {"error": "Incorrect order ID"}

        # 3. User B tries to view User A's order via SMM v2 multiple status
        res_smm_multi = await ac.post("/api/v2", data={"key": key_b, "action": "status", "orders": f"{order_id},999999"})
        assert res_smm_multi.status_code == 200
        multi_data = res_smm_multi.json()
        assert multi_data.get(str(order_id)) == {"error": "Incorrect order ID"}

        # 4. User B tries to cancel User A's order via /user-api/cancel
        res_cancel_api = await ac.post(
            "/api/v1/user-api/cancel",
            headers={"X-API-KEY": key_b},
            json={"order_id": order_id}
        )
        assert res_cancel_api.status_code == 404

        # 5. User B tries to cancel User A's order via SMM v2 cancel
        res_cancel_smm = await ac.post("/api/v2", data={"key": key_b, "action": "cancel", "orders": str(order_id)})
        assert res_cancel_smm.status_code == 200
        cancel_data = res_cancel_smm.json()
        assert any(item.get("order") == str(order_id) and "error" in str(item.get("cancel", "")) for item in cancel_data)

@pytest.mark.asyncio
async def test_smm_deleted_and_inactive_services_cannot_be_accessed():
    """Verify deleted or inactive services and categories cannot be listed or ordered through any API route."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        api_key, token, user_id = await create_user_with_api_key(ac, "inactive_srv")

        # Admin login
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['data']['access_token']}"}

        # Create a test category and service
        async with AsyncSessionLocal() as session:
            cat = Category(
                name="Inactive SMM Category",
                slug=f"inactive-cat-{uuid.uuid4().hex[:6]}",
                platform="Facebook",
                status="INACTIVE"
            )
            session.add(cat)
            await session.flush()
            srv = Service(
                category_id=cat.id,
                name="Hidden SMM Service",
                platform="Facebook",
                service_type="Default",
                price=10.0,
                min_quantity=10,
                max_quantity=1000,
                status="ACTIVE",
                is_deleted=False
            )
            session.add(srv)
            await session.commit()
            hidden_srv_id = srv.id
            cat_id = cat.id

        # 1. User REST API /services must not return service under inactive category
        res_user_services = await ac.get("/api/v1/user-api/services", headers={"X-API-KEY": api_key})
        assert res_user_services.status_code == 200
        assert not any(s["service"] == hidden_srv_id for s in res_user_services.json())

        # 2. SMM v2 action=services must not return service under inactive category
        res_smm_services = await ac.post("/api/v2", data={"key": api_key, "action": "services"})
        assert res_smm_services.status_code == 200
        assert not any(s["service"] == hidden_srv_id for s in res_smm_services.json())

        # 3. User REST API /order must reject placing order for service under inactive category
        res_order_api = await ac.post(
            "/api/v1/user-api/order",
            headers={"X-API-KEY": api_key},
            json={"service": hidden_srv_id, "link": "https://facebook.com/test", "quantity": 100}
        )
        assert res_order_api.status_code == 400

        # 4. SMM v2 action=add must reject placing order for service under inactive category
        res_order_smm = await ac.post("/api/v2", data={
            "key": api_key,
            "action": "add",
            "service": hidden_srv_id,
            "link": "https://facebook.com/test",
            "quantity": 100
        })
        assert res_order_smm.status_code == 200
        assert "error" in res_order_smm.json()

@pytest.mark.asyncio
async def test_smm_v2_dripfeed_parameter_validation():
    """Verify that malformed or negative dripfeed parameters are handled cleanly without 500 crashes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        api_key, _, _ = await create_user_with_api_key(ac, "drip_tester")

        # 1. Malformed non-integer runs
        res_bad_runs = await ac.post("/api/v2", data={
            "key": api_key,
            "action": "add",
            "service": 1,
            "link": "https://facebook.com/test",
            "quantity": 100,
            "runs": "invalid_not_number"
        })
        assert res_bad_runs.status_code == 200
        assert "error" in res_bad_runs.json()

        # 2. Negative runs
        res_neg_runs = await ac.post("/api/v2", data={
            "key": api_key,
            "action": "add",
            "service": 1,
            "link": "https://facebook.com/test",
            "quantity": 100,
            "runs": -5,
            "interval": 10
        })
        assert res_neg_runs.status_code == 200
        assert "error" in res_neg_runs.json()

@pytest.mark.asyncio
async def test_smm_api_key_brute_force_throttling():
    """Verify that repeated invalid API key attempts trigger rate limiting."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        test_headers = {"X-Test-Rate-Limit": "true"}

        # Send multiple invalid API key requests to /api/v2
        blocked = False
        for i in range(35):
            fake_key = f"tang_fake_key_attempt_{i}_{uuid.uuid4().hex[:4]}"
            res = await ac.post("/api/v2", headers=test_headers, data={"key": fake_key, "action": "balance"})
            if res.status_code == 429 or "Too many failed attempts" in str(res.json()):
                blocked = True
                break

        assert blocked is True, "Expected brute-force attempts to be throttled"


@pytest.mark.asyncio
async def test_smm_api_key_wrong_scope_enforcement():
    """Verify API keys have strict scope isolation and cannot access session-scoped or admin-scoped routes."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        api_key, _, _ = await create_user_with_api_key(ac, "scope_test")
        key_header = {"X-API-KEY": api_key}

        # 1. Profile / Session scope
        res_me = await ac.get("/api/v1/auth/me", headers=key_header)
        assert res_me.status_code == 401

        # 2. Support scope
        res_sup = await ac.get("/api/v1/support/conversations", headers=key_header)
        assert res_sup.status_code == 401

        # 3. Payments / Deposit scope
        res_pay = await ac.get("/api/v1/payments/history", headers=key_header)
        assert res_pay.status_code == 401

        # 4. Admin scope
        res_adm = await ac.get("/api/v1/admin/services", headers=key_header)
        assert res_adm.status_code == 401


@pytest.mark.asyncio
async def test_smm_no_api_key_leakage_in_error_responses():
    """Verify that error responses never reflect the raw API key back to the client."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        canary_key = "tang_canary_leak_check_9876543210"

        # 1. SMM v2 invalid key error
        res_err1 = await ac.post("/api/v2", data={"key": canary_key, "action": "balance"})
        assert canary_key not in res_err1.text

        # 2. SMM v2 unknown action error
        res_err2 = await ac.post("/api/v2", data={"key": canary_key, "action": "nonexistent_action"})
        assert canary_key not in res_err2.text

        # 3. User REST API 401 error
        res_err3 = await ac.get("/api/v1/user-api/services", headers={"X-API-KEY": canary_key})
        assert canary_key not in res_err3.text


@pytest.mark.asyncio
async def test_smm_no_api_key_leakage_in_logs(caplog):
    """Verify that server logs never capture or print raw API keys."""
    import logging
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        api_key, _, _ = await create_user_with_api_key(ac, "log_audit")
        with caplog.at_level(logging.DEBUG):
            # Exercise SMM v2 and user-api endpoints
            await ac.post("/api/v2", data={"key": api_key, "action": "balance"})
            await ac.post("/api/v2", data={"key": api_key, "action": "services"})
            await ac.get("/api/v1/user-api/services", headers={"X-API-KEY": api_key})

        # Ensure the raw key is never present in any captured log record
        for record in caplog.records:
            assert api_key not in record.getMessage(), f"API key leaked in log: {record.getMessage()}"


@pytest.mark.asyncio
async def test_smm_deactivated_user_api_key_immediately_expired_and_revoked():
    """Verify that GDPR deactivation immediately revokes the user's API key."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        api_key, token, user_id = await create_user_with_api_key(ac, "deact_key")
        bearer_headers = {"Authorization": f"Bearer {token}"}

        # Key works initially
        chk = await ac.get("/api/v1/user-api/balance", headers={"X-API-KEY": api_key})
        assert chk.status_code == 200

        # Deactivate account via GDPR endpoint
        deact_res = await ac.post(
            "/api/v1/users/me/deactivate",
            headers=bearer_headers,
            json={"password": "TestPassword123!", "reason": "Audit GDPR Erasure"}
        )
        assert deact_res.status_code == 200

        # Now API key must be immediately rejected on both interfaces
        res_user_api = await ac.get("/api/v1/user-api/balance", headers={"X-API-KEY": api_key})
        assert res_user_api.status_code == 401

        res_smm = await ac.post("/api/v2", data={"key": api_key, "action": "balance"})
        assert res_smm.status_code == 200
        assert res_smm.json() == {"error": "API key invalid"}
