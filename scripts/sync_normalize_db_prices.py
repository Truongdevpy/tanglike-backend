# -*- coding: utf-8 -*-
import sys, sqlite3, asyncio, httpx
sys.path.insert(0, 'backend')
from app.utils.crypto import decrypt_secret
from app.services.pricing_service import calculate_single_price

async def sync_db(db_path: str):
    print(f"=== Syncing DB: {db_path} ===")
    conn = sqlite3.connect(db_path)
    
    # 1. Fetch provider 2 API key
    row = conn.execute("SELECT api_key_encrypted FROM providers WHERE id = 2").fetchone()
    if not row or not row[0]:
        print("  No API key for provider 2 in", db_path)
        conn.close()
        return
        
    api_key = decrypt_secret(row[0])
    
    # 2. Fetch live services from THMXH API
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post("https://thmxh.com/api/v2", data={"key": api_key, "action": "services"}, timeout=15)
            live_data = r.json()
        except Exception as e:
            print(f"  Failed to fetch live services: {e}")
            conn.close()
            return
            
    if not isinstance(live_data, list):
        print(f"  Live data is not list: {live_data}")
        conn.close()
        return
        
    live_rates = {str(s.get("service") or s.get("id")): float(s.get("rate") or 0.0) for s in live_data}
    print(f"  Fetched {len(live_rates)} live services from THMXH API")
    
    # 3. Read current system settings
    usd_rate_row = conn.execute("SELECT value FROM system_settings WHERE key = 'thmxh_usd_rate'").fetchone()
    usd_rate = float(usd_rate_row[0]) if usd_rate_row and usd_rate_row[0] else 28000.0
    
    mk_row = conn.execute("SELECT value FROM system_settings WHERE key = 'thmxh_markup_percent'").fetchone()
    markup = float(mk_row[0]) if mk_row and mk_row[0] else 30.0
    
    dmk_row = conn.execute("SELECT value FROM system_settings WHERE key = 'thmxh_dealer_markup_percent'").fetchone()
    dealer_markup = float(dmk_row[0]) if dmk_row and dmk_row[0] else 15.0
    
    print(f"  Using USD Rate: {usd_rate}, Markup: {markup}%, Dealer Markup: {dealer_markup}%")
    
    # 4. Update services and mappings for provider 2
    services = conn.execute("SELECT id, external_service_id, provider_price, price FROM services WHERE provider_id = 2").fetchall()
    updated_cnt = 0
    mismatch_fixed_cnt = 0
    
    for s_id, ext_id, old_pp, old_p in services:
        ext_str = str(ext_id or "")
        if ext_str in live_rates:
            live_usd = live_rates[ext_str]
            # Exact conversion: raw USD * usd_rate
            cost_vnd = round(live_usd * usd_rate, 2)
            if cost_vnd <= 0:
                cost_vnd = 1.0
                
            sell_p = calculate_single_price(cost_vnd, markup_type="PERCENT", markup_value=markup, auto_round=True, round_type="nearest", round_unit=10.0)
            dealer_p = calculate_single_price(cost_vnd, markup_type="PERCENT", markup_value=dealer_markup, auto_round=True, round_type="nearest", round_unit=10.0)
            
            if abs(old_pp - cost_vnd) > 1.0:
                mismatch_fixed_cnt += 1
                
            conn.execute(
                "UPDATE services SET provider_price = ?, price = ?, dealer_price = ?, updated_at = datetime('now') WHERE id = ?",
                (cost_vnd, sell_p, dealer_p, s_id)
            )
            
            # Update mapping with raw USD
            conn.execute(
                "UPDATE provider_service_mappings SET original_rate = ?, selling_price = ?, dealer_price = ?, last_synced_at = datetime('now') WHERE service_id = ?",
                (live_usd, sell_p, dealer_p, s_id)
            )
            updated_cnt += 1
            
    conn.commit()
    print(f"  Updated {updated_cnt} services, corrected {mismatch_fixed_cnt} rate mismatches in {db_path}")
    conn.close()

async def main():
    for p in ["backend/tanglike.db", "tanglike.db"]:
        try:
            await sync_db(p)
        except Exception as e:
            print(f"Error syncing {p}: {e}")

if __name__ == "__main__":
    asyncio.run(main())