from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import SubSite, User
from app.schemas.all import ApiResponse, SubSiteCreate
from app.auth.security import get_current_user
from app.utils.security import is_safe_subsite_domain

router = APIRouter(prefix="/sub-sites", tags=["Sub Sites / Reseller"])



@router.get("", response_model=ApiResponse[list])
async def list_sub_sites(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(SubSite).where(SubSite.user_id == current_user.id).order_by(desc(SubSite.created_at))
    res = await db.execute(stmt)
    sites = res.scalars().all()
    output = []
    for s in sites:
        output.append({
            "id": s.id,
            "domain": s.domain,
            "site_name": s.site_name,
            "markup_percent": s.markup_percent,
            "status": s.status,
            "created_at": s.created_at
        })
    return ApiResponse(data=output)

@router.post("", response_model=ApiResponse[dict])
async def create_sub_site(
    payload: SubSiteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    clean_domain = payload.domain.strip().lower()
    if not is_safe_subsite_domain(clean_domain):
        raise HTTPException(status_code=400, detail="Định dạng tên miền không hợp lệ hoặc không được phép (ví dụ: sub.example.com).")
    res = await db.execute(select(SubSite).where(SubSite.domain == clean_domain))
    if res.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Tên miền này đã được đăng ký.")

    site = SubSite(
        user_id=current_user.id,
        domain=clean_domain,
        site_name=payload.site_name.strip(),
        markup_percent=payload.markup_percent,
        status="PENDING",
        created_at=datetime.utcnow()
    )
    db.add(site)
    await db.commit()
    await db.refresh(site)
    return ApiResponse(data={"id": site.id, "domain": site.domain, "status": site.status}, message="Đăng ký website con thành công. Vui lòng trỏ bản ghi CNAME về hệ thống.")
