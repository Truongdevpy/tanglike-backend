# -*- coding: utf-8 -*-
import pytest
from app.providers.base import ProviderServiceItem
from app.providers.thmxh import THMXHProvider
from app.providers.generic_smm import GenericSMMProvider
from app.providers.tuongtaccheo import TuongTacCheoProvider
from app.services.pricing_service import convert_provider_rate_to_vnd, calculate_single_price
from app.database.session import engine, AsyncSessionLocal
from sqlalchemy.pool import NullPool
from unittest.mock import AsyncMock, MagicMock
from app.workers.order_worker import OrderWorker
from app.models.all import RefillRequest, Order, Provider

def test_convert_provider_rate_to_vnd_usd():
    # USD rate 0.0066 at 28,000 VND/USD should be 184.8 VND
    res = convert_provider_rate_to_vnd(0.0066, provider_type="thmxh", currency="USD", usd_rate=28000.0)
    assert res == 184.8

    # USD rate 655.20 at 28,000 VND/USD should be 18,345,600 VND
    res_high = convert_provider_rate_to_vnd(655.2, provider_type="thmxh", currency="USD", usd_rate=28000.0)
    assert res_high == 18345600.0

def test_convert_provider_rate_to_vnd_vietnam_local():
    # Local Vietnamese provider with rate 150 VND should stay 150 VND
    res_low_vnd = convert_provider_rate_to_vnd(150.0, provider_type="generic_smm", currency="VND", usd_rate=28000.0)
    assert res_low_vnd == 150.0

    res_50_vnd = convert_provider_rate_to_vnd(50.0, provider_type="generic_smm", currency="VND", usd_rate=28000.0)
    assert res_50_vnd == 50.0

def test_convert_provider_rate_to_vnd_ttc_xu():
    # TTC 1000 Xu per unit at divider 100 -> (1000 * 1000) / 100 = 10,000 VND per 1,000 units
    res_ttc = convert_provider_rate_to_vnd(1000.0, provider_type="tuongtaccheo", currency="XU", ttc_divider=100.0)
    assert res_ttc == 10000.0

def test_thmxh_fallback_services_are_vnd():
    adapter = THMXHProvider(api_key="", usd_rate=28000.0)
    fallback = adapter._get_fallback_services()
    assert len(fallback) > 0
    for s in fallback:
        assert s.currency == "VND"
        assert s.raw_rate == s.rate
        assert s.rate in [900.0, 8000.0, 12000.0, 600.0, 2500.0]

@pytest.mark.asyncio
async def test_generic_smm_currency_conversion():
    p_usd = GenericSMMProvider(base_url="https://fake-smm.com", api_key="fake", currency="USD", exchange_rate=28000.0)
    assert p_usd.currency == "USD"

    p_vnd = GenericSMMProvider(base_url="https://fake-vn-smm.com", api_key="fake", currency="VND")
    assert p_vnd.currency == "VND"

def test_engine_pool_is_nullpool_for_sqlite():
    if "sqlite" in str(engine.url):
        assert isinstance(engine.pool, NullPool)

@pytest.mark.asyncio
async def test_refill_request_rejected_when_no_refill_id():
    async with AsyncSessionLocal() as db:
        order = Order(
            user_id=1,
            service_id=1,
            link="https://facebook.com/testpost123",
            quantity=100,
            price=1000.0,
            status="COMPLETED",
            external_order_id="ext_order_test_999"
        )
        db.add(order)
        await db.flush()

        refill = RefillRequest(
            order_id=order.id,
            user_id=1,
            status="PENDING"
        )
        db.add(refill)
        await db.commit()
        refill_id = refill.id

    worker = OrderWorker()
    mock_adapter = MagicMock()
    mock_adapter.refill_order = AsyncMock(return_value={"error": "Refill not allowed for this service"})
    mock_adapter.get_refill_status = AsyncMock(return_value={"status": "PROCESSING"})

    from app.providers.manager import ProviderManager
    orig_get = ProviderManager.get_provider
    ProviderManager.get_provider = MagicMock(return_value=mock_adapter)

    try:
        await worker.sync_refill_requests()
        async with AsyncSessionLocal() as db:
            from sqlalchemy import select
            refreshed = (await db.execute(select(RefillRequest).where(RefillRequest.id == refill_id))).scalar_one_or_none()
            assert refreshed is not None
            assert refreshed.status == "REJECTED"
    finally:
        ProviderManager.get_provider = orig_get
