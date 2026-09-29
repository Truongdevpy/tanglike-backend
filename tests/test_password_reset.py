import os
import sys
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath("backend"))

from app.main import app
from app.database.session import engine, Base


@pytest_asyncio.fixture(scope="module", autouse=True)
async def create_schema():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_password_reset_token_is_one_time():
    username = f"reset_{uuid.uuid4().hex[:8]}"
    email = f"{username}@example.com"
    headers = {"X-Bypass-Rate-Limit": "true"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        register = await client.post("/api/v1/auth/register", headers=headers, json={
            "username": username,
            "email": email,
            "password": "old-password-123",
            "confirm_password": "old-password-123",
        })
        assert register.status_code == 200

        requested = await client.post("/api/v1/auth/forgot-password", headers=headers, json={"email": email})
        assert requested.status_code == 200
        token = requested.json()["data"]["reset_token"]
        assert token

        reset = await client.post("/api/v1/auth/reset-password", headers=headers, json={
            "token": token,
            "new_password": "new-password-123",
        })
        assert reset.status_code == 200

        reused = await client.post("/api/v1/auth/reset-password", headers=headers, json={
            "token": token,
            "new_password": "another-password-123",
        })
        assert reused.status_code == 400

        login = await client.post("/api/v1/auth/login", headers=headers, json={
            "username": username,
            "password": "new-password-123",
        })
        assert login.status_code == 200


@pytest.mark.asyncio
async def test_logout_all_revokes_existing_access_token():
    username = f"logout_{uuid.uuid4().hex[:8]}"
    headers = {"X-Bypass-Rate-Limit": "true"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        registered = await client.post("/api/v1/auth/register", headers=headers, json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password-123",
            "confirm_password": "password-123",
        })
        token = registered.json()["data"]["access_token"]
        authenticated = {**headers, "Authorization": f"Bearer {token}"}
        logout_all = await client.post("/api/v1/auth/logout-all", headers=authenticated)
        assert logout_all.status_code == 200
        rejected = await client.get("/api/v1/auth/me", headers=authenticated)
        assert rejected.status_code == 401


@pytest.mark.asyncio
async def test_email_verification_token_is_one_time():
    username = f"verify_{uuid.uuid4().hex[:8]}"
    headers = {"X-Bypass-Rate-Limit": "true"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        register = await client.post("/api/v1/auth/register", headers=headers, json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password-123",
            "confirm_password": "password-123",
        })
        assert register.status_code == 200
        assert register.json()["data"]["user"]["email_verified"] is False
        token = (await client.post(
            "/api/v1/auth/resend-email-verification",
            headers={**headers, "Authorization": f"Bearer {register.json()['data']['access_token']}"},
        )).json()["data"]["verification_token"]
        assert token
        verified = await client.post("/api/v1/auth/verify-email", headers=headers, json={"token": token})
        assert verified.status_code == 200
        assert (await client.post("/api/v1/auth/verify-email", headers=headers, json={"token": token})).status_code == 400
