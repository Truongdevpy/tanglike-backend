import os
import sys
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath("backend"))

from app.database.session import Base, engine
from app.main import app


@pytest_asyncio.fixture(scope="module", autouse=True)
async def create_schema():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_public_registration_never_grants_admin_role():
    username = f"public_{uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/api/v1/auth/register", headers={"X-Bypass-Rate-Limit": "true"}, json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "password-123",
            "confirm_password": "password-123",
        })
    assert response.status_code == 200
    assert response.json()["data"]["user"]["role"] == "USER"
    assert response.json()["data"]["user"]["balance"] == 0
