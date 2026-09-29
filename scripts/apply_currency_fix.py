# -*- coding: utf-8 -*-
import os, sys, re, sqlite3, asyncio, httpx

def patch_base_py():
    path = "backend/app/providers/base.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    old_class = '''class ProviderServiceItem(BaseModel):
    service_id: str
    name: str
    category: str
    platform: str
    rate: float  # Price per 1000
    min: int
    max: int
    dripfeed: bool = False
    refill: bool = False
    cancel: bool = False'''

    new_class = '''class ProviderServiceItem(BaseModel):
    service_id: str
    name: str
    category: str
    platform: str
    rate: float  # Gia von quy doi ra VND cho 1.000 don vi (GUARANTEED VND)
    raw_rate: Optional[float] = None  # Gia goc nguyen ban tu Provider (USD, XU, hoac VND)
    currency: Optional[str] = "VND"  # Don vi tien te goc cua Provider: USD, XU, VND
    min: int
    max: int
    dripfeed: bool = False
    refill: bool = False
    cancel: bool = False'''

    if old_class in content:
        content = content.replace(old_class, new_class)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Patched base.py")
    else:
        print("base.py already patched or class definition differs")

def patch_thmxh_py():
    path = "backend/app/providers/thmxh.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Fix test_connection action=balance currency
    old_conn_curr = 'curr = str(data.get("currency", "VND"))'
    new_conn_curr = 'curr = str(data.get("currency", "USD"))'
    if old_conn_curr in content:
        content = content.replace(old_conn_curr, new_conn_curr)
        print("Patched thmxh.py test_connection currency default to USD")

    # 2. Fix get_services: eliminate raw_rate < 500.0 magic number
    old_loop = '''                            raw_rate = float(s.get("rate") or 0.0)
                            if raw_rate < 500.0:
                                vnd_rate = round(raw_rate * self.usd_rate, 2)
                            else:
                                vnd_rate = raw_rate

                            items.append(ProviderServiceItem(
                                service_id=str(s.get("service") or s.get("id")),
                                name=name_raw,
                                category=cat_raw,
                                platform=platform,
                                rate=vnd_rate,
                                min=int(s.get("min") or 50),
                                max=int(s.get("max") or 10000),
                                refill=bool(s.get("refill", False)),
                                cancel=bool(s.get("cancel", False))
                            ))'''

    new_loop = '''                            raw_rate = float(s.get("rate") or 0.0)
                            # THMXH API v2 bao gia theo USD cho moi 1.000 don vi dich vu.
                            # Quy doi chinh xac sang VND theo ty gia self.usd_rate da cau hinh.
                            # Tuyet doi khong dung heuristic < 500d de tranh nhan kep hay sai tien te.
                            vnd_rate = round(raw_rate * self.usd_rate, 2)
                            if vnd_rate <= 0:
                                vnd_rate = 1.0

                            items.append(ProviderServiceItem(
                                service_id=str(s.get("service") or s.get("id")),
                                name=name_raw,
                                category=cat_raw,
                                platform=platform,
                                rate=vnd_rate,
                                raw_rate=raw_rate,
                                currency="USD",
                                min=int(s.get("min") or 50),
                                max=int(s.get("max") or 10000),
                                refill=bool(s.get("refill", False)),
                                cancel=bool(s.get("cancel", False))
                            ))'''

    if old_loop in content:
        content = content.replace(old_loop, new_loop)
        print("Patched thmxh.py get_services live loop")
    else:
        print("thmxh.py get_services live loop already patched or differs")

    # 3. Fix fallback services in doc_services to set raw_rate and currency="VND"
    old_fallback_append = '''            items.append(ProviderServiceItem(
                service_id=str(s["service"]),
                name=name_raw,
                category=cat_raw,
                platform=platform,
                rate=vnd_rate,
                min=int(s["min"]),
                max=int(s["max"]),
                refill=bool(s.get("refill", False)),
                cancel=bool(s.get("cancel", False))
            ))'''

    new_fallback_append = '''            items.append(ProviderServiceItem(
                service_id=str(s["service"]),
                name=name_raw,
                category=cat_raw,
                platform=platform,
                rate=vnd_rate,
                raw_rate=vnd_rate,
                currency="VND",
                min=int(s["min"]),
                max=int(s["max"]),
                refill=bool(s.get("refill", False)),
                cancel=bool(s.get("cancel", False))
            ))'''

    if old_fallback_append in content:
        content = content.replace(old_fallback_append, new_fallback_append)
        print("Patched thmxh.py fallback append")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def patch_generic_smm_py():
    path = "backend/app/providers/generic_smm.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update __init__
    old_init = '''    def __init__(self, base_url: str, api_key: str):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()'''

    new_init = '''    def __init__(self, base_url: str, api_key: str, currency: str = "USD", exchange_rate: float = 28000.0):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = (api_key or "").strip()
        self.currency = (currency or "USD").upper().strip()
        self.exchange_rate = float(exchange_rate or 28000.0)'''

    if old_init in content:
        content = content.replace(old_init, new_init)
        print("Patched generic_smm.py __init__")

    # 2. Update get_services conversion
    old_items_append = '''                    items.append(ProviderServiceItem(
                        service_id=str(s.get("service")),
                        name=name,
                        category=cat,
                        platform=platform,
                        rate=float(s.get("rate", 0.0)),
                        min=int(s.get("min", 10)),
                        max=int(s.get("max", 100000)),
                        dripfeed=bool(s.get("dripfeed", False)),
                        refill=is_refill,
                        cancel=is_cancel
                    ))'''

    new_items_append = '''                    raw_rate_val = float(s.get("rate", 0.0))
                    # Neu Provider co don vi tien te la USD, tu dong quy doi ra VND cho 1.000 don vi
                    if self.currency == "USD":
                        vnd_rate_val = round(raw_rate_val * self.exchange_rate, 2)
                    else:
                        vnd_rate_val = round(raw_rate_val, 2)
                    if vnd_rate_val <= 0:
                        vnd_rate_val = 1.0

                    items.append(ProviderServiceItem(
                        service_id=str(s.get("service")),
                        name=name,
                        category=cat,
                        platform=platform,
                        rate=vnd_rate_val,
                        raw_rate=raw_rate_val,
                        currency=self.currency,
                        min=int(s.get("min", 10)),
                        max=int(s.get("max", 100000)),
                        dripfeed=bool(s.get("dripfeed", False)),
                        refill=is_refill,
                        cancel=is_cancel
                    ))'''

    if old_items_append in content:
        content = content.replace(old_items_append, new_items_append)
        print("Patched generic_smm.py get_services")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

def patch_tuongtaccheo_py():
    path = "backend/app/providers/tuongtaccheo.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    old_ttc_item = '''                        items.append(ProviderServiceItem(
                            service_id=str(s.get("service") or s.get("id")),
                            name=name_raw,
                            category=cat_raw,
                            platform=platform,
                            rate=float(s.get("rate") or s.get("price") or 1000.0),
                            min=int(s.get("min") or 10),
                            max=int(s.get("max") or 10000000),
                            refill=bool(s.get("refill", False)),
                            cancel=bool(s.get("cancel", False))
                        ))'''

    new_ttc_item = '''                        raw_xu = float(s.get("rate") or s.get("price") or 1000.0)
                        items.append(ProviderServiceItem(
                            service_id=str(s.get("service") or s.get("id")),
                            name=name_raw,
                            category=cat_raw,
                            platform=platform,
                            rate=raw_xu,
                            raw_rate=raw_xu,
                            currency="XU",
                            min=int(s.get("min") or 10),
                            max=int(s.get("max") or 10000000),
                            refill=bool(s.get("refill", False)),
                            cancel=bool(s.get("cancel", False))
                        ))'''

    if old_ttc_item in content:
        content = content.replace(old_ttc_item, new_ttc_item)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Patched tuongtaccheo.py get_services")

def patch_pricing_service_py():
    path = "backend/app/services/pricing_service.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    helper_code = '''
def convert_provider_rate_to_vnd(
    raw_rate: float,
    provider_type: str,
    currency: Optional[str] = None,
    usd_rate: float = 28000.0,
    ttc_divider: float = 100.0,
) -> float:
    """
    Quy doi gia goc tu Provider sang gia von VND cho 1.000 don vi dich vu.
    - TuongTacCheo (XU): Gia goc la Xu cho 1 don vi. 1.000 don vi = (raw_rate * 1000) / divider (VND).
    - USD (THMXH, GenericSMM USD): Gia goc la USD cho 1.000 don vi. Quy doi = raw_rate * usd_rate (VND).
    - VND: Gia goc da la VND cho 1.000 don vi. Giu nguyen.
    Tuyet doi khong dung heuristic phong doan gia < 500d de tranh nhan kep hoac nhan nham tien te.
    """
    p_type = (provider_type or "").lower().strip()
    curr = (currency or "").upper().strip()

    if p_type == "tuongtaccheo" or curr == "XU" or "ttc" in p_type:
        div = float(ttc_divider or 100.0)
        eff_div = div if div < 300 else (div / 10.0)
        vnd_cost = round((float(raw_rate) * 1000.0) / eff_div, 2)
        return max(vnd_cost, 100.0)

    if curr == "USD" or p_type == "thmxh":
        rate_val = float(raw_rate or 0.0)
        u_rate = float(usd_rate or 28000.0)
        return round(rate_val * u_rate, 2)

    return round(float(raw_rate or 0.0), 2)
'''

    if "def convert_provider_rate_to_vnd" not in content:
        content += helper_code
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        print("Patched pricing_service.py with convert_provider_rate_to_vnd")

def patch_admin_providers_py():
    path = "backend/app/routers/admin_providers.py"
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. format_provider_response
    old_fmt = '''    is_ttc = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
    resp.currency = "XU" if is_ttc else "VND"'''
    new_fmt = '''    is_ttc = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
    is_thmxh = (provider.provider_type or "").lower() == "thmxh" or "thmxh" in (provider.name or "").lower() or "thmxh" in (provider.base_url or "").lower()
    if is_ttc:
        resp.currency = "XU"
    elif is_thmxh:
        resp.currency = "USD"
    else:
        resp.currency = getattr(provider, "currency", None) or "VND"'''

    if old_fmt in content:
        content = content.replace(old_fmt, new_fmt)
        print("Patched admin_providers.py format_provider_response")

    # 2. admin_recalculate_thmxh_prices: fix the double conversion bug!
    old_recalc_block = '''    # Get live catalog from THMXH
    try:
        adapter = ProviderManager.get_provider(thmxh_provider)
        live_items = await adapter.get_services()
        live_rates = {str(it.service_id): float(it.rate) for it in live_items}
    except Exception as e:
        live_rates = {}

    # Load THMXH services
    srv_res = await db.execute(select(Service).where(Service.provider_id == thmxh_provider.id, Service.is_deleted == False))
    services = srv_res.scalars().all()

    map_res = await db.execute(select(ProviderServiceMapping).where(ProviderServiceMapping.provider_id == thmxh_provider.id))
    mappings = {m.service_id: m for m in map_res.scalars().all() if m.service_id}

    updated_count = 0
    for srv in services:
        ext_id = str(srv.external_service_id or "")
        m = mappings.get(srv.id)
        if ext_id in live_rates and live_rates[ext_id] > 0:
            raw_val = live_rates[ext_id]
        elif m and m.original_rate and m.original_rate > 0:
            raw_val = m.original_rate
        elif srv.provider_price and srv.provider_price > 0:
            raw_val = srv.provider_price
        else:
            raw_val = 0.05

        if raw_val < 500.0:
            raw_vnd = round(raw_val * usd_rate, 2)
        else:
            raw_vnd = raw_val'''

    new_recalc_block = '''    # Get live catalog from THMXH
    try:
        adapter = ProviderManager.get_provider(thmxh_provider)
        adapter.usd_rate = usd_rate
        live_items = await adapter.get_services()
        # live_items from THMXH adapter are already converted to VND using usd_rate!
        live_rates = {str(it.service_id): float(it.rate) for it in live_items}
        live_raw_usd = {str(it.service_id): float(getattr(it, "raw_rate", 0.0) or 0.0) for it in live_items}
    except Exception as e:
        live_rates = {}
        live_raw_usd = {}

    # Load THMXH services
    srv_res = await db.execute(select(Service).where(Service.provider_id == thmxh_provider.id, Service.is_deleted == False))
    services = srv_res.scalars().all()

    map_res = await db.execute(select(ProviderServiceMapping).where(ProviderServiceMapping.provider_id == thmxh_provider.id))
    mappings = {m.service_id: m for m in map_res.scalars().all() if m.service_id}

    updated_count = 0
    for srv in services:
        ext_id = str(srv.external_service_id or "")
        m = mappings.get(srv.id)

        # 1. Uu tien 1: Gia cap nhat truc tiep tu live API (Da quy doi ra VND boi adapter theo usd_rate)
        if ext_id in live_rates and live_rates[ext_id] > 0:
            raw_vnd = live_rates[ext_id]
            raw_usd = live_raw_usd.get(ext_id, round(raw_vnd / usd_rate, 6))
        # 2. Uu tien 2: Gia tu mapping da luu
        elif m and m.original_rate and m.original_rate > 0:
            if m.original_rate < 500.0:
                raw_usd = m.original_rate
                raw_vnd = round(raw_usd * usd_rate, 2)
            else:
                raw_vnd = m.original_rate
                raw_usd = round(raw_vnd / usd_rate, 6)
        # 3. Uu tien 3: provider_price da co trong DB (da la VND)
        elif srv.provider_price and srv.provider_price > 0:
            raw_vnd = srv.provider_price
            raw_usd = round(raw_vnd / usd_rate, 6)
        else:
            raw_usd = 0.05
            raw_vnd = round(raw_usd * usd_rate, 2)'''

    if old_recalc_block in content:
        content = content.replace(old_recalc_block, new_recalc_block)
        print("Patched admin_providers.py admin_recalculate_thmxh_prices block")

    # In mapping update of recalculate_thmxh_prices: save raw_usd to m.original_rate!
    old_m_orig = '''        if m:
            m.original_rate = raw_vnd'''
    new_m_orig = '''        if m:
            m.original_rate = raw_usd'''
    if old_m_orig in content:
        content = content.replace(old_m_orig, new_m_orig)
        print("Patched admin_providers.py m.original_rate to raw_usd")

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

if __name__ == "__main__":
    patch_base_py()
    patch_thmxh_py()
    patch_generic_smm_py()
    patch_tuongtaccheo_py()
    patch_pricing_service_py()
    patch_admin_providers_py()
    print("Code patches completed successfully!")