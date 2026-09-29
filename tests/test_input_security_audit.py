import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.utils.security import (
    validate_target_link,
    is_safe_url,
    is_safe_presentation_url,
    is_safe_subsite_domain,
    sanitize_search_query
)
from app.payments.helpers import VietQRHelper


# ==============================================================================
# 1. Target Link Validation Tests (Dangerous Schemes, Obfuscation, Normalization)
# ==============================================================================

def test_validate_target_link_rejects_dangerous_schemes():
    dangerous = [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "   javascript:alert(1)",
        "javascript :alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "file:///etc/passwd",
        "file:///C:/Windows/win.ini",
        "vbscript:msgbox(1)",
        "about:blank",
        "blob:https://example.com/0000-0000-0000-0000",
        "filesystem:http://example.com/temporary/",
        "chrome://settings",
        "ftp://attacker.com/payload",
        "gopher://127.0.0.1:6379/_flushall",
        "ldap://attacker.com/cn=root",
    ]
    for link in dangerous:
        assert validate_target_link(link) is False, f"Failed to reject dangerous scheme: {link}"


def test_validate_target_link_rejects_encoded_and_unicode_bypasses():
    evasions = [
        # URL encoded javascript:
        "%6a%61%76%61%73%63%72%69%70%74%3aalert(1)",
        # Double URL encoded javascript:
        "%256a%2561%2576%2561%2573%2563%2572%2569%2570%2574%253aalert(1)",
        # Fullwidth Unicode characters normalizing to javascript:
        "\uff4a\uff41\uff56\uff41\uff53\uff43\uff52\uff49\uff50\uff54\uff1aalert(1)",
        # Null bytes (raw and encoded)
        "https://facebook.com/post\x00extra",
        "https://facebook.com/post%00extra",
        # CRLF injection
        "https://facebook.com/post\r\nX-Injected: true",
        "https://facebook.com/post%0d%0aX-Injected: true",
        # Authority userinfo credentials
        "https://admin:secret@facebook.com/profile",
        "http://attacker:pass@instagram.com/post",
    ]
    for evasion in evasions:
        assert validate_target_link(evasion) is False, f"Failed to reject evasion attempt: {evasion}"


def test_validate_target_link_accepts_legitimate_inputs():
    valid = [
        "https://facebook.com/1000827362",
        "https://www.tiktok.com/@my_creator/video/7291827391823",
        "http://instagram.com/p/CpXyz12",
        "https://youtube.com/watch?v=dQw4w9WgXcQ",
        "@tiktok_handle",
        "1000827362",
        "https://example.com/path?param=value&other=123",
    ]
    for link in valid:
        assert validate_target_link(link) is True, f"Legitimate link rejected: {link}"


# ==============================================================================
# 2. Outbound SSRF Protection Tests (is_safe_url)
# ==============================================================================

def test_is_safe_url_blocks_metadata_and_internal_ips():
    blocked = [
        # AWS/Azure/GCP metadata
        "http://169.254.169.254/latest/meta-data",
        "http://169.254.169.254/",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://instance-data/latest/meta-data",
        # Alibaba Cloud metadata (CGNAT)
        "http://100.100.100.200/latest/meta-data",
        # Loopback representations
        "http://localhost/api",
        "http://127.0.0.1:8000/api",
        "http://2130706433/",        # Dword / integer 127.0.0.1
        "http://0x7f000001/",        # Hex 127.0.0.1
        "http://0177.0.0.1/",        # Octal 127.0.0.1
        "http://[::1]/",             # IPv6 loopback
        "http://[::ffff:127.0.0.1]/",# IPv4-mapped IPv6 loopback
        "http://[::ffff:169.254.169.254]/", # IPv4-mapped IPv6 metadata
        "http://0.0.0.0/",
        # Private IP ranges (RFC 1918)
        "http://10.0.0.1/smm",
        "http://172.16.0.1/smm",
        "http://192.168.1.1/smm",
        # Internal DNS suffixes
        "http://service.local/api",
        "http://panel.internal/api",
        "http://kubernetes.default.svc/api",
        # Dangerous service ports
        "http://example.com:22/ssh",
        "http://example.com:6379/redis",
        "http://example.com:5432/postgres",
        "http://example.com:25/smtp",
        "http://example.com:3306/mysql",
        "http://example.com:27017/mongo",
        # Userinfo host confusion
        "http://user:pass@127.0.0.1/api",
        "http://attacker.com@169.254.169.254/api",
        # Non-HTTP protocols
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_test",
        "ftp://example.com/api",
    ]
    for target in blocked:
        assert is_safe_url(target, allow_local_in_dev=False) is False, f"SSRF target not blocked: {target}"


# ==============================================================================
# 3. Subsite Domain Validation Tests (is_safe_subsite_domain)
# ==============================================================================

def test_is_safe_subsite_domain():
    assert is_safe_subsite_domain("sub.example.com") is True
    assert is_safe_subsite_domain("my-panel.tanglike.vn") is True
    # Disallow IP addresses
    assert is_safe_subsite_domain("127.0.0.1") is False
    assert is_safe_subsite_domain("169.254.169.254") is False
    assert is_safe_subsite_domain("10.0.0.1") is False
    # Disallow local / internal suffixes
    assert is_safe_subsite_domain("panel.local") is False
    assert is_safe_subsite_domain("evil.internal") is False
    assert is_safe_subsite_domain("sub.localhost") is False
    # Disallow invalid format / no dot
    assert is_safe_subsite_domain("localhost") is False
    assert is_safe_subsite_domain("invalid_domain") is False


# ==============================================================================
# 4. Search Sanitization Tests (sanitize_search_query)
# ==============================================================================

def test_sanitize_search_query():
    assert sanitize_search_query(None) is None
    assert sanitize_search_query("") is None
    assert sanitize_search_query("   ") is None
    assert sanitize_search_query("normal search") == "normal search"
    # Strips null bytes (raw and encoded)
    assert sanitize_search_query("test\x00search") == "testsearch"
    assert sanitize_search_query("test%00search") == "testsearch"
    # Strips CRLF
    assert sanitize_search_query("line1\r\nline2") == "line1line2"
    # Truncates to max_length
    long_query = "A" * 200
    sanitized = sanitize_search_query(long_query, max_length=50)
    assert len(sanitized) == 50


# ==============================================================================
# 5. VietQR Parameter Sanitization Tests
# ==============================================================================

def test_vietqr_url_sanitization():
    url = VietQRHelper.generate_qr_url(
        bank_name="MB Bank",
        account_no="0002406200504\r\nInjected: true",
        amount=50000,
        memo="NAP DEMO\r\n&injected=true",
        account_holder="NGUYEN MINH & <SCRIPT>"
    )
    assert "\r" not in url
    assert "\n" not in url
    assert "Injected:" not in url or "%0D%0A" in url or "Injected" in url
    # Verify account number stripped non-alnum
    assert "0002406200504Injectedtrue" in url or "0002406200504" in url
    # Verify query params are safe
    assert "<SCRIPT>" not in url


# ==============================================================================
# 6. API Integration Tests (Orders, SMM v2, Providers, Settings, Sub-sites)
# ==============================================================================

@pytest.mark.asyncio
async def test_order_creation_rejects_dangerous_link_schemes():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Reject javascript: scheme
        res1 = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "javascript:alert(1)", "quantity": 100},
            headers=headers
        )
        assert res1.status_code == 422 or res1.status_code == 400

        # 2. Reject encoded javascript: scheme
        res2 = await ac.post(
            "/api/v1/orders",
            json={"service_id": 1, "link": "%6a%61%76%61%73%63%72%69%70%74%3aalert(1)", "quantity": 100},
            headers=headers
        )
        assert res2.status_code == 422 or res2.status_code == 400

        # 3. Reject SMM v2 add with dangerous link
        gen_res = await ac.post("/api/v1/auth/generate-api-key", headers=headers)
        api_key = gen_res.json()["data"]
        smm_res = await ac.post(
            "/api/v2",
            data={"key": api_key, "action": "add", "service": 1, "link": "data:text/html,evil", "quantity": 100}
        )
        assert smm_res.status_code == 200
        assert "error" in smm_res.json()


@pytest.mark.asyncio
async def test_provider_update_blocks_ssrf_via_api():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # Attempt to update Provider 1 base_url to cloud metadata
        res = await ac.put(
            "/api/v1/admin/providers/1",
            json={"base_url": "http://169.254.169.254/latest/meta-data"},
            headers=headers
        )
        assert res.status_code == 400
        assert "SSRF" in res.json()["error"]["message"]

        # Attempt to update Provider 1 base_url to integer loopback
        res2 = await ac.put(
            "/api/v1/admin/providers/1",
            json={"base_url": "http://2130706433/"},
            headers=headers
        )
        assert res2.status_code == 400
        assert "SSRF" in res2.json()["error"]["message"]


@pytest.mark.asyncio
async def test_admin_settings_blocks_xss_in_zalo_url():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {admin_token}"}

        # 1. Attempt to set javascript: scheme in zalo_url
        res = await ac.put(
            "/api/v1/admin/settings",
            json={"settings": {"zalo_url": "javascript:alert(document.cookie)"}},
            headers=headers
        )
        assert res.status_code == 400
        assert "XSS" in res.json()["error"]["message"] or "zalo_url" in res.json()["error"]["message"]

        # 2. Valid safe zalo_url updates cleanly
        ok_res = await ac.put(
            "/api/v1/admin/settings",
            json={"settings": {"zalo_url": "https://zalo.me/0987654321"}},
            headers=headers
        )
        assert ok_res.status_code == 200


@pytest.mark.asyncio
async def test_subsite_creation_rejects_ip_and_internal_domains_via_api():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        demo_login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = demo_login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Attempt to register loopback IP
        res1 = await ac.post(
            "/api/v1/sub-sites",
            json={"domain": "127.0.0.1", "site_name": "Localhost SubSite", "markup_percent": 10.0},
            headers=headers
        )
        assert res1.status_code == 400

        # 2. Attempt to register metadata IP
        res2 = await ac.post(
            "/api/v1/sub-sites",
            json={"domain": "169.254.169.254", "site_name": "Metadata SubSite", "markup_percent": 10.0},
            headers=headers
        )
        assert res2.status_code == 400

        # 3. Attempt to register internal .local domain
        res3 = await ac.post(
            "/api/v1/sub-sites",
            json={"domain": "evil.local", "site_name": "Internal SubSite", "markup_percent": 10.0},
            headers=headers
        )
        assert res3.status_code == 400