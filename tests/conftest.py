"""Shared database bootstrap for the complete backend test suite."""

import os

# Use isolated test database so tests NEVER touch production tanglike.db
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_tanglike.db"
os.environ["APP_ENV"] = "test"

import pytest_asyncio

from app.database.session import Base, engine
from app.main import seed_initial_data


@pytest_asyncio.fixture(scope="session", autouse=True)
async def initialize_test_database():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await seed_initial_data()
    yield

import pytest
from app.providers.mock import MockSMMProvider
from app.providers.manager import ProviderManager

@pytest.fixture(autouse=True)
def prevent_all_real_provider_calls(monkeypatch):
    """Safety guard: Never allow ANY automated test to call external APIs or spend real money."""
    mock_p = MockSMMProvider()
    monkeypatch.setattr(ProviderManager, "get_provider", lambda provider=None: mock_p)
    monkeypatch.setattr(ProviderManager, "create_transient_provider", lambda *args, **kwargs: mock_p)