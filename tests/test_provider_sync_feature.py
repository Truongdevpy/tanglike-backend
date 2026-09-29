import sys
import os
sys.path.insert(0, os.path.abspath('backend'))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.services.pricing_service import calculate_single_price, calculate_service_prices, generate_markup_previews

@pytest.mark.asyncio
async def test_pricing_and_autorounding_rules():
    # 1. Percent markup + nearest round 10
    # 15.0 * 1.3 = 19.5 -> nearest 10 = 20.0
    p1 = calculate_single_price(15.0, "PERCENT", 30.0, True, "nearest", 10.0)
    assert p1 == 20.0

    # 2. Percent markup + ceil round 50
    # 22.0 * 1.3 = 28.6 -> ceil 50 = 50.0
    p2 = calculate_single_price(22.0, "PERCENT", 30.0, True, "ceil", 50.0)
    assert p2 == 50.0

    # 3. Percent markup + floor round 100
    # 1250.0 * 1.3 = 1625.0 -> floor 100 = 1600.0
    p3 = calculate_single_price(1250.0, "PERCENT", 30.0, True, "floor", 100.0)
    assert p3 == 1600.0

    # 4. Fixed markup + round nearest 1000
    # 15000 + 5200 = 20200 -> nearest 1000 = 20000.0
    p4 = calculate_single_price(15000.0, "FIXED", 5200.0, True, "nearest", 1000.0)
    assert p4 == 20000.0

    # 5. Dual tier prices (Customer + Dealer)
    prices = calculate_service_prices(
        rate=100.0,
        user_markup_type="PERCENT",
        user_markup_value=30.0,
        dealer_markup_type="PERCENT",
        dealer_markup_value=15.0,
        auto_round=True,
        round_type="nearest",
        round_unit=10.0
    )
    assert prices["selling_price"] == 130.0
    assert prices["dealer_price"] == 120.0  # 115 rounded to nearest multiple of 10 is 120

@pytest.mark.asyncio
async def test_provider_connection_and_masking():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login_res = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        token = login_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 1. Test connection with transient endpoint
        t_res = await ac.post("/api/v1/admin/providers/test-connection", json={
            "name": "Test Mock Provider",
            "base_url": "https://example-provider.com/api/v2",
            "api_key": "test_api_key_12345678",
            "provider_type": "mock"
        }, headers=headers)
        assert t_res.status_code == 200
        assert t_res.json()["data"]["success"] is True
        assert t_res.json()["data"]["balance"] is not None

        # 2. Create provider
        create_res = await ac.post("/api/v1/admin/providers", json={
            "name": "Mock Provider Masking Test",
            "base_url": "https://mock-provider.com/api/v2",
            "api_key": "my_super_secret_key_8899",
            "provider_type": "mock"
        }, headers=headers)
        assert create_res.status_code == 200
        created_prov = create_res.json()["data"]
        prov_id = created_prov["id"]

        # 3. Verify API Key is masked in response (Security requirement 7)
        assert "api_key" not in created_prov
        assert created_prov.get("api_key_masked") is not None
        assert created_prov["api_key_masked"].endswith("8899")
        assert "••••" in created_prov["api_key_masked"]

        # 4. Test connection on saved provider
        saved_test = await ac.post(f"/api/v1/admin/providers/{prov_id}/test-connection", headers=headers)
        assert saved_test.status_code == 200
        assert saved_test.json()["data"]["success"] is True
        assert saved_test.json()["data"]["balance"] is not None

@pytest.mark.asyncio
async def test_catalog_import_mapping_and_sync_flow():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        login_res = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        token = login_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # Create Mock Provider
        c_res = await ac.post("/api/v1/admin/providers", json={
            "name": "Provider Auto Test",
            "base_url": "https://mock-provider.com/api/v2",
            "api_key": "mock_secret_key_9999",
            "provider_type": "mock"
        }, headers=headers)
        prov_id = c_res.json()["data"]["id"]

        # 1. Fetch Catalog
        cat_res = await ac.get(f"/api/v1/admin/providers/{prov_id}/catalog", headers=headers)
        assert cat_res.status_code == 200
        catalog = cat_res.json()["data"]
        assert len(catalog) >= 1

        selected_item = catalog[0]

        # 2. Preview Bulk Markup
        prev_res = await ac.post("/api/v1/admin/providers/preview-markup", json={
            "items": [selected_item],
            "markup_type": "PERCENT",
            "markup_value": 30.0,
            "dealer_markup_type": "PERCENT",
            "dealer_markup_value": 15.0,
            "auto_round": True,
            "round_type": "nearest",
            "round_unit": 10.0
        }, headers=headers)
        assert prev_res.status_code == 200
        previews = prev_res.json()["data"]
        assert len(previews) == 1
        assert "selling_price" in previews[0]
        assert "dealer_price" in previews[0]

        # 3. Import Service
        imp_res = await ac.post(f"/api/v1/admin/providers/{prov_id}/import-services", json={
            "services": [{
                "external_service_id": str(selected_item["service_id"]),
                "name": selected_item["name"],
                "category": selected_item["category"],
                "rate": selected_item["rate"],
                "min": selected_item["min"],
                "max": selected_item["max"],
                "refill": selected_item["refill"],
                "cancel": selected_item["cancel"],
                "markup_type": "PERCENT",
                "markup_value": 30.0,
                "dealer_markup_type": "PERCENT",
                "dealer_markup_value": 15.0,
                "auto_round": True,
                "round_type": "nearest",
                "round_unit": 10.0,
                "selling_price": previews[0]["selling_price"],
                "dealer_price": previews[0]["dealer_price"]
            }]
        }, headers=headers)
        assert imp_res.status_code == 200
        assert imp_res.json()["data"]["imported_count"] + imp_res.json()["data"]["updated_count"] >= 1

        # 4. Check Mappings
        m_res = await ac.get(f"/api/v1/admin/mappings?provider_id={prov_id}", headers=headers)
        assert m_res.status_code == 200
        mappings = m_res.json()["data"]
        assert len(mappings) >= 1
        map_id = mappings[0]["id"]
        assert mappings[0]["external_service_id"] == str(selected_item["service_id"])

        # 5. Update Single Mapping markup
        up_res = await ac.put(f"/api/v1/admin/mappings/{map_id}", json={
            "markup_value": 50.0,
            "dealer_markup_value": 25.0
        }, headers=headers)
        assert up_res.status_code == 200
        assert up_res.json()["data"]["markup_value"] == 50.0

        # 6. Sync Mappings
        sync_res = await ac.post(f"/api/v1/admin/providers/{prov_id}/sync-mappings?alert_threshold_percent=15", headers=headers)
        assert sync_res.status_code == 200
        sync_data = sync_res.json()["data"]
        assert "total_mapped" in sync_data

        # 7. Check Price Sync Logs
        log_res = await ac.get(f"/api/v1/admin/price-sync-logs?provider_id={prov_id}", headers=headers)
        assert log_res.status_code == 200
        logs = log_res.json()["data"]
        assert len(logs) >= 1
        assert logs[0]["provider_name"] == "Provider Auto Test"

        # 8. Unlink Mapping
        del_res = await ac.delete(f"/api/v1/admin/mappings/{map_id}?unlink_service=true", headers=headers)
        assert del_res.status_code == 200
        assert del_res.json()["data"] is True