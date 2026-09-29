from pydantic import BaseModel, Field
from datetime import datetime, timedelta
import time
import random
import re
import unicodedata
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import (
    User, Service, Category, Provider, AuditLog,
    ProviderServiceMapping, PriceSyncLog, SystemSetting
)
from app.schemas.all import (
    ApiResponse, ProviderCreate, ProviderUpdate, ProviderResponse,
    ProviderTestConnectionRequest, ProviderTestConnectionResponse,
    ProviderCatalogItem, ImportServicesRequest, BulkMarkupPreviewRequest,
    ProviderMappingResponse, UpdateMappingRequest, PriceSyncLogResponse,
    TTCBoostRequest, TTCMultiStatusRequest, TTCMultiCancelRequest, TTCLoginRequest, ProviderOrderStatusRequest
)
from app.auth.security import require_role
from app.providers.manager import ProviderManager
from app.routers.services import invalidate_service_cache
from app.utils.crypto import encrypt_secret, decrypt_secret
from app.utils.security import is_safe_url
import logging
from app.services.pricing_service import (
    calculate_service_prices, generate_markup_previews, calculate_single_price
)

logger = logging.getLogger(__name__)


provider_router = APIRouter()

def sanitize_service_name(name: str) -> str:
    cleaned = re.sub(r'^(TTC|THMXH)\s*[-:]\s*', '', name or '', flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*\[(TTC|THMXH)\]', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*\((TTC|THMXH)\)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'(TTC|THMXH)', '', cleaned, flags=re.IGNORECASE)
    return re.sub(r'\s{2,}', ' ', cleaned).strip()

def sanitize_service_description(desc: str = None) -> str:
    if not desc or "tự động import" in desc.lower() or "dịch vụ chuẩn từ" in desc.lower() or "thmxh" in desc.lower() or "tuongtaccheo" in desc.lower() or "ttc" in desc.lower():
        return ""
    cleaned = re.sub(r'https?://\S+', '', desc)
    cleaned = re.sub(r'(TuongTacCheo|TTC|THMXH|thmxh\.com|Provider)', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s{2,}', ' ', cleaned).strip()
    return cleaned if len(cleaned) >= 15 else ""


def format_provider_response(provider: Provider, services_cnt: int = 0, mappings_cnt: int = 0) -> ProviderResponse:
    raw_key = decrypt_secret(provider.api_key_encrypted) if provider.api_key_encrypted else ""
    if raw_key and len(raw_key) > 4:
        masked_key = f"••••••••••••{raw_key[-4:]}"
    elif raw_key:
        masked_key = "••••••••"
    else:
        masked_key = None

    resp = ProviderResponse.model_validate(provider)
    resp.api_key_masked = masked_key
    resp.services_count = services_cnt
    resp.mappings_count = mappings_cnt
    is_ttc = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
    is_thmxh = (provider.provider_type or "").lower() == "thmxh" or "thmxh" in (provider.name or "").lower() or "thmxh" in (provider.base_url or "").lower()
    if is_ttc:
        resp.currency = "XU"
    elif is_thmxh:
        resp.currency = "USD"
    else:
        resp.currency = getattr(provider, "currency", None) or "VND"
    return resp

@provider_router.get("/providers", response_model=ApiResponse[List[ProviderResponse]])
async def admin_list_providers(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Provider).order_by(Provider.id))
    providers = res.scalars().all()
    results = []
    for p in providers:
        s_cnt_res = await db.execute(
            select(func.count(Service.id)).where(Service.provider_id == p.id, Service.is_deleted == False)
        )
        s_cnt = s_cnt_res.scalar() or 0
        m_cnt_res = await db.execute(
            select(func.count(ProviderServiceMapping.id)).where(ProviderServiceMapping.provider_id == p.id)
        )
        m_cnt = m_cnt_res.scalar() or 0
        results.append(format_provider_response(p, s_cnt, m_cnt))
    return ApiResponse(data=results)

@provider_router.post("/providers", response_model=ApiResponse[ProviderResponse])
async def admin_create_provider(
    payload: ProviderCreate,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    if not is_safe_url(payload.base_url, allow_local_in_dev=True):
        raise HTTPException(status_code=400, detail="URL nhà cung cấp không an toàn hoặc trỏ tới mạng nội bộ (SSRF detected).")

    provider = Provider(
        name=payload.name,
        provider_type=payload.provider_type or "generic_smm",
        base_url=payload.base_url.rstrip("/"),
        api_key_encrypted=encrypt_secret(payload.api_key.strip()),
        status="ACTIVE",
        balance=0.0,
        created_at=datetime.utcnow()
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)

    try:
        adapter = ProviderManager.get_provider(provider)
        bal = await adapter.get_balance()
        provider.balance = bal
        provider.last_sync = datetime.utcnow()
        await db.commit()
        await db.refresh(provider)
    except Exception:
        pass

    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="CREATE_PROVIDER",
        target_type="PROVIDER",
        target_id=str(provider.id),
        details=f"Tạo nhà cung cấp {provider.name} ({provider.provider_type})"
    )
    db.add(audit)
    await db.commit()
    return ApiResponse(data=format_provider_response(provider), message="Thêm nhà cung cấp thành công.")

@provider_router.post("/providers/test-connection", response_model=ApiResponse[ProviderTestConnectionResponse])
async def admin_test_connection_transient(payload: ProviderTestConnectionRequest):
    if not is_safe_url(payload.base_url, allow_local_in_dev=True):
        return ApiResponse(data=ProviderTestConnectionResponse(
            success=False,
            error="API URL không an toàn hoặc trỏ tới mạng nội bộ (SSRF detected)."
        ))

    adapter = ProviderManager.create_transient_provider(
        base_url=payload.base_url,
        api_key=payload.api_key.strip(),
        provider_type=payload.provider_type or "generic_smm"
    )

    try:
        if hasattr(adapter, "test_connection"):
            res = await adapter.test_connection()
            return ApiResponse(data=ProviderTestConnectionResponse(
                success=res.get("success", False),
                balance=res.get("balance"),
                currency=res.get("currency", "USD"),
                error=res.get("error")
            ))
        else:
            bal = await adapter.get_balance()
            return ApiResponse(data=ProviderTestConnectionResponse(
                success=True,
                balance=bal,
                currency="USD"
            ))
    except Exception as e:
        return ApiResponse(data=ProviderTestConnectionResponse(
            success=False,
            error=f"Không thể kết nối đến nhà cung cấp: {str(e)}"
        ))

@provider_router.post("/providers/{id}/test-connection", response_model=ApiResponse[ProviderTestConnectionResponse])
async def admin_test_connection_saved(id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")

    ProviderManager.clear_cache(id)
    adapter = ProviderManager.get_provider(provider)

    is_ttc = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
    default_curr = "XU" if is_ttc else "VND"

    try:
        if hasattr(adapter, "test_connection"):
            conn_res = await adapter.test_connection()
            if conn_res.get("success"):
                provider.balance = float(conn_res.get("balance", 0.0))
                provider.last_sync = datetime.utcnow()
                await db.commit()
            return ApiResponse(data=ProviderTestConnectionResponse(
                success=conn_res.get("success", False),
                balance=conn_res.get("balance"),
                currency=conn_res.get("currency", default_curr),
                error=conn_res.get("error")
            ))
        else:
            bal = await adapter.get_balance()
            provider.balance = bal
            provider.last_sync = datetime.utcnow()
            await db.commit()
            return ApiResponse(data=ProviderTestConnectionResponse(
                success=True,
                balance=bal,
                currency=default_curr
            ))
    except Exception as e:
        return ApiResponse(data=ProviderTestConnectionResponse(
            success=False,
            error=f"Không thể kết nối đến nhà cung cấp: {str(e)}"
        ))

@provider_router.get("/providers/{id}/catalog", response_model=ApiResponse[List[ProviderCatalogItem]])
async def admin_get_provider_catalog(id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")

    adapter = ProviderManager.get_provider(provider)
    services_data = await adapter.get_services()

    m_res = await db.execute(
        select(ProviderServiceMapping).where(ProviderServiceMapping.provider_id == id)
    )
    existing_mappings = {str(m.external_service_id): m for m in m_res.scalars().all()}

    s_res = await db.execute(
        select(Service).where(Service.provider_id == id, Service.is_deleted == False)
    )
    existing_services = {str(s.external_service_id): s for s in s_res.scalars().all() if s.external_service_id}

    items: List[ProviderCatalogItem] = []
    for s in services_data:
        ext_id = str(s.service_id)
        m = existing_mappings.get(ext_id)
        srv = existing_services.get(ext_id)

        is_imported = bool(m is not None or srv is not None)
        local_id = srv.id if srv else (m.service_id if m else None)
        local_price = srv.price if srv else (m.selling_price if m else None)
        local_dealer = getattr(srv, "dealer_price", 0.0) if srv else (m.dealer_price if m else None)

        items.append(ProviderCatalogItem(
            service_id=ext_id,
            name=s.name,
            type=getattr(s, "type", "Default") or "Default",
            category=s.category or "General",
            platform=s.platform or "Facebook",
            rate=float(s.rate),
            min=int(s.min),
            max=int(s.max),
            refill=bool(s.refill),
            cancel=bool(s.cancel),
            is_imported=is_imported,
            local_service_id=local_id,
            local_price=local_price,
            local_dealer_price=local_dealer
        ))

    return ApiResponse(data=items)

@provider_router.post("/providers/preview-markup", response_model=ApiResponse[List[dict]])
async def admin_preview_bulk_markup(payload: BulkMarkupPreviewRequest):
    previews = generate_markup_previews(
        items=payload.items,
        user_markup_type=payload.markup_type,
        user_markup_value=payload.markup_value,
        dealer_markup_type=payload.dealer_markup_type,
        dealer_markup_value=payload.dealer_markup_value,
        auto_round=payload.auto_round,
        round_type=payload.round_type,
        round_unit=payload.round_unit
    )
    return ApiResponse(data=previews)
@provider_router.post("/providers/{id}/import-services", response_model=ApiResponse[dict])
async def admin_import_provider_services(
    id: int,
    payload: ImportServicesRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")

    if not payload.services:
        raise HTTPException(status_code=400, detail="Vui lòng chọn ít nhất 1 dịch vụ để import.")

    cat_res = await db.execute(select(Category))
    categories = {c.name.strip().lower(): c for c in cat_res.scalars().all()}
    cat_by_id = {c.id: c for c in categories.values()}

    imported_count = 0
    updated_count = 0

    for item in payload.services:
        cat_id = None
        if item.target_category_id and item.target_category_id in cat_by_id:
            cat_id = item.target_category_id
        else:
            cat_name = (item.category or "Mạng Xã Hội").strip()
            cat_key = cat_name.lower()
            if cat_key in categories:
                cat_id = categories[cat_key].id
            else:
                clean_slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", f"{item.platform or 'smm'}-{cat_name}").encode("ascii", "ignore").decode("utf-8").lower()).strip("-")
                if not clean_slug:
                    clean_slug = f"cat-{int(datetime.utcnow().timestamp())}"
                slug_cand = clean_slug[:60].strip("-")
                slug_suf = 1
                while any(c.slug == slug_cand for c in cat_by_id.values()):
                    slug_cand = f"{clean_slug[:50]}-{slug_suf}"
                    slug_suf += 1
                new_cat = Category(
                    name=cat_name,
                    slug=slug_cand,
                    platform=item.platform or "Facebook",
                    icon="Layers",
                    sort_order=0,
                    created_at=datetime.utcnow()
                )
                db.add(new_cat)
                await db.flush()
                categories[cat_key] = new_cat
                cat_by_id[new_cat.id] = new_cat
                cat_id = new_cat.id

        map_res = await db.execute(select(ProviderServiceMapping).where(
            ProviderServiceMapping.provider_id == provider.id,
            ProviderServiceMapping.external_service_id == str(item.external_service_id)
        ))
        existing_map = map_res.scalar_one_or_none()

        srv_obj = None
        if existing_map and existing_map.service_id:
            s_res = await db.execute(select(Service).where(Service.id == existing_map.service_id))
            srv_obj = s_res.scalar_one_or_none()

        if not srv_obj:
            s_res = await db.execute(select(Service).where(
                Service.provider_id == provider.id,
                Service.external_service_id == str(item.external_service_id),
                Service.is_deleted == False
            ))
            srv_obj = s_res.scalar_one_or_none()

        if srv_obj:
            srv_obj.name = item.name
            srv_obj.category_id = cat_id
            srv_obj.provider_price = item.rate
            srv_obj.price = item.selling_price
            srv_obj.dealer_price = item.dealer_price
            srv_obj.min_quantity = item.min
            srv_obj.max_quantity = item.max
            srv_obj.refill_enabled = item.refill
            srv_obj.cancel_enabled = item.cancel
            srv_obj.status = "ACTIVE"
            srv_obj.updated_at = datetime.utcnow()

            if existing_map:
                existing_map.external_name = item.name
                existing_map.external_category = item.category
                existing_map.original_rate = item.rate
                existing_map.markup_type = item.markup_type
                existing_map.markup_value = item.markup_value
                existing_map.dealer_markup_type = item.dealer_markup_type
                existing_map.dealer_markup_value = item.dealer_markup_value
                existing_map.auto_round = item.auto_round
                existing_map.round_type = item.round_type
                existing_map.round_unit = item.round_unit
                existing_map.selling_price = item.selling_price
                existing_map.dealer_price = item.dealer_price
                existing_map.min_quantity = item.min
                existing_map.max_quantity = item.max
                existing_map.refill_enabled = item.refill
                existing_map.cancel_enabled = item.cancel
                existing_map.sync_status = "ACTIVE"
                existing_map.last_synced_at = datetime.utcnow()
            else:
                new_map = ProviderServiceMapping(
                    provider_id=provider.id,
                    service_id=srv_obj.id,
                    external_service_id=str(item.external_service_id),
                    external_name=sanitize_service_name(item.name),
                    external_category=item.category,
                    original_rate=item.rate,
                    markup_type=item.markup_type,
                    markup_value=item.markup_value,
                    dealer_markup_type=item.dealer_markup_type,
                    dealer_markup_value=item.dealer_markup_value,
                    auto_round=item.auto_round,
                    round_type=item.round_type,
                    round_unit=item.round_unit,
                    selling_price=item.selling_price,
                    dealer_price=item.dealer_price,
                    min_quantity=item.min,
                    max_quantity=item.max,
                    refill_enabled=item.refill,
                    cancel_enabled=item.cancel,
                    sync_status="ACTIVE",
                    last_synced_at=datetime.utcnow(),
                    created_at=datetime.utcnow()
                )
                db.add(new_map)
            updated_count += 1
        else:
            new_srv = Service(
                provider_id=provider.id,
                category_id=cat_id,
                external_service_id=str(item.external_service_id),
                name=sanitize_service_name(item.name),
                description="Dịch vụ tăng tương tác chất lượng cao, tự động xử lý 24/7.",
                platform=item.platform or "Facebook",
                service_type=item.type or "default",
                price=item.selling_price,
                dealer_price=item.dealer_price,
                provider_price=item.rate,
                min_quantity=item.min,
                max_quantity=item.max,
                refill_enabled=item.refill,
                cancel_enabled=item.cancel,
                status="ACTIVE",
                created_at=datetime.utcnow()
            )
            db.add(new_srv)
            await db.flush()

            new_map = ProviderServiceMapping(
                provider_id=provider.id,
                service_id=new_srv.id,
                external_service_id=str(item.external_service_id),
                external_name=sanitize_service_name(item.name),
                external_category=item.category,
                original_rate=item.rate,
                markup_type=item.markup_type,
                markup_value=item.markup_value,
                dealer_markup_type=item.dealer_markup_type,
                dealer_markup_value=item.dealer_markup_value,
                auto_round=item.auto_round,
                round_type=item.round_type,
                round_unit=item.round_unit,
                selling_price=item.selling_price,
                dealer_price=item.dealer_price,
                min_quantity=item.min,
                max_quantity=item.max,
                refill_enabled=item.refill,
                cancel_enabled=item.cancel,
                sync_status="ACTIVE",
                last_synced_at=datetime.utcnow(),
                created_at=datetime.utcnow()
            )
            db.add(new_map)
            srv_obj = new_srv
            imported_count += 1

        log_entry = PriceSyncLog(
            provider_id=provider.id,
            service_id=srv_obj.id if srv_obj else None,
            external_service_id=str(item.external_service_id),
            service_name=sanitize_service_name(item.name),
            old_rate=item.rate,
            new_rate=item.rate,
            old_price=item.selling_price,
            new_price=item.selling_price,
            change_percent=0.0,
            alert_triggered=False,
            status="IMPORTED",
            notes=f"Đã import từ {provider.name} (Markup {item.markup_value}%)",
            created_at=datetime.utcnow()
        )
        db.add(log_entry)

    invalidate_service_cache()
    await db.commit()

    return ApiResponse(
        data={"imported_count": imported_count, "updated_count": updated_count},
        message=f"Đã xử lý {imported_count + updated_count} dịch vụ: Thêm mới {imported_count}, Cập nhật {updated_count} thành công!"
    )

@provider_router.get("/mappings", response_model=ApiResponse[List[ProviderMappingResponse]])
async def admin_list_mappings(
    provider_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    sync_status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    query = select(ProviderServiceMapping, Provider.name, Service.name).join(
        Provider, Provider.id == ProviderServiceMapping.provider_id
    ).outerjoin(
        Service, Service.id == ProviderServiceMapping.service_id
    )

    if provider_id:
        query = query.where(ProviderServiceMapping.provider_id == provider_id)
    if sync_status:
        query = query.where(ProviderServiceMapping.sync_status == sync_status)

    res = await db.execute(query.order_by(desc(ProviderServiceMapping.id)))
    rows = res.all()

    items = []
    for mapping, p_name, s_name in rows:
        if search:
            s_low = search.lower()
            text_haystack = f"{mapping.external_service_id} {mapping.external_name or ''} {s_name or ''}".lower()
            if s_low not in text_haystack:
                continue

        resp = ProviderMappingResponse.model_validate(mapping)
        resp.provider_name = p_name
        resp.service_name = s_name or mapping.external_name
        items.append(resp)

    return ApiResponse(data=items)

@provider_router.put("/mappings/{id}", response_model=ApiResponse[ProviderMappingResponse])
async def admin_update_mapping(
    id: int,
    payload: UpdateMappingRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(ProviderServiceMapping).where(ProviderServiceMapping.id == id))
    mapping = res.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="Không tìm thấy mapping dịch vụ.")

    if payload.markup_type is not None:
        mapping.markup_type = payload.markup_type
    if payload.markup_value is not None:
        mapping.markup_value = payload.markup_value
    if payload.dealer_markup_type is not None:
        mapping.dealer_markup_type = payload.dealer_markup_type
    if payload.dealer_markup_value is not None:
        mapping.dealer_markup_value = payload.dealer_markup_value
    if payload.auto_round is not None:
        mapping.auto_round = payload.auto_round
    if payload.round_type is not None:
        mapping.round_type = payload.round_type
    if payload.round_unit is not None:
        mapping.round_unit = payload.round_unit

    prices = calculate_service_prices(
        rate=mapping.original_rate,
        user_markup_type=mapping.markup_type,
        user_markup_value=mapping.markup_value,
        dealer_markup_type=mapping.dealer_markup_type,
        dealer_markup_value=mapping.dealer_markup_value,
        auto_round=mapping.auto_round,
        round_type=mapping.round_type,
        round_unit=mapping.round_unit
    )

    mapping.selling_price = payload.selling_price if payload.selling_price is not None else prices["selling_price"]
    mapping.dealer_price = payload.dealer_price if payload.dealer_price is not None else prices["dealer_price"]

    if payload.min_quantity is not None:
        mapping.min_quantity = payload.min_quantity
    if payload.max_quantity is not None:
        mapping.max_quantity = payload.max_quantity
    if payload.refill_enabled is not None:
        mapping.refill_enabled = payload.refill_enabled
    if payload.cancel_enabled is not None:
        mapping.cancel_enabled = payload.cancel_enabled
    if payload.sync_status is not None:
        mapping.sync_status = payload.sync_status

    mapping.updated_at = datetime.utcnow()

    if mapping.service_id:
        s_res = await db.execute(select(Service).where(Service.id == mapping.service_id))
        srv = s_res.scalar_one_or_none()
        if srv:
            srv.price = mapping.selling_price
            srv.dealer_price = mapping.dealer_price
            srv.min_quantity = mapping.min_quantity
            srv.max_quantity = mapping.max_quantity
            srv.refill_enabled = mapping.refill_enabled
            srv.cancel_enabled = mapping.cancel_enabled
            srv.updated_at = datetime.utcnow()

    invalidate_service_cache()
    await db.commit()
    await db.refresh(mapping)

    resp = ProviderMappingResponse.model_validate(mapping)
    return ApiResponse(data=resp, message="Đã cập nhật cấu hình mapping thành công.")

@provider_router.delete("/mappings/{id}", response_model=ApiResponse[bool])
async def admin_delete_mapping(
    id: int,
    unlink_service: bool = Query(True),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(ProviderServiceMapping).where(ProviderServiceMapping.id == id))
    mapping = res.scalar_one_or_none()
    if not mapping:
        raise HTTPException(status_code=404, detail="Không tìm thấy mapping dịch vụ.")

    if unlink_service and mapping.service_id:
        s_res = await db.execute(select(Service).where(Service.id == mapping.service_id))
        srv = s_res.scalar_one_or_none()
        if srv:
            srv.provider_id = None
            srv.external_service_id = None
            srv.updated_at = datetime.utcnow()

    await db.delete(mapping)
    invalidate_service_cache()
    await db.commit()
    return ApiResponse(data=True, message="Đã gỡ liên kết mapping dịch vụ thành công.")

@provider_router.post("/providers/{id}/sync-mappings", response_model=ApiResponse[dict])
async def admin_sync_provider_mappings(
    id: int,
    alert_threshold_percent: float = Query(15.0),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")

    ProviderManager.clear_cache(id)
    adapter = ProviderManager.get_provider(provider)
    services_data = await adapter.get_services()
    bal = await adapter.get_balance()
    provider.balance = bal
    provider.last_sync = datetime.utcnow()

    catalog_map = {str(item.service_id): item for item in services_data}

    m_res = await db.execute(
        select(ProviderServiceMapping).where(ProviderServiceMapping.provider_id == id)
    )
    mappings = m_res.scalars().all()

    rates_changed = 0
    disabled_count = 0
    warning_count = 0

    for m in mappings:
        item = catalog_map.get(str(m.external_service_id))
        srv = None
        if m.service_id:
            s_res = await db.execute(select(Service).where(Service.id == m.service_id))
            srv = s_res.scalar_one_or_none()

        if not item:
            if m.sync_status != "PROVIDER_DISABLED":
                m.sync_status = "PROVIDER_DISABLED"
                disabled_count += 1
                if srv:
                    srv.status = "INACTIVE"
                log = PriceSyncLog(
                    provider_id=provider.id,
                    service_id=m.service_id,
                    external_service_id=m.external_service_id,
                    service_name=m.external_name or (srv.name if srv else "N/A"),
                    old_rate=m.original_rate,
                    new_rate=m.original_rate,
                    old_price=m.selling_price,
                    new_price=m.selling_price,
                    change_percent=0.0,
                    alert_triggered=True,
                    status="DISABLED",
                    notes="Nhà cung cấp đã gỡ hoặc ngưng dịch vụ này.",
                    created_at=datetime.utcnow()
                )
                db.add(log)
            continue

        if m.sync_status == "PROVIDER_DISABLED":
            m.sync_status = "ACTIVE"
            if srv and srv.status == "INACTIVE":
                srv.status = "ACTIVE"

        old_rate = m.original_rate
        new_rate = float(item.rate)
        old_selling = m.selling_price

        m.min_quantity = int(item.min)
        m.max_quantity = int(item.max)
        m.refill_enabled = bool(item.refill)
        m.cancel_enabled = bool(item.cancel)
        if srv:
            srv.min_quantity = int(item.min)
            srv.max_quantity = int(item.max)
            srv.refill_enabled = bool(item.refill)
            srv.cancel_enabled = bool(item.cancel)

        if abs(new_rate - old_rate) > 1e-4:
            rates_changed += 1
            change_pct = (abs(new_rate - old_rate) / old_rate * 100.0) if old_rate > 0 else 0.0
            is_warning = change_pct >= alert_threshold_percent
            if is_warning:
                warning_count += 1

            is_ttc_p = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
            is_thmxh_p = (provider.provider_type or "").lower() == "thmxh" or "thmxh" in (provider.name or "").lower()
            if is_ttc_p:
                ttc_div_res = await db.execute(select(SystemSetting).where(SystemSetting.key == "ttc_rate_divider"))
                ttc_div_row = ttc_div_res.scalar_one_or_none()
                try:
                    ttc_div = float(ttc_div_row.value if ttc_div_row else 100.0)
                except Exception:
                    ttc_div = 100.0
                effective_div = ttc_div if ttc_div < 300 else (ttc_div / 10.0)
                effective_calc_rate = round((new_rate * 1000.0) / effective_div, 2)
                if effective_calc_rate < 100.0:
                    effective_calc_rate = max(effective_calc_rate, 100.0)
            elif is_thmxh_p:
                # THMXH adapter already converted USD rates to VND in get_services()
                effective_calc_rate = new_rate
            else:
                effective_calc_rate = new_rate

            prices = calculate_service_prices(
                rate=effective_calc_rate,
                user_markup_type=m.markup_type,
                user_markup_value=m.markup_value,
                dealer_markup_type=m.dealer_markup_type,
                dealer_markup_value=m.dealer_markup_value,
                auto_round=m.auto_round,
                round_type=m.round_type,
                round_unit=m.round_unit
            )

            m.original_rate = new_rate
            m.selling_price = prices["selling_price"]
            m.dealer_price = prices["dealer_price"]
            m.sync_status = "PRICE_CHANGED" if is_warning else "ACTIVE"

            if srv:
                srv.provider_price = effective_calc_rate
                srv.price = prices["selling_price"]
                srv.dealer_price = prices["dealer_price"]
                srv.updated_at = datetime.utcnow()

            direction = "Tăng" if new_rate > old_rate else "Giảm"
            notes = f"Giá gốc {direction} {change_pct:.1f}% ({old_rate:,.2f} -> {new_rate:,.2f})"
            if is_warning:
                notes += f" [CẢNH BÁO BIẾN ĐỘNG > {alert_threshold_percent:.0f}%]"

            log = PriceSyncLog(
                provider_id=provider.id,
                service_id=m.service_id,
                external_service_id=m.external_service_id,
                service_name=m.external_name or (srv.name if srv else item.name),
                old_rate=old_rate,
                new_rate=new_rate,
                old_price=old_selling,
                new_price=prices["selling_price"],
                change_percent=round(change_pct, 2),
                alert_triggered=is_warning,
                status="WARNING_PRICE_SPIKE" if is_warning else "UPDATED",
                notes=notes,
                created_at=datetime.utcnow()
            )
            db.add(log)

        m.last_synced_at = datetime.utcnow()

    invalidate_service_cache()
    await db.commit()

    return ApiResponse(
        data={
            "total_mapped": len(mappings),
            "rates_changed": rates_changed,
            "disabled_count": disabled_count,
            "warning_count": warning_count,
            "balance": bal
        },
        message=f"Đồng bộ thành công! {rates_changed} dịch vụ đổi giá, {disabled_count} dịch vụ bị ngưng, {warning_count} cảnh báo biến động giá."
    )

@provider_router.get("/price-sync-logs", response_model=ApiResponse[List[PriceSyncLogResponse]])
async def admin_list_price_sync_logs(
    provider_id: Optional[int] = Query(None),
    alert_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
):
    query = select(PriceSyncLog, Provider.name).join(
        Provider, Provider.id == PriceSyncLog.provider_id
    )

    if provider_id:
        query = query.where(PriceSyncLog.provider_id == provider_id)
    if alert_only:
        query = query.where(PriceSyncLog.alert_triggered == True)

    res = await db.execute(query.order_by(desc(PriceSyncLog.id)).limit(limit))
    rows = res.all()

    logs = []
    for log, p_name in rows:
        resp = PriceSyncLogResponse.model_validate(log)
        resp.provider_name = p_name
        logs.append(resp)

    return ApiResponse(data=logs)

@provider_router.post("/providers/{id}/sync", response_model=ApiResponse[dict])
async def admin_sync_provider_compat(id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")

    ProviderManager.clear_cache(id)
    adapter = ProviderManager.get_provider(provider)
    services_data = await adapter.get_services()
    bal = await adapter.get_balance()
    provider.balance = bal
    provider.last_sync = datetime.utcnow()

    # Load system settings for auto-round and markups
    from app.models.all import SystemSetting
    res_set = await db.execute(select(SystemSetting).where(
        SystemSetting.key.in_(["ttc_rate_divider", "ttc_markup_percent", "thmxh_usd_rate", "thmxh_markup_percent", "thmxh_dealer_markup_percent", "auto_round", "round_type", "round_unit", "default_markup_percent"])
    ))
    s_map = {s.key: s.value for s in res_set.scalars().all()}
    auto_round = s_map.get("auto_round", "true").lower() != "false"
    round_type = s_map.get("round_type", "nearest")
    try:
        round_unit = float(s_map.get("round_unit", "10") or 10)
    except Exception:
        round_unit = 10.0

    # Load categories for dynamic category mapping
    cat_res = await db.execute(select(Category))
    categories_by_name = {c.name.strip().lower(): c for c in cat_res.scalars().all()}
    categories_by_id = {c.id: c for c in categories_by_name.values()}

    synced_count = 0
    for item in services_data:
        # 1. Resolve or create Category
        raw_cat = (item.category or "Mạng Xã Hội").strip()
        cat_key = raw_cat.lower()
        if cat_key in categories_by_name:
            target_cat = categories_by_name[cat_key]
        else:
            import unicodedata
            clean_slug = re.sub(r"[^a-z0-9]+", "-", unicodedata.normalize("NFKD", f"{item.platform or 'smm'}-{raw_cat}").encode("ascii", "ignore").decode("utf-8").lower()).strip("-")[:55]
            if not clean_slug:
                clean_slug = f"cat-{int(time.time())}-{random.randint(100, 999)}"
            slug_cand = clean_slug
            suf = 1
            while any(c.slug == slug_cand for c in categories_by_id.values()):
                slug_cand = f"{clean_slug[:45]}-{suf}"
                suf += 1
            target_cat = Category(
                name=raw_cat,
                slug=slug_cand,
                platform=item.platform or "Facebook",
                icon="Layers",
                status="ACTIVE",
                sort_order=10,
                created_at=datetime.utcnow()
            )
            db.add(target_cat)
            await db.flush()
            categories_by_name[cat_key] = target_cat
            categories_by_id[target_cat.id] = target_cat

        cat_id = target_cat.id

        # 2. Calculate provider price and selling price
        is_ttc = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
        is_thmxh = (provider.provider_type or "").lower() == "thmxh" or "thmxh" in (provider.name or "").lower()

        if is_ttc:
            try:
                ttc_div = float(s_map.get("ttc_rate_divider", "1000") or 1000)
            except Exception:
                ttc_div = 1000.0
            if ttc_div <= 0:
                ttc_div = 1000.0
            try:
                ttc_mk = float(s_map.get("ttc_markup_percent", "30") or 30)
            except Exception:
                ttc_mk = 30.0

            # TTC rate is in Xu for 1 unit.
            # In SMM Panel, prices are PER 1,000 UNITS.
            # 1.000.000 Xu = 10.000 VNĐ -> 1 Xu = 1/100 VNĐ (tỷ lệ 100 Xu = 1 VNĐ).
            # Giá vốn 1.000 lượt = (item.rate * 1000.0) / effective_divider.
            effective_div = ttc_div if ttc_div < 300 else (ttc_div / 10.0)
            provider_price_val = round((item.rate * 1000.0) / effective_div, 2)
            if provider_price_val < 100.0:
                provider_price_val = max(provider_price_val, 100.0)
            selling_price = calculate_single_price(
                rate=provider_price_val,
                markup_type="PERCENT",
                markup_value=ttc_mk,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit
            )
            dealer_price = calculate_single_price(
                rate=provider_price_val,
                markup_type="PERCENT",
                markup_value=ttc_mk * 0.5,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit
            )
        elif is_thmxh:
            # item.rate is already converted to VND by THMXH adapter
            provider_price_val = round(item.rate, 2)
            if provider_price_val <= 0:
                provider_price_val = 1.0
            try:
                thmxh_mk = float(s_map.get("thmxh_markup_percent", "30") or 30)
            except Exception:
                thmxh_mk = 30.0
            try:
                thmxh_dealer_mk = float(s_map.get("thmxh_dealer_markup_percent", "15") or 15)
            except Exception:
                thmxh_dealer_mk = 15.0

            selling_price = calculate_single_price(
                rate=provider_price_val,
                markup_type="PERCENT",
                markup_value=thmxh_mk,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit
            )
            dealer_price = calculate_single_price(
                rate=provider_price_val,
                markup_type="PERCENT",
                markup_value=thmxh_dealer_mk,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit
            )
        else:
            provider_price_val = item.rate
            selling_price = calculate_single_price(
                rate=provider_price_val,
                markup_type="PERCENT",
                markup_value=30.0,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit
            )
            dealer_price = calculate_single_price(
                rate=provider_price_val,
                markup_type="PERCENT",
                markup_value=15.0,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit
            )

        # 3. Create or update Service
        s_res = await db.execute(select(Service).where(
            Service.provider_id == provider.id,
            Service.external_service_id == str(item.service_id),
            Service.is_deleted == False
        ))
        existing_srv = s_res.scalar_one_or_none()

        if existing_srv:
            existing_srv.name = item.name
            existing_srv.category_id = cat_id
            existing_srv.platform = item.platform or target_cat.platform or "Facebook"
            existing_srv.provider_price = provider_price_val
            existing_srv.price = selling_price
            existing_srv.dealer_price = dealer_price
            existing_srv.min_quantity = item.min
            existing_srv.max_quantity = item.max
            existing_srv.refill_enabled = item.refill
            existing_srv.cancel_enabled = item.cancel
            srv_obj = existing_srv
        else:
            new_srv = Service(
                provider_id=provider.id,
                category_id=cat_id,
                external_service_id=str(item.service_id),
                name=sanitize_service_name(item.name),
                description=f"Dịch vụ từ {provider.name} - {target_cat.name}",
                platform=item.platform or target_cat.platform or "Facebook",
                service_type="default",
                price=selling_price,
                dealer_price=dealer_price,
                provider_price=provider_price_val,
                min_quantity=item.min,
                max_quantity=item.max,
                refill_enabled=item.refill,
                cancel_enabled=item.cancel,
                status="ACTIVE",
                created_at=datetime.utcnow()
            )
            db.add(new_srv)
            await db.flush()
            srv_obj = new_srv

        # 4. Ensure ProviderServiceMapping
        m_res = await db.execute(select(ProviderServiceMapping).where(
            ProviderServiceMapping.provider_id == provider.id,
            ProviderServiceMapping.external_service_id == str(item.service_id)
        ))
        m = m_res.scalar_one_or_none()
        if not m:
            m = ProviderServiceMapping(
                provider_id=provider.id,
                service_id=srv_obj.id,
                external_service_id=str(item.service_id),
                external_name=sanitize_service_name(item.name),
                original_rate=provider_price_val,
                markup_type="PERCENT",
                markup_value=30.0,
                dealer_markup_type="PERCENT",
                dealer_markup_value=15.0,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit,
                selling_price=selling_price,
                dealer_price=dealer_price,
                min_quantity=item.min,
                max_quantity=item.max,
                refill_enabled=item.refill,
                cancel_enabled=item.cancel,
                sync_status="ACTIVE",
                last_synced_at=datetime.utcnow()
            )
            db.add(m)
        else:
            m.service_id = srv_obj.id
            m.external_name = item.name
            m.original_rate = provider_price_val
            m.selling_price = selling_price
            m.dealer_price = dealer_price
            m.last_synced_at = datetime.utcnow()

        synced_count += 1

    invalidate_service_cache()
    await db.commit()

    return ApiResponse(
        data={"synced": synced_count, "balance": bal},
        message=f"Đã đồng bộ {synced_count} dịch vụ từ {provider.name} thành công!"
    )

class TtcPriceRecalculateRequest(BaseModel):
    rate_divider: Optional[float] = Field(default=1000.0, description="Tỷ lệ quy đổi: số Xu TTC tương đương 1 VNĐ (Mặc định 1000 xu = 1đ)")
    markup_percent: Optional[float] = Field(default=30.0, description="Tỷ lệ tăng giá bán so với giá vốn (%)")

class ThmxhPriceRecalculateRequest(BaseModel):
    usd_rate: Optional[float] = Field(default=28000.0, description="Tỷ giá USD sang VNĐ cho THMXH (Mặc định 28.000đ)")
    markup_percent: Optional[float] = Field(default=30.0, description="Tỷ lệ tăng giá bán lẻ so với giá gốc (%)")
    dealer_markup_percent: Optional[float] = Field(default=15.0, description="Tỷ lệ tăng giá đại lý (%)")
    auto_round: Optional[bool] = Field(default=True, description="Tự động làm tròn giá")
    round_type: Optional[str] = Field(default="nearest", description="Kiểu làm tròn: nearest, ceil, floor")
    round_unit: Optional[float] = Field(default=10.0, description="Đơn vị làm tròn (1, 10, 50, 100, 1000)")

@provider_router.get("/providers/thmxh-config", response_model=ApiResponse[dict])
async def admin_get_thmxh_config(
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    from app.models.all import SystemSetting
    res = await db.execute(select(SystemSetting).where(
        SystemSetting.key.in_(["thmxh_usd_rate", "thmxh_markup_percent", "thmxh_dealer_markup_percent", "auto_round", "round_type", "round_unit", "provider_auto_sync_interval"])
    ))
    settings_dict = {s.key: s.value for s in res.scalars().all()}
    try:
        usd_rate = float(settings_dict.get("thmxh_usd_rate", "28000") or 28000)
    except Exception:
        usd_rate = 28000.0
    try:
        markup = float(settings_dict.get("thmxh_markup_percent", "30") or 30)
    except Exception:
        markup = 30.0
    try:
        dealer_markup = float(settings_dict.get("thmxh_dealer_markup_percent", "15") or 15)
    except Exception:
        dealer_markup = 15.0

    auto_round = settings_dict.get("auto_round", "true").lower() != "false"
    round_type = settings_dict.get("round_type", "nearest")
    try:
        round_unit = float(settings_dict.get("round_unit", "10") or 10)
    except Exception:
        round_unit = 10.0
    try:
        sync_interval = int(settings_dict.get("provider_auto_sync_interval", "4") or 4)
    except Exception:
        sync_interval = 4

    res_p = await db.execute(select(Provider).where(
        (Provider.provider_type == "thmxh") |
        (Provider.name.ilike("%thmxh%"))
    ))
    thmxh_p = res_p.scalar_one_or_none()
    balance = thmxh_p.balance if thmxh_p else 0.002186

    sample_17_cost = 184.80 # TikTok Views: 184.80 VND (thmxh.com/services)
    sample_17_sell = calculate_single_price(sample_17_cost, "PERCENT", markup, auto_round, round_type, round_unit)
    sample_13_cost = 1343.72 # Fast Views: 1343.72 VND (thmxh.com/services)
    sample_13_sell = calculate_single_price(sample_13_cost, "PERCENT", markup, auto_round, round_type, round_unit)

    return ApiResponse(
        data={
            "usd_rate": usd_rate,
            "markup_percent": markup,
            "dealer_markup_percent": dealer_markup,
            "balance": balance,
            "currency": "VND",
            "auto_round": auto_round,
            "round_type": round_type,
            "round_unit": round_unit,
            "auto_sync_interval": sync_interval,
            "sample_tiktok_17_cost": sample_17_cost,
            "sample_tiktok_17_sell": sample_17_sell,
            "sample_tiktok_13_cost": sample_13_cost,
            "sample_tiktok_13_sell": sample_13_sell,
        },
        message="Lấy cấu hình tỷ giá và làm tròn thành công."
    )

@provider_router.post("/providers/recalculate-thmxh", response_model=ApiResponse[dict])
async def admin_recalculate_thmxh_prices(
    payload: Optional[ThmxhPriceRecalculateRequest] = None,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    from app.models.all import SystemSetting
    now = datetime.utcnow()

    res_set = await db.execute(select(SystemSetting).where(
        SystemSetting.key.in_(["thmxh_usd_rate", "thmxh_markup_percent", "thmxh_dealer_markup_percent", "auto_round", "round_type", "round_unit"])
    ))
    settings_dict = {s.key: s.value for s in res_set.scalars().all()}

    usd_rate = float(payload.usd_rate) if payload and payload.usd_rate is not None and payload.usd_rate > 0 else float(settings_dict.get("thmxh_usd_rate", "28000") or 28000)
    markup = float(payload.markup_percent) if payload and payload.markup_percent is not None else float(settings_dict.get("thmxh_markup_percent", "30") or 30)
    dealer_markup = float(payload.dealer_markup_percent) if payload and payload.dealer_markup_percent is not None else float(settings_dict.get("thmxh_dealer_markup_percent", "15") or 15)
    auto_round = payload.auto_round if payload and payload.auto_round is not None else (settings_dict.get("auto_round", "true").lower() != "false")
    round_type = payload.round_type if payload and payload.round_type is not None else settings_dict.get("round_type", "nearest")
    round_unit = float(payload.round_unit) if payload and payload.round_unit is not None and payload.round_unit > 0 else float(settings_dict.get("round_unit", "10") or 10)

    # Save to SystemSetting
    for k, v, d in [
        ("thmxh_usd_rate", str(usd_rate), "Tỷ giá USD sang VNĐ cho THMXH (thmxh.com/services)"),
        ("thmxh_markup_percent", str(markup), "Tỷ lệ tăng giá bán lẻ dịch vụ THMXH (%)"),
        ("thmxh_dealer_markup_percent", str(dealer_markup), "Tỷ lệ tăng giá đại lý dịch vụ THMXH (%)"),
        ("auto_round", str(auto_round).lower(), "Tự động làm tròn giá"),
        ("round_type", str(round_type), "Kiểu làm tròn"),
        ("round_unit", str(round_unit), "Đơn vị làm tròn (VNĐ)"),
    ]:
        s_res = await db.execute(select(SystemSetting).where(SystemSetting.key == k))
        s_obj = s_res.scalar_one_or_none()
        if s_obj:
            s_obj.value = v
            s_obj.updated_at = now
        else:
            db.add(SystemSetting(key=k, value=v, description=d, updated_at=now))

    # Find THMXH provider
    res_p = await db.execute(select(Provider).where(
        (Provider.provider_type == "thmxh") |
        (Provider.name.ilike("%thmxh%"))
    ))
    thmxh_provider = res_p.scalar_one_or_none()
    if not thmxh_provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp Cổng 2.")

    # Get live catalog from THMXH
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
        # 2. Uu tien 2: Gia tu mapping da luu (THMXH luon bao gia theo USD, quy doi xuyen suot qua usd_rate)
        elif m and m.original_rate and m.original_rate > 0:
            raw_usd = float(m.original_rate)
            raw_vnd = round(raw_usd * usd_rate, 2)
        # 3. Uu tien 3: provider_price da co trong DB (da la VND)
        elif srv.provider_price and srv.provider_price > 0:
            raw_vnd = srv.provider_price
            raw_usd = round(raw_vnd / usd_rate, 6)
        else:
            raw_usd = 0.05
            raw_vnd = round(raw_usd * usd_rate, 2)

        selling_price = calculate_single_price(
            rate=raw_vnd,
            markup_type="PERCENT",
            markup_value=markup,
            auto_round=auto_round,
            round_type=round_type,
            round_unit=round_unit,
        )
        dealer_price = calculate_single_price(
            rate=raw_vnd,
            markup_type="PERCENT",
            markup_value=dealer_markup,
            auto_round=auto_round,
            round_type=round_type,
            round_unit=round_unit,
        )

        srv.provider_price = raw_vnd
        srv.price = selling_price
        srv.dealer_price = dealer_price
        srv.updated_at = now

        m = mappings.get(srv.id)
        if m:
            m.original_rate = raw_usd
            m.selling_price = selling_price
            m.dealer_price = dealer_price
            m.auto_round = auto_round
            m.round_type = round_type
            m.round_unit = round_unit
            m.last_synced_at = now

        updated_count += 1

    invalidate_service_cache()
    await db.commit()

    return ApiResponse(
        data={
            "updated_count": updated_count,
            "usd_rate": usd_rate,
            "markup_percent": markup,
            "dealer_markup_percent": dealer_markup,
            "auto_round": auto_round,
            "round_type": round_type,
            "round_unit": round_unit,
        },
        message=f"Đã cập nhật giá và làm tròn {updated_count} dịch vụ Cổng 2 thành công theo giá gốc VNĐ chuẩn từ web mẹ!"
    )

@provider_router.get("/providers/ttc-config", response_model=ApiResponse[dict])
async def admin_get_ttc_config(
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    from app.models.all import SystemSetting
    res = await db.execute(select(SystemSetting).where(
        SystemSetting.key.in_(["ttc_rate_divider", "ttc_markup_percent"])
    ))
    settings_dict = {s.key: s.value for s in res.scalars().all()}
    try:
        divider = float(settings_dict.get("ttc_rate_divider", "1000") or 1000)
    except Exception:
        divider = 1000.0
    if divider <= 0:
        divider = 1000.0
    try:
        markup = float(settings_dict.get("ttc_markup_percent", "30") or 30)
    except Exception:
        markup = 30.0

    res_p = await db.execute(select(Provider).where(
        (Provider.provider_type == "tuongtaccheo") |
        (Provider.name.ilike("%tuongtaccheo%")) |
        (Provider.name.ilike("%ttc%"))
    ))
    ttc_p = res_p.scalar_one_or_none()
    balance = ttc_p.balance if ttc_p else 282605.0

    # Quy đổi: 1.000.000 Xu = 10.000 VNĐ (1 Xu = 1/100 VNĐ; 100 Xu = 1 VNĐ).
    # Với 1 lượt = 1.000 Xu -> 1.000 lượt = 1.000.000 Xu = 10.000 VNĐ vốn.
    effective_div = divider if divider < 300 else (divider / 10.0)
    sample_cost_1k = round((1000.0 * 1000.0) / effective_div, 2)  # 10.000 VNĐ vốn cho 1.000 lượt
    sample_sell_1k = calculate_single_price(sample_cost_1k, "PERCENT", markup, True, "nearest", 10.0)

    return ApiResponse(
        data={
            "rate_divider": divider,
            "markup_percent": markup,
            "balance": balance,
            "currency": "XU",
            "sample_1000_xu_cost": sample_cost_1k,
            "sample_1000_xu_sell": sample_sell_1k
        },
        message="Lấy cấu hình tỷ giá và markup thành công."
    )

@provider_router.post("/providers/recalculate-ttc", response_model=ApiResponse[dict])
async def admin_recalculate_ttc_prices(
    payload: Optional[TtcPriceRecalculateRequest] = None,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    from app.models.all import SystemSetting

    # Load auto-round settings
    res_set = await db.execute(select(SystemSetting).where(
        SystemSetting.key.in_(["ttc_rate_divider", "ttc_markup_percent", "auto_round", "round_type", "round_unit"])
    ))
    settings_dict = {s.key: s.value for s in res_set.scalars().all()}
    auto_round = settings_dict.get("auto_round", "true").lower() != "false"
    round_type = settings_dict.get("round_type", "nearest")
    try:
        round_unit = float(settings_dict.get("round_unit", "10") or 10)
    except Exception:
        round_unit = 10.0

    if payload and payload.rate_divider is not None and payload.rate_divider > 0:
        divider = float(payload.rate_divider)
        s_div = await db.execute(select(SystemSetting).where(SystemSetting.key == "ttc_rate_divider"))
        s_obj = s_div.scalar_one_or_none()
        if s_obj:
            s_obj.value = str(divider)
        else:
            db.add(SystemSetting(key="ttc_rate_divider", value=str(divider), description="Tỷ lệ quy đổi Xu TTC / 1 VNĐ"))
    else:
        try:
            divider = float(settings_dict.get("ttc_rate_divider", "1000") or 1000)
        except Exception:
            divider = 1000.0
    if divider <= 0:
        divider = 1000.0

    if payload and payload.markup_percent is not None:
        markup = float(payload.markup_percent)
        s_mk = await db.execute(select(SystemSetting).where(SystemSetting.key == "ttc_markup_percent"))
        s_obj = s_mk.scalar_one_or_none()
        if s_obj:
            s_obj.value = str(markup)
        else:
            db.add(SystemSetting(key="ttc_markup_percent", value=str(markup), description="Tỷ lệ tăng giá markup dịch vụ TTC (%)"))
    else:
        try:
            markup = float(settings_dict.get("ttc_markup_percent", "30") or 30)
        except Exception:
            markup = 30.0

    # Find TTC providers
    res_p = await db.execute(select(Provider).where(
        (Provider.provider_type == "tuongtaccheo") |
        (Provider.name.ilike("%tuongtaccheo%")) |
        (Provider.name.ilike("%ttc%"))
    ))
    ttc_providers = res_p.scalars().all()
    ttc_provider_ids = [p.id for p in ttc_providers]

    # Find services linked to TTC providers or with TTC in description
    if ttc_provider_ids:
        srv_stmt = select(Service).where(
            (Service.provider_id.in_(ttc_provider_ids)) |
            (Service.description.ilike("%tuongtaccheo%"))
        )
    else:
        srv_stmt = select(Service).where(Service.description.ilike("%tuongtaccheo%"))

    res_srv = await db.execute(srv_stmt)
    services = res_srv.scalars().all()

    # Load mappings for original rates
    if ttc_provider_ids:
        map_res = await db.execute(select(ProviderServiceMapping).where(
            ProviderServiceMapping.provider_id.in_(ttc_provider_ids)
        ))
    else:
        map_res = await db.execute(select(ProviderServiceMapping))
    mappings_by_srv_id = {m.service_id: m for m in map_res.scalars().all() if m.service_id}

    # Fetch live services from TTC API if provider available to get true XU rate
    ttc_live_rates = {}
    if ttc_providers:
        ttc_main = ttc_providers[0]
        try:
            client = ProviderManager.get_provider(ttc_main)
            live_items = await client.get_services()
            for it in live_items:
                ttc_live_rates[str(it.service_id)] = float(it.rate)
        except Exception as e:
            pass

    updated_count = 0
    for srv in services:
        m = mappings_by_srv_id.get(srv.id)
        ext_sid = str(srv.external_service_id or (m.external_service_id if m else "") or "").strip()
        
        # 1. First priority: Live rate from TTC API by external_service_id
        if ext_sid and ext_sid in ttc_live_rates and ttc_live_rates[ext_sid] > 0:
            xu_rate = ttc_live_rates[ext_sid]
        # 2. Second priority: Mapping original_rate (raw xu rate)
        elif m and m.original_rate and 10.0 <= m.original_rate <= 500000.0:
            xu_rate = m.original_rate
        else:
            xu_rate = 1000.0

        # In TuongTacCheo, xu_rate is the price in XU for 1 UNIT (1 lượt).
        # In SMM Panel, provider_price and price are strictly PER 1,000 UNITS (1,000 lượt).
        # 1,000 units cost in Xu = xu_rate * 1000.0.
        # 1.000.000 Xu = 10.000 VNĐ (1 Xu = 1/100 VNĐ, tỷ lệ 100 Xu = 1 VNĐ).
        # Giá vốn 1.000 lượt = (xu_rate * 1000.0) / effective_divider.
        effective_div = divider if divider < 300 else (divider / 10.0)
        provider_price_vnd = round((xu_rate * 1000.0) / effective_div, 2)
        if provider_price_vnd < 100.0:
            provider_price_vnd = max(provider_price_vnd, 100.0)

        selling_price = calculate_single_price(
            rate=provider_price_vnd,
            markup_type="PERCENT",
            markup_value=markup,
            auto_round=auto_round,
            round_type=round_type,
            round_unit=round_unit,
        )
        dealer_price = calculate_single_price(
            rate=provider_price_vnd,
            markup_type="PERCENT",
            markup_value=markup * 0.5,
            auto_round=auto_round,
            round_type=round_type,
            round_unit=round_unit,
        )

        srv.provider_price = provider_price_vnd
        srv.price = selling_price
        srv.dealer_price = dealer_price
        srv.updated_at = datetime.utcnow()

        if m:
            m.original_rate = xu_rate  # Always keep RAW XU in original_rate
            m.selling_price = selling_price
            m.dealer_price = dealer_price
            m.markup_type = "PERCENT"
            m.markup_value = markup
            m.last_synced_at = datetime.utcnow()
        elif ttc_providers and ext_sid:
            new_map = ProviderServiceMapping(
                provider_id=ttc_providers[0].id,
                service_id=srv.id,
                external_service_id=ext_sid,
                external_name=srv.name,
                original_rate=xu_rate,
                markup_type="PERCENT",
                markup_value=markup,
                dealer_markup_type="PERCENT",
                dealer_markup_value=markup * 0.5,
                auto_round=auto_round,
                round_type=round_type,
                round_unit=round_unit,
                selling_price=selling_price,
                dealer_price=dealer_price,
                min_quantity=srv.min_quantity,
                max_quantity=srv.max_quantity,
                refill_enabled=srv.refill_enabled,
                cancel_enabled=srv.cancel_enabled,
                sync_status="ACTIVE",
                last_synced_at=datetime.utcnow(),
                created_at=datetime.utcnow()
            )
            db.add(new_map)

        updated_count += 1

    invalidate_service_cache()
    await db.commit()

    disp_div = int(divider) if divider.is_integer() else divider
    return ApiResponse(
        data={
            "updated_count": updated_count,
            "ttc_rate_divider": divider,
            "ttc_markup_percent": markup,
        },
        message=f"Đã cập nhật giá bán thành công cho {updated_count} dịch vụ Cổng 1 theo tỷ lệ Xu/{disp_div} quy đổi (Markup: {markup}%)."
    )

@provider_router.put("/providers/{id}", response_model=ApiResponse[ProviderResponse])
async def admin_update_provider(
    id: int,
    payload: ProviderUpdate,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")

    if payload.name is not None:
        provider.name = payload.name
    if payload.provider_type is not None:
        provider.provider_type = payload.provider_type
    if payload.base_url is not None:
        if not is_safe_url(payload.base_url, allow_local_in_dev=True):
            raise HTTPException(
                status_code=400,
                detail="URL nhà cung cấp không an toàn hoặc trỏ tới mạng nội bộ (SSRF detected)."
            )
        provider.base_url = payload.base_url.rstrip("/")
    if payload.api_key is not None and payload.api_key.strip() and not payload.api_key.strip().startswith("••••"):
        provider.api_key_encrypted = encrypt_secret(payload.api_key.strip())
    if payload.status is not None:
        provider.status = payload.status

    ProviderManager.clear_cache(id)
    await db.commit()
    await db.refresh(provider)
    return ApiResponse(data=format_provider_response(provider), message="Cập nhật nhà cung cấp thành công.")

@provider_router.delete("/providers/{id}", response_model=ApiResponse[bool])
async def admin_delete_provider(
    id: int,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Kh?ng t?m th?y nh? cung c?p.")
    await db.execute(update(Service).where(Service.provider_id == id).values(provider_id=None, external_service_id=None))
    await db.delete(provider)
    audit = AuditLog(
        user_id=admin.id,
        username=admin.username,
        action="DELETE_PROVIDER",
        target_type="PROVIDER",
        target_id=str(id),
        details=f"X?a nh? cung c?p #{id} ({provider.name})"
    )
    db.add(audit)
    from app.routers.services import invalidate_service_cache
    invalidate_service_cache()
    await db.commit()
    return ApiResponse(data=True, message="?? x?a nh? cung c?p th?nh c?ng.")

# --- TuongTacCheo Dedicated Management Endpoints ---

@provider_router.post("/providers/{id}/ttc-boost", response_model=ApiResponse[dict])
async def admin_ttc_boost_orders(
    id: int,
    payload: TTCBoostRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Tăng tốc độ chạy đơn hàng (Action: boost) trên TuongTacCheo."""
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")
    
    adapter = ProviderManager.get_provider(provider)
    if hasattr(adapter, "boost_multiple_orders"):
        result = await adapter.boost_multiple_orders(payload.service_id, payload.orders)
        return ApiResponse(data=result, message="Đã gửi yêu cầu tăng tốc độ đơn sang TuongTacCheo.")
    raise HTTPException(status_code=400, detail="Nhà cung cấp này không hỗ trợ lệnh boost.")


@provider_router.post("/providers/{id}/ttc-multi-status", response_model=ApiResponse[dict])
async def admin_ttc_multi_status(
    id: int,
    payload: TTCMultiStatusRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Tra cứu trạng thái nhiều đơn hàng cùng lúc từ TuongTacCheo (Action: status + orders)."""
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")
    
    adapter = ProviderManager.get_provider(provider)
    if hasattr(adapter, "get_multiple_orders_status"):
        result = await adapter.get_multiple_orders_status(payload.order_ids)
        return ApiResponse(data=result, message=f"Đã tra cứu thông tin {len(payload.order_ids)} đơn hàng từ TuongTacCheo.")
    raise HTTPException(status_code=400, detail="Nhà cung cấp này không hỗ trợ tra cứu đa đơn.")


@provider_router.post("/providers/{id}/ttc-multi-cancel", response_model=ApiResponse[Any])
async def admin_ttc_multi_cancel(
    id: int,
    payload: TTCMultiCancelRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Gửi yêu cầu hủy nhiều đơn hàng cùng lúc sang TuongTacCheo (Action: cancel + orders)."""
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Không tìm thấy nhà cung cấp.")
    
    adapter = ProviderManager.get_provider(provider)
    if hasattr(adapter, "cancel_multiple_orders"):
        result = await adapter.cancel_multiple_orders(payload.order_ids)
        return ApiResponse(data=result, message=f"Đã gửi lệnh hủy {len(payload.order_ids)} đơn sang TuongTacCheo.")
    raise HTTPException(status_code=400, detail="Nhà cung cấp này không hỗ trợ hủy hàng loạt.")


@provider_router.post("/providers/{id}/ttc-login", response_model=ApiResponse[dict])
async def admin_ttc_test_login(
    id: int,
    payload: TTCLoginRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Đăng nhập hoặc xác minh tài khoản TuongTacCheo (qua Access Token hoặc Username/Password)."""
    from app.providers.tuongtaccheo import TuongTacCheoProvider
    from app.config.settings import settings
    
    if payload.access_token:
        res = await TuongTacCheoProvider.login_by_access_token(payload.access_token)
        return ApiResponse(data=res, message="Kết quả đăng nhập bằng Access Token.")
    
    username = payload.username or getattr(settings, "TTC_USERNAME", "")
    password = payload.password or getattr(settings, "TTC_PASSWORD", "")
    if not username or not password:
        raise HTTPException(status_code=400, detail="Chưa cấu hình tài khoản và mật khẩu TuongTacCheo.")
    res = await TuongTacCheoProvider.login_by_credentials(username, password)
    return ApiResponse(data=res, message="Kết quả đăng nhập tài khoản Cổng 1.")


# --- Full System Auto-Sync & Provider Periodic Sync Endpoints ---

@provider_router.post("/providers/sync-all", response_model=ApiResponse[dict])
@provider_router.post("/sync-all", response_model=ApiResponse[dict])
async def admin_sync_all_providers(
    alert_threshold_percent: float = Query(15.0),
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res_p = await db.execute(select(Provider).where(Provider.status == "ACTIVE"))
    active_providers = res_p.scalars().all()
    if not active_providers:
        res_p = await db.execute(select(Provider))
        active_providers = res_p.scalars().all()

    total_providers = len(active_providers)
    total_mappings_checked = 0
    total_rates_changed = 0
    total_disabled = 0
    total_warnings = 0

    for provider in active_providers:
        try:
            ProviderManager.clear_cache(provider.id)
            adapter = ProviderManager.get_provider(provider)
            services_data = await adapter.get_services()
            bal = await adapter.get_balance()
            provider.balance = bal
            provider.last_sync = datetime.utcnow()

            catalog_map = {str(item.service_id): item for item in services_data}

            m_res = await db.execute(
                select(ProviderServiceMapping).where(ProviderServiceMapping.provider_id == provider.id)
            )
            mappings = m_res.scalars().all()
            total_mappings_checked += len(mappings)

            for m in mappings:
                item = catalog_map.get(str(m.external_service_id))
                srv = None
                if m.service_id:
                    s_res = await db.execute(select(Service).where(Service.id == m.service_id))
                    srv = s_res.scalar_one_or_none()

                if not item:
                    if m.sync_status != "PROVIDER_DISABLED":
                        m.sync_status = "PROVIDER_DISABLED"
                        total_disabled += 1
                        if srv:
                            srv.status = "INACTIVE"
                        log = PriceSyncLog(
                            provider_id=provider.id,
                            service_id=m.service_id,
                            external_service_id=m.external_service_id,
                            service_name=m.external_name or (srv.name if srv else "N/A"),
                            old_rate=m.original_rate,
                            new_rate=m.original_rate,
                            old_price=m.selling_price,
                            new_price=m.selling_price,
                            change_percent=0.0,
                            alert_triggered=True,
                            status="DISABLED",
                            notes="Nhà cung cấp đã gỡ hoặc ngưng dịch vụ này.",
                            created_at=datetime.utcnow()
                        )
                        db.add(log)
                    continue

                if m.sync_status == "PROVIDER_DISABLED":
                    m.sync_status = "ACTIVE"
                    if srv and srv.status == "INACTIVE":
                        srv.status = "ACTIVE"

                old_rate = m.original_rate
                new_rate = float(item.rate)
                old_selling = m.selling_price

                m.min_quantity = int(item.min)
                m.max_quantity = int(item.max)
                m.refill_enabled = bool(item.refill)
                m.cancel_enabled = bool(item.cancel)
                if srv:
                    srv.min_quantity = int(item.min)
                    srv.max_quantity = int(item.max)
                    srv.refill_enabled = bool(item.refill)
                    srv.cancel_enabled = bool(item.cancel)

                if abs(new_rate - old_rate) > 1e-4:
                    total_rates_changed += 1
                    change_pct = (abs(new_rate - old_rate) / old_rate * 100.0) if old_rate > 0 else 0.0
                    is_warning = change_pct >= alert_threshold_percent
                    if is_warning:
                        total_warnings += 1

                    is_ttc_p = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
                    if is_ttc_p:
                        ttc_div_res = await db.execute(select(SystemSetting).where(SystemSetting.key == "ttc_rate_divider"))
                        ttc_div_row = ttc_div_res.scalar_one_or_none()
                        try:
                            ttc_div = float(ttc_div_row.value if ttc_div_row else 1000.0)
                        except Exception:
                            ttc_div = 1000.0
                        if ttc_div <= 0:
                            ttc_div = 1000.0
                        effective_calc_rate = round((new_rate * 1000.0) / ttc_div, 2)
                    elif (provider.provider_type or "").lower() == "thmxh" or "thmxh" in (provider.name or "").lower():
                        # THMXH adapter already converted USD rates to VND in get_services()
                        effective_calc_rate = new_rate
                    else:
                        effective_calc_rate = new_rate

                    prices = calculate_service_prices(
                        rate=effective_calc_rate,
                        user_markup_type=m.markup_type,
                        user_markup_value=m.markup_value,
                        dealer_markup_type=m.dealer_markup_type,
                        dealer_markup_value=m.dealer_markup_value,
                        auto_round=m.auto_round,
                        round_type=m.round_type,
                        round_unit=m.round_unit
                    )

                    m.original_rate = new_rate
                    m.selling_price = prices["selling_price"]
                    m.dealer_price = prices["dealer_price"]
                    m.sync_status = "PRICE_CHANGED" if is_warning else "ACTIVE"

                    if srv:
                        srv.provider_price = effective_calc_rate
                        srv.price = prices["selling_price"]
                        srv.dealer_price = prices["dealer_price"]
                        srv.updated_at = datetime.utcnow()

                    direction = "Tăng" if new_rate > old_rate else "Giảm"
                    notes = f"Giá gốc {direction} {change_pct:.1f}% ({old_rate:,.2f} -> {new_rate:,.2f})"
                    if is_warning:
                        notes += f" [CẢNH BÁO BIẾN ĐỘNG > {alert_threshold_percent:.0f}%]"

                    log = PriceSyncLog(
                        provider_id=provider.id,
                        service_id=m.service_id,
                        external_service_id=m.external_service_id,
                        service_name=m.external_name or (srv.name if srv else item.name),
                        old_rate=old_rate,
                        new_rate=new_rate,
                        old_price=old_selling,
                        new_price=prices["selling_price"],
                        change_percent=round(change_pct, 2),
                        alert_triggered=is_warning,
                        status="WARNING_PRICE_SPIKE" if is_warning else "UPDATED",
                        notes=notes,
                        created_at=datetime.utcnow()
                    )
                    db.add(log)

                m.last_synced_at = datetime.utcnow()

        except Exception as exc:
            logger.warning(f"Error syncing provider mapping for provider {provider.id}: {exc}")

    now = datetime.utcnow()
    st_res = await db.execute(select(SystemSetting).where(SystemSetting.key == "provider_last_auto_sync"))
    st_row = st_res.scalar_one_or_none()
    if st_row:
        st_row.value = now.isoformat()
        st_row.updated_at = now
    else:
        db.add(SystemSetting(key="provider_last_auto_sync", value=now.isoformat(), description="Lần đồng bộ dịch vụ NCC gần nhất", updated_at=now))

    invalidate_service_cache()
    await db.commit()

    return ApiResponse(
        data={
            "providers_synced": total_providers,
            "mappings_checked": total_mappings_checked,
            "rates_changed": total_rates_changed,
            "disabled_count": total_disabled,
            "warning_count": total_warnings,
            "last_synced_at": now.strftime("%H:%M:%S %d/%m/%Y")
        },
        message=f"Đã đồng bộ thành công {total_providers} nhà cung cấp: {total_mappings_checked} dịch vụ đã kiểm tra, {total_rates_changed} dịch vụ đổi giá!"
    )


@provider_router.get("/providers/auto-sync-config", response_model=ApiResponse[dict])
@provider_router.get("/auto-sync-config", response_model=ApiResponse[dict])
async def admin_get_auto_sync_config(
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    res_int = await db.execute(select(SystemSetting).where(SystemSetting.key == "provider_auto_sync_interval"))
    row_int = res_int.scalar_one_or_none()
    interval = int(row_int.value) if row_int and row_int.value and row_int.value.isdigit() else 2

    res_last = await db.execute(select(SystemSetting).where(SystemSetting.key == "provider_last_auto_sync"))
    row_last = res_last.scalar_one_or_none()
    last_sync = row_last.value if row_last else None

    next_sync_str = None
    if interval > 0:
        if last_sync:
            try:
                last_dt = datetime.fromisoformat(last_sync)
                next_dt = last_dt + timedelta(hours=interval)
                next_sync_str = next_dt.strftime("%H:%M:%S %d/%m/%Y")
            except Exception:
                pass
        if not next_sync_str:
            next_dt = datetime.utcnow() + timedelta(hours=interval)
            next_sync_str = next_dt.strftime("%H:%M:%S %d/%m/%Y")

    last_sync_display = None
    if last_sync:
        try:
            last_dt = datetime.fromisoformat(last_sync)
            last_sync_display = last_dt.strftime("%H:%M:%S %d/%m/%Y")
        except Exception:
            last_sync_display = last_sync

    return ApiResponse(
        data={
            "interval_hours": interval,
            "last_synced_at": last_sync_display,
            "next_sync_at": next_sync_str,
            "is_active": interval > 0,
        }
    )


@provider_router.post("/providers/auto-sync-config", response_model=ApiResponse[dict])
@provider_router.post("/auto-sync-config", response_model=ApiResponse[dict])
async def admin_set_auto_sync_config(
    payload: dict,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    interval = int(payload.get("interval_hours", 2))
    st_res = await db.execute(select(SystemSetting).where(SystemSetting.key == "provider_auto_sync_interval"))
    st_row = st_res.scalar_one_or_none()
    now = datetime.utcnow()
    if st_row:
        st_row.value = str(interval)
        st_row.updated_at = now
    else:
        db.add(SystemSetting(key="provider_auto_sync_interval", value=str(interval), description="Chu kỳ tự động đồng bộ dịch vụ NCC (giờ)", updated_at=now))

    await db.commit()
    msg = f"Đã thiết lập tự động đồng bộ dịch vụ sau mỗi {interval} tiếng!" if interval > 0 else "Đã tắt chế độ tự động đồng bộ định kỳ."
    return ApiResponse(
        data={"interval_hours": interval, "is_active": interval > 0},
        message=msg
    )


@provider_router.post("/providers/check-all-balances", response_model=ApiResponse[List[dict]])
async def admin_check_all_balances(
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Kiem tra va cap nhat so du, so xu, trang thai toan bo API provider."""
    import time
    res = await db.execute(select(Provider).where(Provider.status == "ACTIVE").order_by(Provider.id))
    providers = res.scalars().all()
    results = []
    for p in providers:
        ProviderManager.clear_cache(p.id)
        adapter = ProviderManager.get_provider(p)
        is_ttc = (p.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (p.name or "").lower()
        currency = "XU" if is_ttc else ("USD" if (p.provider_type or "").lower() == "thmxh" else "VND")
        
        start_t = time.time()
        status_online = False
        error_msg = None
        new_balance = p.balance or 0.0
        
        try:
            if hasattr(adapter, "test_connection"):
                conn = await adapter.test_connection()
                if conn.get("success"):
                    status_online = True
                    new_balance = float(conn.get("balance", 0.0))
                    currency = conn.get("currency", currency)
                    p.balance = new_balance
                    p.last_sync = datetime.utcnow()
                else:
                    error_msg = conn.get("error")
            else:
                bal = await adapter.get_balance()
                new_balance = float(bal)
                status_online = True
                p.balance = new_balance
                p.last_sync = datetime.utcnow()
        except Exception as e:
            error_msg = str(e)
            
        latency_ms = int((time.time() - start_t) * 1000)
        results.append({
            "id": p.id,
            "name": p.name,
            "provider_type": p.provider_type,
            "currency": currency,
            "balance": new_balance,
            "status": "ONLINE" if status_online else "OFFLINE",
            "latency_ms": latency_ms,
            "error": error_msg,
            "last_sync": p.last_sync.isoformat() if p.last_sync else None
        })
        
    await db.commit()
    return ApiResponse(data=results, message="Da kiem tra va cap nhat so du toan bo nha cung cap.")


@provider_router.post("/providers/{id}/order-status", response_model=ApiResponse[Any])
async def admin_provider_order_status(
    id: int,
    payload: ProviderOrderStatusRequest,
    admin: User = Depends(require_role(["ADMIN"])),
    db: AsyncSession = Depends(get_db)
):
    """Tra cuu truc tiep trang thai don hang tu API (THMXH, TTC, Generic SMM)."""
    res = await db.execute(select(Provider).where(Provider.id == id))
    provider = res.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=404, detail="Khong tim thay nha cung cap.")
        
    adapter = ProviderManager.get_provider(provider)
    action = (payload.action or "status").lower()
    
    # Neu tra cuu nhieu don hang
    if payload.order_ids and len(payload.order_ids) > 1:
        if action == "cancel" and hasattr(adapter, "cancel_multiple_orders"):
            result = await adapter.cancel_multiple_orders(payload.order_ids)
            return ApiResponse(data=result, message=f"Da gui lenh huy {len(payload.order_ids)} don sang API.")
        elif action == "refill_status" and hasattr(adapter, "get_multiple_refill_status"):
            result = await adapter.get_multiple_refill_status(payload.order_ids)
            return ApiResponse(data=result, message=f"Ket qua tra cuu refill {len(payload.order_ids)} don hang.")
        elif hasattr(adapter, "get_multiple_orders_status"):
            result = await adapter.get_multiple_orders_status(payload.order_ids)
            return ApiResponse(data=result, message=f"Ket qua tra cuu trang thai {len(payload.order_ids)} don tu API.")
        else:
            raise HTTPException(status_code=400, detail="Provider khong ho tro tra cuu da don.")
            
    # Tra cuu 1 don hang
    oid = payload.order_id or (payload.order_ids[0] if payload.order_ids else None)
    if not oid:
        raise HTTPException(status_code=400, detail="Vui long nhap ma don hang (Order ID).")
        
    oid = str(oid).strip()
    if action == "cancel":
        if hasattr(adapter, "cancel_order"):
            result = await adapter.cancel_order(oid)
            return ApiResponse(data=result, message=f"Ket qua huy don #{oid}")
        raise HTTPException(status_code=400, detail="Provider khong ho tro huy don.")
    elif action == "refill_status":
        if hasattr(adapter, "get_refill_status"):
            result = await adapter.get_refill_status(oid)
            return ApiResponse(data=result, message=f"Trang thai refill don #{oid}")
        raise HTTPException(status_code=400, detail="Provider khong ho tro tra cuu refill.")
    elif action == "refill":
        if hasattr(adapter, "refill_order"):
            result = await adapter.refill_order(oid)
            return ApiResponse(data=result, message=f"Yeu cau refill don #{oid}")
        raise HTTPException(status_code=400, detail="Provider khong ho tro refill.")
    else:
        status_obj = await adapter.get_order_status(oid)
        is_ttc = (provider.provider_type or "").lower() == "tuongtaccheo" or "ttc" in (provider.name or "").lower()
        curr = "XU" if is_ttc else ("USD" if (provider.provider_type or "").lower() == "thmxh" else "VND")
        data = {
            "order_id": status_obj.order_id,
            "status": status_obj.status,
            "start_count": status_obj.start_count,
            "remains": status_obj.remains,
            "currency": curr,
            "error_message": status_obj.error_message
        }
        return ApiResponse(data=data, message=f"Tra cuu trang thai don #{oid} thanh cong tu API.")
