import re
import time
from typing import List, Optional, Dict, Tuple, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import Service, Category, Provider
from app.schemas.all import ApiResponse, ServiceResponse, CategoryResponse
from app.utils.security import sanitize_search_query

router = APIRouter(prefix="/services", tags=["Services & Categories"])

# High-performance in-memory cache for read-heavy catalog endpoints
_CACHE: Dict[str, Tuple[float, Any]] = {}
CACHE_TTL_SECONDS = 60.0

def get_cached(key: str) -> Optional[Any]:
    now = time.time()
    if key in _CACHE:
        ts, data = _CACHE[key]
        if now - ts < CACHE_TTL_SECONDS:
            return data
        del _CACHE[key]
    return None

def set_cached(key: str, data: Any):
    _CACHE[key] = (time.time(), data)

def invalidate_service_cache():
    """Purges the cached catalog upon service/category modifications."""
    _CACHE.clear()

@router.get("", response_model=ApiResponse[List[ServiceResponse]])
async def list_services(
    platform: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    search = sanitize_search_query(search)
    cache_key = f"services:{platform or ''}:{category_id or ''}:{search or ''}"
    cached = get_cached(cache_key)
    if cached is not None:
        return ApiResponse(data=cached)

    stmt = (
        select(Service)
        .join(Category, Service.category_id == Category.id)
        .where(
            Service.status == "ACTIVE",
            Service.is_deleted == False,
            Category.status == "ACTIVE",
            Category.is_deleted == False,
        )
        .order_by(Service.sort_order, Service.id)
    )

    if platform and platform.strip().upper() not in ("ALL", "*", ""):
        stmt = stmt.where(Service.platform.ilike(platform.strip()))
    if category_id:
        stmt = stmt.where(Service.category_id == category_id)
    if search:
        stmt = stmt.where(Service.name.ilike(f"%{search}%"))

    res = await db.execute(stmt)
    services = res.scalars().all()

    # Preload categories in batch to prevent N+1 queries
    cat_ids = {s.category_id for s in services}
    cat_map = {}
    if cat_ids:
        c_res = await db.execute(select(Category).where(Category.id.in_(cat_ids)))
        for c in c_res.scalars().all():
            cat_map[c.id] = c.name

    prov_ids = {s.provider_id for s in services if s.provider_id}
    prov_map = {}
    if prov_ids:
        p_res = await db.execute(select(Provider).where(Provider.id.in_(prov_ids)))
        for p in p_res.scalars().all():
            prov_map[p.id] = (p.name or "", p.provider_type or "")

    output = []
    for s in services:
        sr = ServiceResponse.model_validate(s)
        sr.category_name = cat_map.get(s.category_id)
        # White-label protection: Mask provider internal IDs and wholesale price from public catalog
        sr.provider_id = None
        sr.external_service_id = None
        sr.provider_price = None

        # Scrub provider references from public description
        raw_desc = (s.description or "").strip()
        if any(kw in raw_desc.lower() for kw in ("thmxh", "tuongtaccheo", "dịch vụ chuẩn từ", "provider", "ttc")):
            clean_d = re.sub(r"Dịch vụ chuẩn từ [^\-]+ - ", "", raw_desc, flags=re.IGNORECASE)
            clean_d = re.sub(r"https?://\S+", "", clean_d)
            clean_d = re.sub(r"(thmxh(\.com)?|tuongtaccheo(\.com)?|ttc|provider)", "", clean_d, flags=re.IGNORECASE).strip()
            sr.description = clean_d if len(clean_d) >= 15 else ""
        else:
            sr.description = raw_desc
        p_name, p_type = prov_map.get(s.provider_id, ("", ""))
        is_ttc = bool(
            s.provider_id == 1
            or p_type.lower() == "tuongtaccheo"
            or "tuongtaccheo" in p_name.lower()
            or "ttc" in p_name.lower()
            or "tuongtaccheo" in (s.description or "").lower()
            or "ttc" in (s.description or "").lower()
            or "[ttc]" in (s.name or "").lower()
        )
        sr.is_ttc = is_ttc
        sr.server_tag = "V1" if is_ttc else "PRO"
        output.append(sr)

    # Sap xep TTC xuong duoi cung
    output.sort(key=lambda s: (1 if getattr(s, 'is_ttc', False) else 0, s.sort_order, s.id))

    set_cached(cache_key, output)
    return ApiResponse(data=output)

@router.get("/categories", response_model=ApiResponse[List[CategoryResponse]])
async def list_categories(
    platform: Optional[str] = Query(None),
    hide_empty: Optional[bool] = Query(True),
    db: AsyncSession = Depends(get_db)
):
    cache_key = f"categories:{platform or ''}:{hide_empty}"
    cached = get_cached(cache_key)
    if cached is not None:
        return ApiResponse(data=cached)

    stmt = select(Category).where(
        Category.status == "ACTIVE",
        Category.is_deleted == False
    ).order_by(Category.sort_order, Category.id)
    if platform and platform.strip().upper() not in ("ALL", "*", ""):
        stmt = stmt.where(Category.platform.ilike(platform.strip()))
    res = await db.execute(stmt)
    categories = res.scalars().all()

    # Pre-calculate active service count per category and identify TTC categories
    srv_counts_res = await db.execute(
        select(Service.category_id, Service.provider_id, func.count(Service.id))
        .where(Service.status == "ACTIVE", Service.is_deleted == False)
        .group_by(Service.category_id, Service.provider_id)
    )
    count_map = {}
    ttc_cat_ids = set()
    for cat_id, prov_id, cnt in srv_counts_res.fetchall():
        count_map[cat_id] = count_map.get(cat_id, 0) + cnt
        if prov_id == 1:
            ttc_cat_ids.add(cat_id)

    output = []
    for c in categories:
        cnt = count_map.get(c.id, 0)
        if hide_empty and cnt == 0:
            continue
        cr = CategoryResponse.model_validate(c)
        cr.service_count = cnt
        is_ttc_cat = bool(c.id in ttc_cat_ids or c.id in (1, 2, 4, 5) or 'ttc' in (c.name or '').lower() or c.sort_order >= 900)
        cr.is_ttc = is_ttc_cat
        output.append(cr)

    # Sap xep danh muc: cac danh muc TTC luon o duoi cung
    output.sort(key=lambda c: (1 if getattr(c, 'is_ttc', False) else 0, c.sort_order, c.id))

    set_cached(cache_key, output)
    return ApiResponse(data=output)

@router.get("/{id}", response_model=ApiResponse[ServiceResponse])
async def get_service(id: int, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Service)
        .join(Category, Service.category_id == Category.id)
        .where(
            Service.id == id,
            Service.status == "ACTIVE",
            Service.is_deleted == False,
            Category.status == "ACTIVE",
            Category.is_deleted == False,
        )
    )
    srv = res.scalar_one_or_none()
    if not srv:
        raise HTTPException(status_code=404, detail="Không tìm thấy dịch vụ.")
    
    sr = ServiceResponse.model_validate(srv)
    sr.provider_id = None
    sr.external_service_id = None
    sr.provider_price = None
    cat_res = await db.execute(select(Category).where(Category.id == srv.category_id))
    cat = cat_res.scalar_one_or_none()
    if cat:
        sr.category_name = cat.name
    return ApiResponse(data=sr)
