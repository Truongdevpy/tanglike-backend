import os
import sys
import uuid
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.abspath("backend"))

from app.main import app, seed_initial_data
from app.database.session import engine, Base, AsyncSessionLocal
from app.models.all import User, BankTransaction, SystemSetting
from app.config.settings import settings
from app.payments.bank_sync import BankSyncService
from app.payments.mbbank import BankTransactionPayload

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await seed_initial_data()
    yield

@pytest.mark.asyncio
async def test_rate_limit_bypass_header_strictly_env_gated():
    """Verify that X-Bypass-Rate-Limit only functions when APP_ENV == 'test'."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # In test mode, header works
        res = await ac.get("/health", headers={"X-Bypass-Rate-Limit": "true"})
        assert res.status_code == 200

        # Temporarily mock APP_ENV as production
        with patch.object(settings, "APP_ENV", "production"):
            # Header should NOT bypass rate limiting in production mode
            # Dispatch multiple rapid requests to /api/v1/auth/login to trigger limiter
            hit_429 = False
            for _ in range(25):
                r = await ac.post(
                    "/api/v1/auth/login",
                    json={"username": "nonexistent_brute", "password": "wrongpassword"},
                    headers={"X-Bypass-Rate-Limit": "true", "X-Test-Rate-Limit": "true"}
                )
                if r.status_code == 429:
                    hit_429 = True
                    break
            assert hit_429 is True, "Rate limiter was bypassed in production despite X-Bypass-Rate-Limit!"

@pytest.mark.asyncio
async def test_bank_sync_failed_transaction_preserved_after_rollback():
    """Verify that BankTransaction with status FAILED is safely committed even if webhook processing raises an error."""
    async with AsyncSessionLocal() as db:
        sync_service = BankSyncService(db)

        # Mock incoming transaction that will fail because memo has invalid user
        tx_id = f"TX_FAIL_AUDIT_{uuid.uuid4().hex[:8]}"
        mock_payload = BankTransactionPayload(
            transaction_id=tx_id,
            amount=50000.0,
            content=f"NAP NONEXISTENTUSER {tx_id}",
            occurred_at=None,
            raw={"transactionID": tx_id, "amount": 50000, "description": f"NAP NONEXISTENTUSER {tx_id}"}
        )

        with patch("app.payments.service.PaymentService.get_banking_config", new_callable=AsyncMock, return_value={"enabled": True, "content_prefix": "NAP", "bank_code": "MBBANK", "api_type": "thueapi"}):
            with patch("app.payments.bank_sync.MBBankTransactionSource.fetch_transactions", new_callable=AsyncMock, return_value=[mock_payload]):
                res = await sync_service.sync_mbbank()
                assert res["failed"] == 1

        # Verify transaction is recorded in DB as FAILED and not wiped out by rollback
        from sqlalchemy import select
        chk = await db.execute(select(BankTransaction).where(BankTransaction.bank_transaction_id == tx_id))
        saved = chk.scalar_one_or_none()
        assert saved is not None, "Failed bank transaction was rolled back and lost!"
        assert saved.status == "FAILED"
        assert saved.error_message is not None

@pytest.mark.asyncio
async def test_live_chat_guest_balance_not_spoofed():
    """Verify that unauthenticated live chat payloads cannot spoof VIP balances."""
    with patch("app.notifications.telegram.TelegramNotifier.notify_live_chat", new_callable=AsyncMock) as mock_notify:
        mock_notify.return_value = True
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post("/api/v1/support/live-chat", json={
                "message": "Testing guest balance spoofing",
                "sender_name": "Scammer",
                "email": "scam@test.com",
                "balance": 999999999.0
            })
            assert res.status_code == 200
            # Check what balance was forwarded to TelegramNotifier
            assert mock_notify.called
            call_kwargs = mock_notify.call_args.kwargs
            assert call_kwargs.get("balance") is None, f"Client-supplied balance was trusted: {call_kwargs.get('balance')}"

@pytest.mark.asyncio
async def test_referral_stats_public_url_and_null_safe():
    """Verify referral link formatting uses PUBLIC_APP_URL and handles missing referral code safely."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Register a user
        uid = uuid.uuid4().hex[:6]
        reg = await ac.post("/api/v1/auth/register", json={
            "username": f"refuser_{uid}",
            "email": f"refuser_{uid}@test.com",
            "password": "Password123!",
            "confirm_password": "Password123!",
            "full_name": "Ref Tester"
        })
        token = reg.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Test with custom PUBLIC_APP_URL
        with patch.object(settings, "PUBLIC_APP_URL", "https://tanglike.vn"):
            stats_res = await ac.get("/api/v1/referral/stats", headers=headers)
            assert stats_res.status_code == 200
            data = stats_res.json()["data"]
            assert data["referral_link"].startswith("https://tanglike.vn/register?ref=")

@pytest.mark.asyncio
async def test_api_key_whitespace_handling():
    """Verify that an API key passed with trailing/leading whitespace is correctly authenticated."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        uid = uuid.uuid4().hex[:6]
        reg = await ac.post("/api/v1/auth/register", json={
            "username": f"apikeyuser_{uid}",
            "email": f"apikey_{uid}@test.com",
            "password": "Password123!",
            "confirm_password": "Password123!",
            "full_name": "API Key Tester"
        })
        token = reg.json()["data"]["access_token"]
        k_res = await ac.post("/api/v1/auth/generate-api-key", headers={"Authorization": f"Bearer {token}"})
        raw_key = k_res.json()["data"]

        # Pass with surrounding whitespace in X-API-KEY header
        padded_key = f"  {raw_key}  \t"
        bal_res = await ac.get("/api/v1/user-api/balance", headers={"X-API-KEY": padded_key})
        assert bal_res.status_code == 200
        assert "balance" in bal_res.json()

@pytest.mark.asyncio
async def test_admin_sync_all_providers_logger_on_exception():
    """Verify that admin_sync_all_providers catches provider sync errors and logs via logger without NameError."""
    from app.routers import admin_providers

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        r_admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = r_admin_login.json()["data"]["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # Mock ProviderManager.get_provider to raise an error during sync
        with patch("app.providers.manager.ProviderManager.get_provider", side_effect=RuntimeError("Simulated provider upstream failure")):
            with patch.object(admin_providers.logger, "warning") as mock_logger_warn:
                res = await ac.post("/api/v1/admin/providers/sync-all?alert_threshold_percent=15", headers=admin_headers)
                assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
                assert res.json()["success"] is True
                assert mock_logger_warn.called, "logger.warning was not called when an error occurred in provider sync!"
                assert any("Simulated provider upstream failure" in str(arg) for call_obj in mock_logger_warn.call_args_list for arg in call_obj.args)

@pytest.mark.asyncio
async def test_worker_sync_refill_requests_multiple_items_no_missing_greenlet():
    """Verify that sync_refill_requests processes multiple refills without MissingGreenlet or variable shadowing."""
    from app.workers.order_worker import OrderWorker
    from app.models.all import RefillRequest, Order, Service, Provider

    async with AsyncSessionLocal() as db:
        prov = Provider(name="TestProvRefill", base_url="http://mock-prov/api/v2", provider_type="standard")
        db.add(prov)
        await db.flush()

        srv = Service(name="Refill Srv", category_id=1, provider_id=prov.id, platform="facebook", price=10.0, min_quantity=10, max_quantity=100)
        db.add(srv)
        await db.flush()

        ord1 = Order(user_id=1, service_id=srv.id, link="http://post1", quantity=100, price=1000.0, external_order_id="ext_ord_1", status="COMPLETED")
        ord2 = Order(user_id=1, service_id=srv.id, link="http://post2", quantity=100, price=1000.0, external_order_id="ext_ord_2", status="COMPLETED")
        db.add_all([ord1, ord2])
        await db.flush()

        ref1 = RefillRequest(order_id=ord1.id, user_id=1, status="PENDING")
        ref2 = RefillRequest(order_id=ord2.id, user_id=1, status="PENDING")
        db.add_all([ref1, ref2])
        await db.commit()
        ref1_id = ref1.id
        ref2_id = ref2.id

    worker = OrderWorker()

    mock_adapter = AsyncMock()
    mock_adapter.refill_order = AsyncMock(return_value={})
    mock_adapter.get_refill_status = AsyncMock(return_value={"status": "PROCESSING"})

    with patch("app.providers.manager.ProviderManager.get_provider", return_value=mock_adapter):
        # Must execute without MissingGreenlet or loop abort
        await worker.sync_refill_requests()

    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
        res1 = await db.execute(select(RefillRequest).where(RefillRequest.id == ref1_id))
        r1 = res1.scalar_one_or_none()
        res2 = await db.execute(select(RefillRequest).where(RefillRequest.id == ref2_id))
        r2 = res2.scalar_one_or_none()
        assert r1 is not None and r1.status == "PENDING"
        assert r2 is not None and r2.status == "PENDING"
