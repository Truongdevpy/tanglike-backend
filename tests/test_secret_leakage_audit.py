import inspect
import pytest
from httpx import AsyncClient, ASGITransport
import httpx
from app.main import app
from app.schemas import all as schemas
from app.providers.tuongtaccheo import TuongTacCheoProvider
from app.notifications.telegram import TelegramNotifier
from app.config.settings import settings


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_docker_compose_does_not_expose_database_and_redis_to_all_interfaces():
    with open("docker-compose.yml", "r", encoding="utf-8") as f:
        dc_text = f.read()
    # Check that 5432 and 6379 are bound to 127.0.0.1 or omitted, never 0.0.0.0 or raw "5432:5432"
    assert '"5432:5432"' not in dc_text
    assert '"6379:6379"' not in dc_text
    assert '"127.0.0.1:5432:5432"' in dc_text
    assert '"127.0.0.1:6379:6379"' in dc_text


def test_pydantic_response_models_never_expose_secrets():
    sensitive_substrings = ["password_hash", "api_key_encrypted", "secret_key", "jwt_secret", "token_version"]
    for attr in dir(schemas):
        item = getattr(schemas, attr)
        if isinstance(item, type) and issubclass(item, schemas.BaseModel) and attr.endswith("Response"):
            for f in item.model_fields:
                f_lower = f.lower()
                for sensitive in sensitive_substrings:
                    assert sensitive not in f_lower, f"Model {attr} exposes sensitive field {f}"


@pytest.mark.asyncio
async def test_ttc_login_methods_do_not_leak_credentials_on_failure(caplog, monkeypatch):
    secret_token = "ultra_sensitive_ttc_token_12345"
    secret_pass = "super_secret_ttc_pass_99999"

    import logging

    # 1. Mock network error
    async def mock_network_error(*args, **kwargs):
        raise httpx.ConnectError(f"Connection failure to upstream: {secret_token} and {secret_pass}")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_network_error)

    with caplog.at_level(logging.ERROR):
        res_err1 = await TuongTacCheoProvider.login_by_access_token(secret_token)
        assert res_err1["status"] == "error"
        assert secret_token not in str(res_err1)
        assert secret_token not in caplog.text

    caplog.clear()
    with caplog.at_level(logging.ERROR):
        res_err2 = await TuongTacCheoProvider.login_by_credentials("my_user", secret_pass)
        assert res_err2["status"] == "error"
        assert secret_pass not in str(res_err2)
        assert secret_pass not in caplog.text


@pytest.mark.asyncio
async def test_telegram_notifier_masks_token_in_logs(caplog, monkeypatch):
    import logging
    bot_token = "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ_SECRET"

    async def mock_get_cfg():
        return (bot_token, "987654321")

    monkeypatch.setattr(TelegramNotifier, "get_telegram_config", mock_get_cfg)

    async def mock_post(*args, **kwargs):
        raise httpx.ConnectError(f"Failed to connect to https://api.telegram.org/bot{bot_token}/sendMessage")

    monkeypatch.setattr(httpx.AsyncClient, "post", mock_post)

    with caplog.at_level(logging.WARNING):
        ok = await TelegramNotifier.send_message("Test message")
        assert ok is False
        assert bot_token not in caplog.text
        assert "••••••••••••" in caplog.text


@pytest.mark.asyncio
async def test_admin_sync_mbbank_error_does_not_leak_raw_exception_or_token():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        from app.payments.bank_sync import BankSyncService
        secret_bank_token = "secret_bank_token_abcdef123456"

        async def mock_sync(self):
            raise RuntimeError(f"Connection failed to https://thueapi.pro/historyapimbbankv2/{secret_bank_token}")

        orig_sync = BankSyncService.sync_mbbank
        BankSyncService.sync_mbbank = mock_sync

        try:
            res = await ac.post("/api/v1/admin/banking/sync", headers=headers)
            assert res.status_code == 502
            err_msg = res.json().get("detail", "") or res.json().get("error", {}).get("message", "")
            assert secret_bank_token not in err_msg
            assert "thueapi.pro" not in err_msg
        finally:
            BankSyncService.sync_mbbank = orig_sync


def test_seed_initial_data_code_does_not_log_passwords():
    from app.main import seed_initial_data
    source = inspect.getsource(seed_initial_data)
    assert "admin / admin123456" not in source
    assert "demo / demo123456" not in source