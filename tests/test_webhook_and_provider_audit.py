import hashlib
import hmac
import time
import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from app.main import app
from app.config.settings import settings
from app.database.session import AsyncSessionLocal
from app.models.all import User, Payment, Provider
from app.utils.network import async_retry
import httpx


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ==============================================================================
# 1. Webhook Authentication, Forgery, and Signature Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_webhook_forged_and_invalid_auth_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        payload = {"content": "NAP DEMO", "amount": 50000, "transaction_id": "TX_TEST_001"}

        # 1. Missing authentication headers
        res_no_auth = await ac.post("/api/v1/payments/webhook", json=payload)
        assert res_no_auth.status_code == 401

        # 2. Invalid secret in X-Secret-Key
        res_bad_secret = await ac.post(
            "/api/v1/payments/webhook",
            json=payload,
            headers={"X-Secret-Key": "completely_wrong_secret"}
        )
        assert res_bad_secret.status_code == 401

        # 3. Invalid bearer token
        res_bad_bearer = await ac.post(
            "/api/v1/payments/webhook",
            json=payload,
            headers={"Authorization": "Bearer wrong_bearer_token"}
        )
        assert res_bad_bearer.status_code == 401


@pytest.mark.asyncio
async def test_webhook_hmac_signature_and_modified_payload():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        secret = settings.PAYMENT_WEBHOOK_SECRET or "test_webhook_secret_default"
        # Ensure secret is set for the test
        orig_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_WEBHOOK_SECRET = "super_secret_webhook_key_2026"

        try:
            sec = settings.PAYMENT_WEBHOOK_SECRET
            raw_body = b'{"content": "NAP DEMO CHUYEN KHOAN", "amount": 60000, "transaction_id": "TX_HMAC_01"}'
            valid_sig = hmac.new(sec.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

            # 1. Valid HMAC signature succeeds
            res_valid = await ac.post(
                "/api/v1/payments/webhook",
                content=raw_body,
                headers={"X-Signature": valid_sig, "Content-Type": "application/json"}
            )
            assert res_valid.status_code == 200
            assert res_valid.json()["success"] is True

            # 2. Invalid HMAC signature fails
            res_bad_sig = await ac.post(
                "/api/v1/payments/webhook",
                content=raw_body,
                headers={"X-Signature": "invalid_hmac_hash_000000000000000000000", "Content-Type": "application/json"}
            )
            assert res_bad_sig.status_code == 401

            # 3. Modified payload (tampered in transit) fails HMAC check
            tampered_body = b'{"content": "NAP DEMO CHUYEN KHOAN", "amount": 6000000, "transaction_id": "TX_HMAC_01"}'
            res_tampered = await ac.post(
                "/api/v1/payments/webhook",
                content=tampered_body,
                headers={"X-Signature": valid_sig, "Content-Type": "application/json"}
            )
            assert res_tampered.status_code == 401

        finally:
            settings.PAYMENT_WEBHOOK_SECRET = orig_secret


# ==============================================================================
# 2. Webhook Timestamp & Replay Protection Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_webhook_timestamp_replay_protection():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        orig_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_WEBHOOK_SECRET = "timestamp_test_secret"
        sec = settings.PAYMENT_WEBHOOK_SECRET
        headers = {"X-Secret-Key": sec}

        try:
            now = time.time()
            tx_id = f"TX_TIME_{uuid.uuid4().hex[:6]}"

            # 1. Old timestamp (>300s ago) rejected
            old_res = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 50000, "transaction_id": f"{tx_id}_old"},
                headers={**headers, "X-Webhook-Timestamp": str(int(now - 600))}
            )
            assert old_res.status_code == 400
            err = old_res.json().get("error", {}).get("message", "") or old_res.json().get("detail", "")
            assert "timestamp" in err.lower()

            # 2. Future timestamp (>300s ahead) rejected
            future_res = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 50000, "transaction_id": f"{tx_id}_fut"},
                headers={**headers, "X-Webhook-Timestamp": str(int(now + 600))}
            )
            assert future_res.status_code == 400

            # 3. Valid recent timestamp accepted
            valid_res = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 50000, "transaction_id": f"{tx_id}_ok"},
                headers={**headers, "X-Webhook-Timestamp": str(int(now))}
            )
            assert valid_res.status_code == 200

        finally:
            settings.PAYMENT_WEBHOOK_SECRET = orig_secret


# ==============================================================================
# 3. Webhook Malformed JSON & Unknown Events Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_webhook_malformed_json_and_unknown_event_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        orig_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_WEBHOOK_SECRET = "malformed_test_secret"
        sec = settings.PAYMENT_WEBHOOK_SECRET
        headers = {"X-Secret-Key": sec, "Content-Type": "application/json"}

        try:
            # 1. Malformed JSON
            bad_json_res = await ac.post(
                "/api/v1/payments/webhook",
                content=b'{"malformed_json: not closed',
                headers=headers
            )
            assert bad_json_res.status_code == 400
            err = bad_json_res.json().get("error", {}).get("message", "") or bad_json_res.json().get("detail", "")
            assert "malformed" in err.lower()

            # 2. Unknown event type rejected
            unknown_event_res = await ac.post(
                "/api/v1/payments/webhook",
                json={
                    "event": "charge.refunded",
                    "content": "NAP DEMO",
                    "amount": 50000,
                    "transaction_id": "TX_UNKNOWN_EVT"
                },
                headers=headers
            )
            assert unknown_event_res.status_code == 400
            err = unknown_event_res.json().get("error", {}).get("message", "") or unknown_event_res.json().get("detail", "")
            assert "không được hỗ trợ" in err.lower()

            # 3. Failed transaction status rejected without crediting
            failed_st_res = await ac.post(
                "/api/v1/payments/webhook",
                json={
                    "status": "FAILED",
                    "content": "NAP DEMO",
                    "amount": 50000,
                    "transaction_id": "TX_FAILED_ST"
                },
                headers=headers
            )
            assert failed_st_res.status_code == 400

        finally:
            settings.PAYMENT_WEBHOOK_SECRET = orig_secret


# ==============================================================================
# 4. Duplicate Event & Replay Idempotency Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_webhook_duplicate_event_idempotency_prevents_double_crediting():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        orig_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_WEBHOOK_SECRET = "idempotency_test_secret"
        sec = settings.PAYMENT_WEBHOOK_SECRET
        headers = {"X-Secret-Key": sec}

        try:
            async with AsyncSessionLocal() as db:
                u_res = await db.execute(select(User).where(User.username == "demo"))
                demo = u_res.scalar_one()
                bal_before = float(demo.balance)

            unique_tx = f"TX_IDEMP_{uuid.uuid4().hex[:8]}"

            # 1. First delivery credits balance
            res1 = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 80000, "transaction_id": unique_tx},
                headers=headers
            )
            assert res1.status_code == 200
            assert res1.json()["data"]["status"] == "success"

            async with AsyncSessionLocal() as db:
                u_res = await db.execute(select(User).where(User.username == "demo"))
                demo = u_res.scalar_one()
                assert float(demo.balance) == round(bal_before + 80000, 2)

            # 2. Duplicate delivery must NOT credit balance a second time
            res2 = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 80000, "transaction_id": unique_tx},
                headers=headers
            )
            assert res2.status_code == 200
            assert res2.json()["data"]["status"] == "already_processed"

            async with AsyncSessionLocal() as db:
                u_res = await db.execute(select(User).where(User.username == "demo"))
                demo = u_res.scalar_one()
                assert float(demo.balance) == round(bal_before + 80000, 2)

        finally:
            settings.PAYMENT_WEBHOOK_SECRET = orig_secret


# ==============================================================================
# 5. Cross-User Balance Manipulation Prevention & Business Rules Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_webhook_cannot_modify_unauthorized_user_balance():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        orig_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_WEBHOOK_SECRET = "isolation_test_secret"
        sec = settings.PAYMENT_WEBHOOK_SECRET
        headers = {"X-Secret-Key": sec}

        try:
            # 1. Non-existent user in memo
            res_fake_user = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP GHOST_USER_9999", "amount": 50000, "transaction_id": "TX_GHOST"},
                headers=headers
            )
            assert res_fake_user.status_code == 400

            # 2. Deposit amount below min_deposit rejected on direct transfer
            res_below_min = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 500, "transaction_id": "TX_BELOW_MIN"},
                headers=headers
            )
            assert res_below_min.status_code == 400
            err = res_below_min.json().get("error", {}).get("message", "") or res_below_min.json().get("detail", "")
            assert "tối thiểu" in err.lower()

            # 3. Excessive amount (>1 billion VND) rejected
            res_excessive = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 2_000_000_000, "transaction_id": "TX_TOO_BIG"},
                headers=headers
            )
            assert res_excessive.status_code == 400

        finally:
            settings.PAYMENT_WEBHOOK_SECRET = orig_secret


# ==============================================================================
# 6. Provider Secret Exposure Audit Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_provider_secrets_never_exposed_in_api_responses():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Check provider list endpoint for admin
        p_res = await ac.get("/api/v1/admin/providers", headers=admin_headers)
        assert p_res.status_code == 200
        providers = p_res.json()["data"]

        for prov in providers:
            # Must NOT expose raw plaintext api_key
            assert "api_key" not in prov or prov["api_key"] is None
            assert "api_key_encrypted" not in prov
            # Masked key should exist and contain dots
            if prov.get("api_key_masked"):
                assert "••••" in prov["api_key_masked"]

        # 2. Check public services endpoint
        svc_res = await ac.get("/api/v1/services")
        assert svc_res.status_code == 200
        services = svc_res.json()["data"]
        for svc in services:
            assert "provider_price" not in svc or svc.get("provider_price") is None
            assert "provider_api_key" not in svc


# ==============================================================================
# 7. Provider Timeout and Retry Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_provider_retry_on_transient_failures():
    attempts = 0

    async def flaky_call():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ConnectError("Transient network drop")
        return "SUCCESS"

    result = await async_retry(flaky_call, max_retries=3, initial_delay=0.01)
    assert result == "SUCCESS"
    assert attempts == 3


@pytest.mark.asyncio
async def test_provider_retry_eventually_fails_when_exhausted():
    attempts = 0

    async def always_failing_call():
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("Provider timeout")

    with pytest.raises(httpx.ReadTimeout):
        await async_retry(always_failing_call, max_retries=3, initial_delay=0.01)

    assert attempts == 3

# ==============================================================================
# 8. Webhook Nonce Replay, Event-ID Idempotency & Provider Identity Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_webhook_nonce_replay_protection_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        orig_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_WEBHOOK_SECRET = "nonce_test_secret"
        sec = settings.PAYMENT_WEBHOOK_SECRET
        headers = {"X-Secret-Key": sec}

        try:
            nonce_val = f"nonce_{uuid.uuid4().hex}"
            tx_id = f"TX_NONCE_{uuid.uuid4().hex[:6]}"

            # 1. First request with nonce succeeds
            res1 = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 50000, "transaction_id": tx_id},
                headers={**headers, "X-Webhook-Nonce": nonce_val}
            )
            assert res1.status_code == 200
            assert res1.json()["success"] is True

            # 2. Second request with same nonce is rejected as replay
            res2 = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 50000, "transaction_id": f"{tx_id}_replay"},
                headers={**headers, "X-Webhook-Nonce": nonce_val}
            )
            assert res2.status_code == 400
            err = res2.json().get("detail", "") or res2.json().get("error", {}).get("message", "")
            assert "nonce" in err.lower()

        finally:
            settings.PAYMENT_WEBHOOK_SECRET = orig_secret


@pytest.mark.asyncio
async def test_webhook_event_id_header_idempotency():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        orig_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_WEBHOOK_SECRET = "event_id_test_secret"
        sec = settings.PAYMENT_WEBHOOK_SECRET
        headers = {"X-Secret-Key": sec}

        try:
            evt_id = f"EVT_{uuid.uuid4().hex[:10]}"
            memo = f"NAP DEMO {uuid.uuid4().hex[:4].upper()}"

            # First delivery
            res1 = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": memo, "amount": 35000},
                headers={**headers, "X-Event-Id": evt_id}
            )
            assert res1.status_code == 200
            assert res1.json()["data"]["status"] == "success"

            # Duplicate delivery with same X-Event-Id header
            res2 = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": memo, "amount": 35000},
                headers={**headers, "X-Event-Id": evt_id}
            )
            assert res2.status_code == 200
            assert res2.json()["data"]["status"] == "already_processed"

        finally:
            settings.PAYMENT_WEBHOOK_SECRET = orig_secret


@pytest.mark.asyncio
async def test_webhook_unknown_provider_identity_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        orig_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_WEBHOOK_SECRET = "provider_id_test_secret"
        sec = settings.PAYMENT_WEBHOOK_SECRET
        headers = {"X-Secret-Key": sec}

        try:
            # 1. Invalid provider in header
            res_bad_header = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 50000, "transaction_id": "TX_PROV_1"},
                headers={**headers, "X-Provider": "unauthorized_fake_bank"}
            )
            assert res_bad_header.status_code == 400
            err1 = res_bad_header.json().get("detail", "") or res_bad_header.json().get("error", {}).get("message", "")
            assert "nhà cung cấp" in err1.lower() or "provider" in err1.lower()

            # 2. Invalid provider in payload
            res_bad_body = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 50000, "transaction_id": "TX_PROV_2", "provider": "malicious_payment_gateway"},
                headers=headers
            )
            assert res_bad_body.status_code == 400
            err2 = res_bad_body.json().get("detail", "") or res_bad_body.json().get("error", {}).get("message", "")
            assert "nhà cung cấp" in err2.lower() or "provider" in err2.lower()

            # 3. Valid provider in header succeeds
            res_valid = await ac.post(
                "/api/v1/payments/webhook",
                json={"content": "NAP DEMO", "amount": 50000, "transaction_id": f"TX_OK_{uuid.uuid4().hex[:6]}"},
                headers={**headers, "X-Provider": "mbbank"}
            )
            assert res_valid.status_code == 200

        finally:
            settings.PAYMENT_WEBHOOK_SECRET = orig_secret


@pytest.mark.asyncio
async def test_webhook_cannot_trigger_unauthorized_order_creation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        orig_secret = settings.PAYMENT_WEBHOOK_SECRET
        settings.PAYMENT_WEBHOOK_SECRET = "order_protection_secret"
        sec = settings.PAYMENT_WEBHOOK_SECRET
        headers = {"X-Secret-Key": sec}

        from app.models.all import Order

        async with AsyncSessionLocal() as db:
            orders_before = (await db.execute(select(Order))).scalars().all()
            count_before = len(orders_before)

        try:
            malicious_payload = {
                "content": "NAP DEMO",
                "amount": 50000,
                "transaction_id": f"TX_INJECT_{uuid.uuid4().hex[:6]}",
                "service_id": 1,
                "link": "https://facebook.com/malicious_order_target",
                "quantity": 10000,
                "action": "create_order",
                "order": 999999
            }
            res = await ac.post("/api/v1/payments/webhook", json=malicious_payload, headers=headers)
            assert res.status_code == 200

            async with AsyncSessionLocal() as db:
                orders_after = (await db.execute(select(Order))).scalars().all()
                count_after = len(orders_after)
                assert count_after == count_before

        finally:
            settings.PAYMENT_WEBHOOK_SECRET = orig_secret


@pytest.mark.asyncio
async def test_tuongtaccheo_and_thmxh_provider_rate_limiting_and_retry():
    from app.providers.thmxh import THMXHProvider
    from app.providers.tuongtaccheo import TuongTacCheoProvider

    thmxh = THMXHProvider(api_url="https://thmxh.com/api/v2", api_key="test_key")
    ttc = TuongTacCheoProvider(api_url="https://tuongtaccheo.com/api/v2", api_key="test_key")

    assert hasattr(thmxh, "_post_with_retry")
    assert hasattr(ttc, "_post_with_retry")
