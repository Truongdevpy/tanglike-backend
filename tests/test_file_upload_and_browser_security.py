import os
import io
import zipfile
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.config.settings import settings
from app.utils.upload import (
    validate_uploaded_image,
    sanitize_filename,
    get_safe_destination_path,
    validate_zip_archive,
    MAX_FILE_SIZE_BYTES,
)


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ==============================================================================
# 1. Filename Sanitization & Path Traversal Tests
# ==============================================================================

def test_sanitize_filename_rejects_path_traversal():
    traversal_filenames = [
        "../../etc/passwd",
        "..\\..\\windows\\system32\\cmd.exe",
        "avatar/../../../secret.txt",
        "..\\secret.jpg",
        "image/../avatar.png",
    ]
    for fn in traversal_filenames:
        with pytest.raises(ValueError, match="path traversal"):
            sanitize_filename(fn)


def test_sanitize_filename_rejects_null_bytes():
    null_byte_filenames = [
        "avatar.png\x00.php",
        "safe_image%00.exe",
        "test\x00pic.jpg",
    ]
    for fn in null_byte_filenames:
        with pytest.raises(ValueError, match="null byte"):
            sanitize_filename(fn)


def test_sanitize_filename_accepts_clean_names():
    assert sanitize_filename("my_avatar.png") == "my_avatar.png"
    assert sanitize_filename("user-photo-2026.jpg") == "user-photo-2026.jpg"


# ==============================================================================
# 2. File Upload Extension & MIME Security Tests
# ==============================================================================

def test_validate_uploaded_image_rejects_executable_extensions():
    dangerous = [
        ("shell.php", "image/png"),
        ("backdoor.phtml", "image/jpeg"),
        ("exploit.exe", "image/png"),
        ("script.sh", "image/png"),
        ("app.py", "image/png"),
        ("malicious.svg", "image/svg+xml"),
        ("index.html", "text/html"),
    ]
    dummy_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    for name, mime in dangerous:
        with pytest.raises(ValueError, match="bị từ chối|không được hỗ trợ"):
            validate_uploaded_image(name, mime, dummy_bytes)


def test_validate_uploaded_image_rejects_disallowed_mime():
    valid_png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    with pytest.raises(ValueError, match="Kiểu nội dung"):
        validate_uploaded_image("avatar.png", "text/html", valid_png_bytes)

    with pytest.raises(ValueError, match="Kiểu nội dung"):
        validate_uploaded_image("avatar.png", "application/x-executable", valid_png_bytes)


# ==============================================================================
# 3. Magic Bytes (File Signature) Verification Tests
# ==============================================================================

def test_validate_uploaded_image_verifies_magic_signatures():
    # 1. Spoofed PNG (PHP script disguised as PNG)
    fake_png = b"<?php echo 'malicious'; ?>"
    with pytest.raises(ValueError, match="Chữ ký tệp"):
        validate_uploaded_image("fake.png", "image/png", fake_png)

    # 2. Spoofed JPEG
    fake_jpg = b"GIF89aNotAJpeg"
    with pytest.raises(ValueError, match="Chữ ký tệp"):
        validate_uploaded_image("fake.jpg", "image/jpeg", fake_jpg)

    # 3. Legitimate PNG succeeds
    valid_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
    safe_name = validate_uploaded_image("valid.png", "image/png", valid_png)
    assert safe_name.endswith(".png")

    # 4. Legitimate JPEG succeeds
    valid_jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 50
    safe_name_jpg = validate_uploaded_image("valid.jpeg", "image/jpeg", valid_jpeg)
    assert safe_name_jpg.endswith(".jpeg")


# ==============================================================================
# 4. Polyglot File & Malicious Script Rejection Tests
# ==============================================================================

def test_validate_uploaded_image_rejects_polyglots():
    # Valid GIF header followed by embedded PHP code
    polyglot_php = b"GIF89a" + b"\x00" * 10 + b"<?php system($_GET['cmd']); ?>"
    with pytest.raises(ValueError, match="payload polyglot"):
        validate_uploaded_image("polyglot.gif", "image/gif", polyglot_php)

    # Valid PNG header followed by embedded JavaScript <script>
    polyglot_js = b"\x89PNG\r\n\x1a\n" + b"\x00" * 10 + b"<script>alert(1)</script>"
    with pytest.raises(ValueError, match="payload polyglot"):
        validate_uploaded_image("polyglot.png", "image/png", polyglot_js)


# ==============================================================================
# 5. File Size & Storage Path Containment Tests
# ==============================================================================

def test_validate_uploaded_image_rejects_oversized_files():
    oversized = b"\x89PNG\r\n\x1a\n" + (b"A" * (MAX_FILE_SIZE_BYTES + 10))
    with pytest.raises(ValueError, match="vượt quá giới hạn"):
        validate_uploaded_image("large.png", "image/png", oversized)


def test_get_safe_destination_path_blocks_escape():
    base_dir = os.path.abspath("static/uploads")
    # Valid filename stays inside base_dir
    safe_path = get_safe_destination_path(base_dir, "clean_uuid_123.png")
    assert safe_path.startswith(base_dir)

    # Escape attempt is blocked
    with pytest.raises(ValueError, match="path traversal"):
        get_safe_destination_path(base_dir, "../../../evil.png")


# ==============================================================================
# 6. ZIP Bomb Defense Tests
# ==============================================================================

def test_validate_zip_archive_detects_traversal_and_bombs():
    # 1. Traversal in zip entry
    bio_trav = io.BytesIO()
    with zipfile.ZipFile(bio_trav, "w") as zf:
        zf.writestr("../../etc/cron.d/evil", b"echo 1")
    with pytest.raises(ValueError, match="path traversal"):
        validate_zip_archive(bio_trav.getvalue())

    # 2. High ratio ZIP bomb simulation
    bio_bomb = io.BytesIO()
    with zipfile.ZipFile(bio_bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zero.bin", b"\x00" * (5 * 1024 * 1024))
    with pytest.raises(ValueError, match="Tỷ lệ nén|ZIP bomb"):
        validate_zip_archive(bio_bomb.getvalue(), max_ratio=5.0)


# ==============================================================================
# 7. XSS Mitigation in Profiles & Avatar Endpoints
# ==============================================================================

@pytest.mark.asyncio
async def test_profile_update_rejects_xss_avatar_schemes():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        dangerous_avatars = [
            "javascript:alert(document.cookie)",
            "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
            "vbscript:msgbox(1)",
            "file:///etc/passwd",
        ]
        for bad_av in dangerous_avatars:
            res = await ac.put("/api/v1/auth/profile", json={"avatar": bad_av}, headers=headers)
            assert res.status_code == 400
            err = res.json().get("detail", "") or res.json().get("error", {}).get("message", "")
            assert "ảnh đại diện" in err.lower() or "giao thức" in err.lower()

        # Legitimate avatar URL succeeds
        valid_res = await ac.put("/api/v1/auth/profile", json={"avatar": "https://images.example.com/avatar.png"}, headers=headers)
        assert valid_res.status_code == 200


@pytest.mark.asyncio
async def test_avatar_upload_endpoint_security():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login = await ac.post("/api/v1/auth/login", json={"username": "demo", "password": "demo123456"})
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Upload dangerous PHP file disguised as image
        bad_files = {"file": ("shell.php", b"<?php phpinfo(); ?>", "application/x-php")}
        res_bad = await ac.post("/api/v1/users/avatar", files=bad_files, headers=headers)
        assert res_bad.status_code == 400

        # 2. Upload valid PNG image
        valid_png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 128
        good_files = {"file": ("my_avatar.png", valid_png, "image/png")}
        res_good = await ac.post("/api/v1/users/avatar", files=good_files, headers=headers)
        assert res_good.status_code == 200
        data = res_good.json()["data"]
        assert data["avatar_url"].startswith("/static/uploads/")
        assert data["filename"].endswith(".png")


# ==============================================================================
# 8. Browser Security Headers Tests
# ==============================================================================

@pytest.mark.asyncio
async def test_browser_security_headers_enforced():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/health")
        assert res.status_code == 200
        # X-Content-Type-Options
        assert res.headers.get("X-Content-Type-Options") == "nosniff"
        # X-Frame-Options (Clickjacking defense)
        assert res.headers.get("X-Frame-Options") == "DENY"
        # Referrer-Policy
        assert res.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
        # Content-Security-Policy for API
        csp = res.headers.get("Content-Security-Policy", "")
        assert "default-src 'none'" in csp or "frame-ancestors 'none'" in csp