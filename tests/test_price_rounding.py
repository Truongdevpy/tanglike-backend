import pytest
from app.routers.admin import PUBLIC_SYSTEM_SETTING_KEYS
from app.services.pricing_service import calculate_single_price, calculate_service_prices

def test_rounding_setting_keys():
    assert "auto_round" in PUBLIC_SYSTEM_SETTING_KEYS
    assert "round_type" in PUBLIC_SYSTEM_SETTING_KEYS
    assert "round_unit" in PUBLIC_SYSTEM_SETTING_KEYS

def test_calculate_single_price_rounding():
    # 10983.47 + 30% = 14278.511
    # nearest to 10 -> 14280.0
    price_nearest_10 = calculate_single_price(rate=10983.47, markup_value=30.0, auto_round=True, round_type="nearest", round_unit=10.0)
    assert price_nearest_10 == 14280.0

    # ceil to 10 -> 14280.0
    price_ceil_10 = calculate_single_price(rate=10983.47, markup_value=30.0, auto_round=True, round_type="ceil", round_unit=10.0)
    assert price_ceil_10 == 14280.0

    # floor to 10 -> 14270.0
    price_floor_10 = calculate_single_price(rate=10983.47, markup_value=30.0, auto_round=True, round_type="floor", round_unit=10.0)
    assert price_floor_10 == 14270.0

    # nearest to 100 -> 14300.0
    price_nearest_100 = calculate_single_price(rate=10983.47, markup_value=30.0, auto_round=True, round_type="nearest", round_unit=100.0)
    assert price_nearest_100 == 14300.0

    # nearest to 1.0 (remove decimals) -> 14279.0
    price_nearest_1 = calculate_single_price(rate=10983.47, markup_value=30.0, auto_round=True, round_type="nearest", round_unit=1.0)
    assert price_nearest_1 == 14279.0


@pytest.mark.asyncio
async def test_category_service_count_and_hide_empty():
    from httpx import AsyncClient, ASGITransport
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # 1. Get all categories
        res_all = await ac.get("/api/v1/services/categories")
        assert res_all.status_code == 200
        all_cats = res_all.json()["data"]
        assert len(all_cats) > 0
        for c in all_cats:
            assert "service_count" in c

        # 2. Get with hide_empty=True
        res_filtered = await ac.get("/api/v1/services/categories?hide_empty=true")
        assert res_filtered.status_code == 200
        filtered_cats = res_filtered.json()["data"]
        for c in filtered_cats:
            assert c["service_count"] > 0
