import os
import sys

import pytest
import pytest_asyncio
from datetime import datetime, timedelta
from httpx import ASGITransport, AsyncClient

sys.path.insert(0, os.path.abspath("backend"))

from app.database.session import Base, engine
from app.main import app
from app.models.all import AuditLog, EmailVerificationToken, JobRun, PasswordResetToken, PriceSyncLog
from app.workers.order_worker import OrderWorker


@pytest_asyncio.fixture(scope="module", autouse=True)
async def create_schema():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_worker_records_successful_job_run():
    worker = OrderWorker()

    async def succeeds():
        return None

    await worker._run_monitored("test_success", succeeds)
    from app.database.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(JobRun.__table__.select().where(JobRun.job_name == "test_success"))).all()
    assert rows
    assert rows[-1].status == "SUCCESS"


@pytest.mark.asyncio
async def test_worker_records_failed_job_run():
    worker = OrderWorker()

    async def fails():
        raise RuntimeError("expected worker failure")

    await worker._run_monitored("test_failure", fails)
    from app.database.session import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(JobRun.__table__.select().where(JobRun.job_name == "test_failure"))).all()
    assert rows
    assert rows[-1].status == "FAILED"
    assert "expected worker failure" in rows[-1].details


@pytest.mark.asyncio
async def test_provider_service_sync_uses_background_mapping_sync(monkeypatch):
    calls = []

    async def fake_sync(*, id, alert_threshold_percent, db):
        calls.append((id, alert_threshold_percent))

    monkeypatch.setattr("app.routers.admin_providers.admin_sync_provider_mappings", fake_sync)
    worker = OrderWorker()
    await worker.sync_provider_services()
    assert calls
    assert all(threshold == 15.0 for _, threshold in calls)


@pytest.mark.asyncio
async def test_job_runs_endpoint_requires_admin():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/admin/job-runs")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_job_status_endpoint_requires_admin():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/v1/admin/job-status")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_cleanup_removes_expired_reset_tokens_and_old_runs():
    from app.database.session import AsyncSessionLocal
    old_time = datetime.utcnow() - timedelta(days=400)
    async with AsyncSessionLocal() as db:
        db.add(JobRun(job_name="old", status="SUCCESS", started_at=old_time, completed_at=old_time, duration_ms=1))
        db.add(PasswordResetToken(user_id=1, token_hash="f" * 64, expires_at=old_time, used_at=old_time))
        db.add(EmailVerificationToken(user_id=1, token_hash="e" * 64, expires_at=old_time, used_at=old_time))
        db.add(AuditLog(action="OLD_TEST", created_at=old_time))
        db.add(PriceSyncLog(
            provider_id=1, external_service_id="old", service_name="Old sync",
            created_at=old_time,
        ))
        await db.commit()
    worker = OrderWorker()
    await worker.cleanup_expired_records()
    async with AsyncSessionLocal() as db:
        assert not (await db.execute(JobRun.__table__.select().where(JobRun.job_name == "old"))).all()
        assert not (await db.execute(PasswordResetToken.__table__.select().where(PasswordResetToken.token_hash == "f" * 64))).all()
        assert not (await db.execute(EmailVerificationToken.__table__.select().where(EmailVerificationToken.token_hash == "e" * 64))).all()
        assert not (await db.execute(AuditLog.__table__.select().where(AuditLog.action == "OLD_TEST"))).all()
        assert not (await db.execute(PriceSyncLog.__table__.select().where(PriceSyncLog.external_service_id == "old"))).all()
