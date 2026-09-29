import sys

import os

import uuid

from datetime import datetime, timedelta

import jwt

import pytest

from httpx import AsyncClient, ASGITransport



sys.path.insert(0, os.path.abspath("backend"))



from app.main import app

from app.config.settings import settings

from app.database.session import AsyncSessionLocal

from app.models.all import User

from app.auth.security import create_access_token, create_refresh_token, hash_password



@pytest.fixture(scope="session")

def anyio_backend():

    return "asyncio"



async def register_user(ac: AsyncClient, prefix: str = "auth_test") -> tuple[dict, str, str, str, str]:

    uid = uuid.uuid4().hex[:6]

    username = f"{prefix}_{uid}"

    email = f"{username}@example.com"

    password = "TestPassword123!"

    reg = await ac.post("/api/v1/auth/register", json={

        "username": username,

        "email": email,

        "password": password,

        "confirm_password": password,

        "full_name": "Auth Tester"

    })

    assert reg.status_code == 200, f"Register failed: {reg.text}"

    data = reg.json()["data"]

    token = data["access_token"]

    refresh = data["refresh_token"]

    return {"Authorization": f"Bearer {token}"}, token, refresh, username, password



@pytest.mark.asyncio

async def test_auth_alg_none_rejected():

    """Verify that unsigned tokens with alg=none are strictly rejected with 401."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        headers, _, _, _, _ = await register_user(ac, "alg_none")

        # Craft an unencoded/unsigned token with alg=none

        none_token = jwt.encode({"sub": "1", "type": "access", "tv": 0, "exp": datetime.utcnow() + timedelta(hours=1)}, key="", algorithm="none")

        res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {none_token}"})

        assert res.status_code == 401, f"Expected 401, got {res.status_code}"



@pytest.mark.asyncio

async def test_auth_wrong_signature_rejected():

    """Verify that tokens signed with an arbitrary or wrong secret key are rejected with 401."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        fake_token = jwt.encode(

            {"sub": "1", "type": "access", "tv": 0, "exp": datetime.utcnow() + timedelta(hours=1)},

            key="wrong_secret_key_that_does_not_match_app_key_123456",

            algorithm="HS256"

        )

        res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {fake_token}"})

        assert res.status_code == 401, f"Expected 401, got {res.status_code}"



@pytest.mark.asyncio

async def test_auth_expired_jwt_rejected():

    """Verify that expired JWT access tokens return 401."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        expired_token = jwt.encode(

            {"sub": "1", "type": "access", "tv": 0, "exp": datetime.utcnow() - timedelta(minutes=5)},

            key=settings.JWT_SECRET,

            algorithm="HS256"

        )

        res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired_token}"})

        assert res.status_code == 401, f"Expected 401, got {res.status_code}"



@pytest.mark.asyncio

async def test_auth_invalid_subject_rejected():

    """Verify that tokens with non-integer subject return 401 rather than unhandled 500 error."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        bad_sub_token = jwt.encode(

            {"sub": "invalid_sub_string", "type": "access", "tv": 0, "exp": datetime.utcnow() + timedelta(hours=1)},

            key=settings.JWT_SECRET,

            algorithm="HS256"

        )

        res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_sub_token}"})

        assert res.status_code == 401, f"Expected 401, got {res.status_code}"



@pytest.mark.asyncio

async def test_auth_token_version_revocation_on_change_password():

    """Verify that changing password increments token_version and invalidates both old access and refresh tokens."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        headers, old_token, old_refresh, uname, pwd = await register_user(ac, "pwd_change")



        # Change password

        new_pwd = "NewPassword456!"

        cp_res = await ac.post("/api/v1/auth/change-password", headers=headers, json={

            "current_password": pwd,

            "new_password": new_pwd

        })

        assert cp_res.status_code == 200, f"Change password failed: {cp_res.text}"



        # Old access token must be revoked (401)

        stale_res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old_token}"})

        assert stale_res.status_code == 401, f"Old access token was not revoked: {stale_res.status_code}"



        # Old refresh token must be revoked (401)

        ref_res = await ac.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})

        assert ref_res.status_code == 401, f"Old refresh token was not revoked: {ref_res.status_code}"



        # Login with new password succeeds and works

        login_res = await ac.post("/api/v1/auth/login", json={"username": uname, "password": new_pwd})

        assert login_res.status_code == 200



@pytest.mark.asyncio

async def test_auth_logout_endpoint_revokes_session():

    """Verify that /auth/logout endpoint increments token_version and revokes existing tokens."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        headers, token, refresh, _, _ = await register_user(ac, "logout_user")



        # Verify active session works

        chk = await ac.get("/api/v1/auth/me", headers=headers)

        assert chk.status_code == 200



        # Call /auth/logout

        logout_res = await ac.post("/api/v1/auth/logout", headers=headers)

        assert logout_res.status_code == 200

        assert logout_res.json()["success"] is True



        # Now previous access token must be rejected with 401

        after_logout = await ac.get("/api/v1/auth/me", headers=headers)

        assert after_logout.status_code == 401



        # Previous refresh token must also be rejected

        ref_chk = await ac.post("/api/v1/auth/refresh", json={"refresh_token": refresh})

        assert ref_chk.status_code == 401



@pytest.mark.asyncio

async def test_auth_banned_user_tokens_and_login_rejected():

    """Verify that banning a user increments token_version and immediately revokes active tokens and rejects logins."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        # Admin login

        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})

        admin_token = admin_login.json()["data"]["access_token"]

        admin_headers = {"Authorization": f"Bearer {admin_token}"}



        # Create target user

        headers, user_token, refresh, uname, pwd = await register_user(ac, "ban_target")

        u_info = await ac.get("/api/v1/auth/me", headers=headers)

        user_id = u_info.json()["data"]["id"]



        # Admin bans user

        ban_res = await ac.put(f"/api/v1/admin/users/{user_id}/status?status_val=BANNED", headers=admin_headers)

        assert ban_res.status_code == 200



        # Target user token must now be blocked

        me_res = await ac.get("/api/v1/auth/me", headers=headers)

        assert me_res.status_code == 403



        # Refresh must be rejected

        rf_res = await ac.post("/api/v1/auth/refresh", json={"refresh_token": refresh})

        assert rf_res.status_code == 401



        # Login must be rejected

        log_res = await ac.post("/api/v1/auth/login", json={"username": uname, "password": pwd})

        assert log_res.status_code == 403



@pytest.mark.asyncio

async def test_auth_case_insensitive_registration_blocked():

    """Verify that registering with existing username or email in different casing is rejected."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        _, _, _, uname, _ = await register_user(ac, "case_test")



        # Try to register same username in upper case

        dup_user = await ac.post("/api/v1/auth/register", json={

            "username": uname.upper(),

            "email": f"diff_email_{uuid.uuid4().hex[:4]}@example.com",

            "password": "Password123!",

            "confirm_password": "Password123!",

            "full_name": "Duplicate"

        })

        assert dup_user.status_code == 400



@pytest.mark.asyncio

async def test_auth_whitespace_password_rejected():

    """Verify that pure whitespace passwords are rejected at registration."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        uid = uuid.uuid4().hex[:6]

        res = await ac.post("/api/v1/auth/register", json={

            "username": f"white_pwd_{uid}",

            "email": f"white_pwd_{uid}@example.com",

            "password": "      ",

            "confirm_password": "      ",

            "full_name": "Whitespace Password"

        })

        assert res.status_code == 400





@pytest.mark.asyncio

async def test_auth_passwords_over_72_bytes_rejected():

    """Verify that passwords exceeding bcrypt 72 bytes limit are safely rejected with 400 rather than crashing with 500."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        long_pwd = "A" * 80

        uid = uuid.uuid4().hex[:6]

        # Registration

        reg_res = await ac.post("/api/v1/auth/register", json={

            "username": f"long_pwd_{uid}",

            "email": f"long_pwd_{uid}@example.com",

            "password": long_pwd,

            "confirm_password": long_pwd,

            "full_name": "Long Password"

        })

        assert reg_res.status_code in [400, 422]



        # Change password

        headers, _, _, _, _ = await register_user(ac, "pwd_len")

        cp_res = await ac.post("/api/v1/auth/change-password", headers=headers, json={

            "current_password": "TestPassword123!",

            "new_password": long_pwd

        })

        assert cp_res.status_code in [400, 422]



        # Reset password

        rp_res = await ac.post("/api/v1/auth/reset-password", json={

            "token": "valid_looking_token_with_length_over_32_characters",

            "new_password": long_pwd

        })

        assert rp_res.status_code in [400, 422]





@pytest.mark.asyncio

async def test_auth_password_null_byte_rejected():

    """Verify that passwords with null bytes are safely rejected with 400."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        uid = uuid.uuid4().hex[:6]

        null_pwd = "bad" + chr(0) + "password123"

        reg_res = await ac.post("/api/v1/auth/register", json={

            "username": f"null_pwd_{uid}",

            "email": f"null_pwd_{uid}@example.com",

            "password": null_pwd,

            "confirm_password": null_pwd,

            "full_name": "Null Password"

        })

        assert reg_res.status_code in [400, 422]





@pytest.mark.asyncio

async def test_auth_reset_password_whitespace_rejected():

    """Verify that pure whitespace new_password in reset-password is rejected."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        res = await ac.post("/api/v1/auth/reset-password", json={

            "token": "token_that_is_at_least_32_characters_long_for_pydantic",

            "new_password": "        "

        })

        assert res.status_code == 400





@pytest.mark.asyncio

async def test_auth_jwt_missing_exp_rejected():

    """Verify that JWTs missing the exp claim are strictly rejected with 401."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        no_exp_token = jwt.encode(

            {"sub": "1", "type": "access", "tv": 0},

            key=settings.JWT_SECRET,

            algorithm="HS256"

        )

        res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {no_exp_token}"})

        assert res.status_code == 401





@pytest.mark.asyncio

async def test_auth_jwt_missing_sub_rejected():

    """Verify that JWTs missing the sub claim are strictly rejected with 401."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        no_sub_token = jwt.encode(

            {"type": "access", "tv": 0, "exp": datetime.utcnow() + timedelta(hours=1)},

            key=settings.JWT_SECRET,

            algorithm="HS256"

        )

        res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {no_sub_token}"})

        assert res.status_code == 401





@pytest.mark.asyncio

async def test_auth_jwt_non_positive_sub_rejected():

    """Verify that JWTs with non-positive integer subjects (<= 0) are strictly rejected with 401."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        for bad_sub in ["0", "-1"]:

            bad_token = jwt.encode(

                {"sub": bad_sub, "type": "access", "tv": 0, "exp": datetime.utcnow() + timedelta(hours=1)},

                key=settings.JWT_SECRET,

                algorithm="HS256"

            )

            res = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {bad_token}"})

            assert res.status_code == 401





@pytest.mark.asyncio

async def test_auth_token_type_enforcement():

    """Verify that refresh tokens cannot access protected routes and access tokens cannot be used to refresh."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        headers, access_tok, refresh_tok, _, _ = await register_user(ac, "type_enforce")



        # 1. Refresh token on access route -> 401

        res_me = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {refresh_tok}"})

        assert res_me.status_code == 401



        # 2. Access token on refresh route -> 401

        res_ref = await ac.post("/api/v1/auth/refresh", json={"refresh_token": access_tok})

        assert res_ref.status_code == 401





@pytest.mark.asyncio

async def test_auth_reset_password_revokes_all_active_tokens():

    """Verify that completing a password reset invalidates existing access and refresh tokens."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        headers, access_tok, refresh_tok, uname, old_pwd = await register_user(ac, "reset_revoke")



        # Request reset token

        email = f"{uname}@example.com"

        forgot_res = await ac.post("/api/v1/auth/forgot-password", json={"email": email})

        assert forgot_res.status_code == 200

        raw_reset_token = forgot_res.json()["data"]["reset_token"]

        assert raw_reset_token is not None



        # Reset password

        new_pwd = "BrandNewPassword999!"

        reset_res = await ac.post("/api/v1/auth/reset-password", json={

            "token": raw_reset_token,

            "new_password": new_pwd

        })

        assert reset_res.status_code == 200



        # Old access token must now be revoked (401)

        chk_access = await ac.get("/api/v1/auth/me", headers=headers)

        assert chk_access.status_code == 401



        # Old refresh token must now be revoked (401)

        chk_refresh = await ac.post("/api/v1/auth/refresh", json={"refresh_token": refresh_tok})

        assert chk_refresh.status_code == 401



        # Login with old password must fail

        old_login = await ac.post("/api/v1/auth/login", json={"username": uname, "password": old_pwd})

        assert old_login.status_code == 400



        # Login with new password must succeed

        new_login = await ac.post("/api/v1/auth/login", json={"username": uname, "password": new_pwd})

        assert new_login.status_code == 200





@pytest.mark.asyncio

async def test_auth_empty_and_whitespace_headers_rejected():

    """Verify that empty/whitespace Bearer tokens and API keys are rejected with 401."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        # Whitespace Bearer token

        res_bearer = await ac.get("/api/v1/auth/me", headers={"Authorization": "Bearer   "})

        assert res_bearer.status_code == 401



        # Whitespace X-API-KEY

        res_key = await ac.get("/api/v1/user-api/services", headers={"X-API-KEY": "   "})

        assert res_key.status_code == 401





@pytest.mark.asyncio

async def test_auth_registration_username_length_and_whitespace_rejected():

    """Verify that registration rejects usernames with whitespace or fewer than 3 characters."""

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:

        # Pure whitespace username

        res_ws = await ac.post("/api/v1/auth/register", json={

            "username": "   ",

            "email": f"ws_user_{uuid.uuid4().hex[:4]}@example.com",

            "password": "Password123!",

            "confirm_password": "Password123!",

            "full_name": "Whitespace"

        })

        assert res_ws.status_code == 422 or res_ws.status_code == 400



        # 2-character username

        res_short = await ac.post("/api/v1/auth/register", json={

            "username": "ab",

            "email": f"short_user_{uuid.uuid4().hex[:4]}@example.com",

            "password": "Password123!",

            "confirm_password": "Password123!",

            "full_name": "Short"

        })

        assert res_short.status_code == 422 or res_short.status_code == 400

