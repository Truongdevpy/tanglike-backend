import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.auth.security import create_access_token

@pytest.mark.asyncio
async def test_hide_and_unhide_categories_and_services():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Admin login or token
        login_res = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        if login_res.status_code != 200:
            login_res = await client.post("/api/v1/auth/login", json={"username": "admin", "password": "password123"})
        assert login_res.status_code == 200, f"Admin login failed: {login_res.text}"
        admin_token = login_res.json()["data"]["access_token"]
        admin_headers = {"Authorization": f"Bearer {admin_token}"}

        # 2. Get all categories for admin
        res = await client.get("/api/v1/admin/categories", headers=admin_headers)
        assert res.status_code == 200
        cats = res.json()["data"]
        assert len(cats) > 0
        target_cat = next((c for c in cats if c.get("service_count", 0) > 0), cats[0])
        cat_id = target_cat["id"]

        # 3. Toggle category to INACTIVE
        res = await client.put(f"/api/v1/admin/categories/{cat_id}/status", json={"status": "INACTIVE"}, headers=admin_headers)
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "INACTIVE"

        # 4. Verify public /services/categories does NOT contain this category
        pub_res = await client.get("/api/v1/services/categories")
        assert pub_res.status_code == 200
        pub_cats = pub_res.json()["data"]
        assert not any(c["id"] == cat_id for c in pub_cats)

        # 5. Verify public /services does NOT return services from this category
        pub_srv_res = await client.get("/api/v1/services")
        assert pub_srv_res.status_code == 200
        pub_srvs = pub_srv_res.json()["data"]
        assert not any(s["category_id"] == cat_id for s in pub_srvs)

        # 6. Toggle category back to ACTIVE
        res = await client.put(f"/api/v1/admin/categories/{cat_id}/status", json={"status": "ACTIVE"}, headers=admin_headers)
        assert res.status_code == 200
        assert res.json()["data"]["status"] == "ACTIVE"

        pub_res = await client.get("/api/v1/services/categories")
        pub_cats = pub_res.json()["data"]
        assert any(c["id"] == cat_id for c in pub_cats)

        # 7. Test bulk category status
        bulk_res = await client.post("/api/v1/admin/categories/bulk-status", json={"ids": [cat_id], "status": "INACTIVE"}, headers=admin_headers)
        assert bulk_res.status_code == 200
        assert bulk_res.json()["data"]["updated"] >= 1

        bulk_res2 = await client.post("/api/v1/admin/categories/bulk-status", json={"ids": [cat_id], "status": "ACTIVE"}, headers=admin_headers)
        assert bulk_res2.status_code == 200

        # 8. Test admin services endpoint
        srv_res = await client.get("/api/v1/admin/services", headers=admin_headers)
        assert srv_res.status_code == 200
        srvs = srv_res.json()["data"]
        assert len(srvs) > 0
        target_srv = srvs[0]
        srv_id = target_srv["id"]

        # 9. Toggle service to INACTIVE
        toggle_res = await client.put(f"/api/v1/admin/services/{srv_id}/status", json={"status": "INACTIVE"}, headers=admin_headers)
        assert toggle_res.status_code == 200
        assert toggle_res.json()["data"]["status"] == "INACTIVE"

        # Verify public /services does not return this service
        pub_srv_res = await client.get("/api/v1/services")
        assert not any(s["id"] == srv_id for s in pub_srv_res.json()["data"])

        # But admin /admin/services STILL returns it!
        admin_srv_res = await client.get("/api/v1/admin/services", headers=admin_headers)
        assert any(s["id"] == srv_id and s["status"] == "INACTIVE" for s in admin_srv_res.json()["data"])

        # 10. Filter admin services by status
        inactive_res = await client.get("/api/v1/admin/services?status=INACTIVE", headers=admin_headers)
        assert inactive_res.status_code == 200
        assert all(s["status"] == "INACTIVE" for s in inactive_res.json()["data"])
        assert any(s["id"] == srv_id for s in inactive_res.json()["data"])

        # 11. Toggle service back to ACTIVE
        toggle_back = await client.put(f"/api/v1/admin/services/{srv_id}/status", json={"status": "ACTIVE"}, headers=admin_headers)
        assert toggle_back.status_code == 200
        assert toggle_back.json()["data"]["status"] == "ACTIVE"

        # 12. Bulk service status
        bulk_srv = await client.post("/api/v1/admin/services/bulk-status", json={"ids": [srv_id], "status": "INACTIVE"}, headers=admin_headers)
        assert bulk_srv.status_code == 200
        assert bulk_srv.json()["data"]["updated"] >= 1

        bulk_srv_active = await client.post("/api/v1/admin/services/bulk-status", json={"ids": [srv_id], "status": "ACTIVE"}, headers=admin_headers)
        assert bulk_srv_active.status_code == 200
