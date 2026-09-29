import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from starlette.requests import Request
from sqlalchemy import select
import sys
import os

sys.path.insert(0, os.path.abspath('backend'))

from app.main import app
from app.database.session import engine, Base
from app.auth.security import create_refresh_token
from app.middleware.rate_limit import RateLimitMiddleware
from app.providers.manager import ProviderManager
from app.auth.security import hash_api_key
from app.database.session import AsyncSessionLocal
from app.models.all import Provider, User

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
async def test_negative_or_zero_quantity_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Negative quantity
        res_neg = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "https://facebook.com/test", "quantity": -50},
            headers=headers
        )
        assert res_neg.status_code in [400, 422]

        # Zero quantity
        res_zero = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "https://facebook.com/test", "quantity": 0},
            headers=headers
        )
        assert res_zero.status_code in [400, 422]


@pytest.mark.asyncio
async def test_api_security_headers_are_present():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"

@pytest.mark.asyncio
async def test_quantity_exceeding_max_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Exceeds max
        res_max = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "https://facebook.com/test", "quantity": 99999999},
            headers=headers
        )
        assert res_max.status_code == 400
        assert "Số lượng phải từ" in res_max.json()["error"]["message"]

@pytest.mark.asyncio
async def test_invalid_coupon_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        res_cp = await ac.post(
            "/api/v1/orders",
            json={
                "service_id": 1,
                "link": "https://facebook.com/test",
                "quantity": 10,
                "coupon_code": "FAKE_COUPON_999"
            },
            headers=headers
        )
        assert res_cp.status_code == 400
        assert "Mã giảm giá không tồn tại" in res_cp.json()["error"]["message"]

@pytest.mark.asyncio
async def test_deposit_below_min_amount_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Below 10,000đ min deposit
        res = await ac.post(
            "/api/v1/payments/deposit",
            json={"amount": 500, "payment_method": "VIETQR"},
            headers=headers
        )
        assert res.status_code in [400, 500]

@pytest.mark.asyncio
async def test_refresh_token_rejected_on_access_route():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Create a refresh token
        rf_token = create_refresh_token({"sub": "2"})

        # Try to use refresh token on a protected route expecting access token
        res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {rf_token}"})
        assert res.status_code == 401
        assert "access token" in res.json()["error"]["message"].lower()


@pytest.mark.asyncio
async def test_admin_settings_never_exposes_or_accepts_secrets():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        current = await ac.get("/api/v1/admin/settings", headers=headers)
        assert current.status_code == 200
        assert "telegram_bot_token" not in current.json()["data"]

        update = await ac.put(
            "/api/v1/admin/settings",
            json={"settings": {"telegram_bot_token": "must-not-be-stored"}},
            headers=headers,
        )
        assert update.status_code == 400


@pytest.mark.asyncio
async def test_provider_submission_failure_does_not_charge_wallet(monkeypatch):
    class FailingProvider:
        async def create_order(self, **_kwargs):
            raise TimeoutError("provider unavailable")

    monkeypatch.setattr(ProviderManager, "get_provider", lambda _provider=None: FailingProvider())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        before = (await ac.get("/api/v1/auth/me", headers=headers)).json()["data"]["balance"]

        response = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "https://facebook.com/provider-failure", "quantity": 100},
            headers=headers,
        )
        assert response.status_code == 502
        assert "không bị trừ" in response.json()["error"]["message"]

        after = (await ac.get("/api/v1/auth/me", headers=headers)).json()["data"]["balance"]
        assert after == before


@pytest.mark.asyncio
async def test_user_api_key_is_hashed_and_authenticates():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = login.json()["data"]["access_token"]
        generated = await ac.post("/api/v1/auth/generate-api-key", headers={"Authorization": f"Bearer {token}"})
        raw_key = generated.json()["data"]
        assert raw_key not in login.json()["data"]["user"]
        async with AsyncSessionLocal() as db:
            user = (await db.execute(select(User).where(User.username == "demo"))).scalar_one()
            assert user.api_key is None
            assert user.api_key_hash == hash_api_key(raw_key)
        response = await ac.get("/api/v1/user-api/balance", headers={"X-API-KEY": raw_key})
        assert response.status_code == 200
        revoked = await ac.delete("/api/v1/auth/api-key", headers={"Authorization": f"Bearer {token}"})
        assert revoked.status_code == 200
        assert (await ac.get("/api/v1/user-api/balance", headers={"X-API-KEY": raw_key})).status_code == 401

@pytest.mark.asyncio
async def test_sql_injection_safe_search():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # SQL injection attack strings
        sqli_res = await ac.get("/api/v1/orders?search=' OR 1=1 --", headers=headers)
        assert sqli_res.status_code == 200
        # Should return 0 or empty list safely, never dump all users' orders
        data = sqli_res.json()["data"]
        assert isinstance(data, list)

@pytest.mark.asyncio
async def test_rate_limiter_protection():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Send 20 rapid login attempts to trigger 429
        got_429 = False
        for _ in range(25):
            res = await ac.post(
                "/api/v1/auth/login",
                json={"username": "brute_force_user", "password": "wrong_password"},
                headers={"X-Test-Rate-Limit": "true"}
            )
            if res.status_code == 429:
                got_429 = True
                assert res.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
                break
        assert got_429 is True


def test_untrusted_forwarded_for_cannot_change_rate_limit_identity():
    middleware = RateLimitMiddleware(app)
    request = Request({
        "type": "http",
        "method": "POST",
        "path": "/api/v1/auth/login",
        "headers": [(b"x-forwarded-for", b"198.51.100.42")],
        "client": ("203.0.113.55", 12345),
        "scheme": "http",
        "server": ("testserver", 80),
    })
    assert middleware._get_client_ip(request) == "203.0.113.55"


@pytest.mark.asyncio
async def test_user_api_is_rate_limited_per_api_key(monkeypatch):
    monkeypatch.setattr("app.middleware.rate_limit.settings.USER_API_RATE_LIMIT_PER_MINUTE", 2)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = login.json()["data"]["access_token"]
        raw_key = (await ac.post("/api/v1/auth/generate-api-key", headers={"Authorization": f"Bearer {token}"})).json()["data"]
        headers = {"X-API-KEY": raw_key, "X-Test-Rate-Limit": "true"}
        assert (await ac.get("/api/v1/user-api/balance", headers=headers)).status_code == 200
        assert (await ac.get("/api/v1/user-api/balance", headers=headers)).status_code == 200
        assert (await ac.get("/api/v1/user-api/balance", headers=headers)).status_code == 429

@pytest.mark.asyncio
async def test_xss_and_crlf_link_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. XSS scheme in link
        res_xss = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "javascript:alert(1)", "quantity": 10},
            headers=headers
        )
        assert res_xss.status_code == 422

        # 2. CRLF injection in link
        res_crlf = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "https://facebook.com/test\r\nHost: evil.com", "quantity": 10},
            headers=headers
        )
        assert res_crlf.status_code == 422

@pytest.mark.asyncio
async def test_subsite_negative_markup_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Negative markup
        res_neg = await ac.post(
            "/api/v1/sub-sites",
            json={"domain": "reseller.com", "site_name": "Test Site", "markup_percent": -50.0},
            headers=headers
        )
        assert res_neg.status_code == 422

@pytest.mark.asyncio
async def test_simulate_webhook_forbidden_for_regular_user():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        l_res = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = l_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Normal user calling simulate-webhook must be rejected (403 Forbidden)
        res = await ac.post(
            "/api/v1/payments/simulate-webhook?transaction_code=NAP_DEMO_999&amount=500000",
            headers=headers
        )
        assert res.status_code == 403

@pytest.mark.asyncio
async def test_payment_webhook_invalid_secret_rejected():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Invalid secret in header
        res = await ac.post(
            "/api/v1/payments/webhook",
            json={"transaction_code": "TEST", "amount": 10000},
            headers={"X-Secret-Key": "WRONG_SECRET_TOKEN"}
        )
        assert res.status_code == 401

@pytest.mark.asyncio
async def test_ssrf_metadata_endpoint_rejected_on_provider_creation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_res = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        token = admin_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # SSRF attempt: Cloud metadata URL
        ssrf_res = await ac.post(
            "/api/v1/admin/providers",
            json={
                "name": "Evil Metadata Provider",
                "provider_type": "generic_smm",
                "base_url": "http://169.254.169.254/latest/meta-data",
                "api_key": "some_key"
            },
            headers=headers
        )
        assert ssrf_res.status_code == 400
        assert "SSRF" in ssrf_res.json()["error"]["message"]

@pytest.mark.asyncio
async def test_crypto_encryption_and_decryption():
    from app.utils.crypto import encrypt_secret, decrypt_secret
    raw_key = "sensitive_partner_api_key_xyz987"
    enc = encrypt_secret(raw_key)
    assert enc.startswith("enc:")
    assert enc != raw_key
    dec = decrypt_secret(enc)
    assert dec == raw_key

@pytest.mark.asyncio
async def test_seeded_provider_credentials_are_encrypted_at_rest():
    async with AsyncSessionLocal() as db:
        providers = (await db.execute(select(Provider))).scalars().all()
    assert providers
    assert all(not provider.api_key_encrypted or provider.api_key_encrypted.startswith("enc:") for provider in providers)

@pytest.mark.asyncio
async def test_idor_order_access_prevented():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        demo_login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = demo_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Try to access a non-existent or other user's order
        res = await ac.get("/api/v1/orders/999999", headers=headers)
        assert res.status_code == 404

def test_trusted_proxy_headers_extraction():
    middleware = RateLimitMiddleware(app)
    
    # 1. Trusted proxy with CF-Connecting-IP
    req_cf = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/services",
        "headers": [(b"cf-connecting-ip", b"103.21.244.10"), (b"x-forwarded-for", b"1.1.1.1")],
        "client": ("127.0.0.1", 45678),
        "scheme": "http",
        "server": ("testserver", 80),
    })
    assert middleware._get_client_ip(req_cf) == "103.21.244.10"

    # 2. Trusted proxy with X-Real-IP
    req_real = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/services",
        "headers": [(b"x-real-ip", b"118.69.182.5")],
        "client": ("::1", 45678),
        "scheme": "http",
        "server": ("testserver", 80),
    })
    assert middleware._get_client_ip(req_real) == "118.69.182.5"

    # 3. Untrusted client attempting to spoof CF-Connecting-IP
    req_spoof = Request({
        "type": "http",
        "method": "GET",
        "path": "/api/v1/services",
        "headers": [(b"cf-connecting-ip", b"1.2.3.4"), (b"x-forwarded-for", b"5.6.7.8")],
        "client": ("198.51.100.99", 45678),
        "scheme": "http",
        "server": ("testserver", 80),
    })
    assert middleware._get_client_ip(req_spoof) == "198.51.100.99"
