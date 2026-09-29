import logging
import re
from typing import Dict, Any, List
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.database.session import get_db
from app.models.all import User, Service, Order, RefillRequest, Category

logger = logging.getLogger(__name__)
from app.services.order_service import OrderService
from app.utils.security import validate_target_link
from app.auth.security import hash_api_key
from app.middleware.rate_limit import smm_v2_api_key_limiter, failed_api_key_tracker

router = APIRouter(tags=["SMM Protocol API v2"])

@router.post("/api/v2")
async def smm_v2_endpoint(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    """
    Standard SMM API v2 endpoint.
    Accepts application/x-www-form-urlencoded and application/json.
    Actions supported:
      - services
      - add
      - status (single & multiple)
      - refill (single & multiple)
      - refill_status (single & multiple)
      - cancel (single & multiple)
      - balance
    """
    # Parse payload from form or json
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            data = {}
    else:
        form = await request.form()
        data = dict(form)

    api_key = str(data.get("key") or "").strip()
    action = str(data.get("action") or "").lower().strip()

    if not api_key:
        return {"error": "Incorrect request: Missing key"}

    client_ip = request.client.host if request.client else "127.0.0.1"
    is_test = settings.APP_ENV.lower() == "test"
    test_flag = bool(request.headers.get("X-Test-Rate-Limit"))
    if failed_api_key_tracker.is_blocked(client_ip, is_test=is_test, test_flag=test_flag):
        return {"error": "Too many failed attempts. Please retry later."}

    if len(api_key) > 128 or len(api_key) < 16:
        failed_api_key_tracker.record_failure(client_ip)
        return {"error": "API key invalid"}

    # Enforce per-key rate limiting before running database queries
    smm_v2_api_key_limiter.check(api_key)

    # Authenticate user by API key
    res = await db.execute(select(User).where(
        User.api_key_hash == hash_api_key(api_key),
        User.status == "ACTIVE",
        User.is_deleted.is_(False),
    ))
    user = res.scalar_one_or_none()
    if not user:
        failed_api_key_tracker.record_failure(client_ip)
        return {"error": "API key invalid"}

    from app.models.all import AuditLog
    if action in {"add", "cancel", "refill"}:
        db.add(AuditLog(
            user_id=user.id,
            username=user.username,
            action="RESELLER_API",
            target_type="SMM_V2",
            target_id=action,
            ip_address=client_ip,
            details=f"Action: {action}, Service: {data.get('service')}, Link: {str(data.get('link') or '')[:80]}"
        ))

    # 1. Action: services
    if action == "services":
        s_res = await db.execute(
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
        services_list = list(s_res.scalars().all())
        services_list.sort(key=lambda s: (
            1 if (
                getattr(s, "is_ttc", False)
                or s.provider_id == 1
                or (s.name and "ttc" in s.name.lower())
                or (s.description and "ttc" in s.description.lower())
                or s.sort_order >= 900
            ) else 0,
            s.sort_order,
            s.id
        ))
        return [
            {
                "service": s.id,
                "name": s.name,
                "type": s.service_type or "Default",
                "category": s.platform or "General",
                "rate": str(round(s.price, 2)),
                "min": str(s.min_quantity),
                "max": str(s.max_quantity),
                "refill": bool(s.refill_enabled),
                "cancel": bool(s.cancel_enabled)
            }
            for s in services_list
        ]

    # 2. Action: add (Add order)
    if action == "add":
        service_id_raw = data.get("service")
        link = str(data.get("link") or "").strip()
        quantity_raw = data.get("quantity")

        if not service_id_raw or not link or not quantity_raw:
            return {"error": "Missing required fields (service, link, quantity)"}

        if not validate_target_link(link):
            return {"error": "Đường liên kết không hợp lệ hoặc chứa ký tự nguy hiểm."}

        try:
            service_id = int(service_id_raw)
            quantity = int(quantity_raw)
            if service_id <= 0 or quantity <= 0:
                return {"error": "Invalid service ID or quantity format"}
            runs = int(data["runs"]) if data.get("runs") else None
            interval = int(data["interval"]) if data.get("interval") else None
            if runs is not None and runs <= 0:
                return {"error": "Runs must be greater than 0"}
            if interval is not None and interval <= 0:
                return {"error": "Interval must be greater than 0"}
        except ValueError:
            return {"error": "Invalid service ID, quantity, runs, or interval format"}

        service = OrderService(db)
        try:
            order = await service.create_single_order(
                user=user,
                service_id=service_id,
                link=link,
                quantity=quantity,
                is_dripfeed=bool(runs and interval),
                dripfeed_runs=runs,
                dripfeed_interval=interval
            )
            return {"order": order.id}
        except HTTPException as e:
            return {"error": e.detail}
        except Exception as ex:
            logger.error("SMM v2 add order error: %s", ex, exc_info=True)
            return {"error": "Failed to create order. Please try again later."}

    # 3. Action: status (Single or Multiple)
    if action == "status":
        order_id = data.get("order")
        orders_ids = data.get("orders")

        if order_id:
            try:
                oid = int(order_id)
            except ValueError:
                return {"error": "Incorrect order ID"}

            stmt = select(Order).where(Order.id == oid)
            if user.role != "ADMIN":
                stmt = stmt.where(Order.user_id == user.id)
            res_ord = await db.execute(stmt)
            ord_obj = res_ord.scalar_one_or_none()
            if not ord_obj:
                return {"error": "Incorrect order ID"}

            return {
                "charge": str(round(ord_obj.price, 4)),
                "start_count": str(ord_obj.start_count),
                "status": ord_obj.status.capitalize(),
                "remains": str(ord_obj.remains),
                "currency": "VND"
            }

        elif orders_ids:
            id_list = [s.strip() for s in str(orders_ids).split(",") if s.strip()][:100][:100][:100]
            results = {}
            for sid in id_list:
                try:
                    oid = int(sid)
                    stmt = select(Order).where(Order.id == oid)
                    if user.role != "ADMIN":
                        stmt = stmt.where(Order.user_id == user.id)
                    res_ord = await db.execute(stmt)
                    ord_obj = res_ord.scalar_one_or_none()
                    if ord_obj:
                        results[sid] = {
                            "charge": str(round(ord_obj.price, 4)),
                            "start_count": str(ord_obj.start_count),
                            "status": ord_obj.status.capitalize(),
                            "remains": str(ord_obj.remains),
                            "currency": "VND"
                        }
                    else:
                        results[sid] = {"error": "Incorrect order ID"}
                except Exception:
                    results[sid] = {"error": "Incorrect order ID"}
            return results
        else:
            return {"error": "Missing order or orders parameter"}

    # 4. Action: refill
    if action == "refill":
        order_id = data.get("order")
        orders_ids = data.get("orders")
        service = OrderService(db)

        if order_id:
            try:
                oid = int(order_id)
                await service.request_refill(user, oid)
                return {"refill": str(oid)}
            except Exception as e:
                return {"error": getattr(e, "detail", str(e))}

        elif orders_ids:
            id_list = [s.strip() for s in str(orders_ids).split(",") if s.strip()]
            results = []
            for sid in id_list:
                try:
                    oid = int(sid)
                    await service.request_refill(user, oid)
                    results.append({"order": oid, "refill": oid})
                except Exception:
                    results.append({"order": sid, "refill": {"error": "Incorrect order ID"}})
            return results
        else:
            return {"error": "Missing order or orders parameter"}

    # 5. Action: refill_status
    if action == "refill_status":
        refill_id = data.get("refill")
        refills_ids = data.get("refills")

        if refill_id:
            try:
                rid = int(refill_id)
            except ValueError:
                return {"error": "Incorrect refill ID"}

            stmt = select(RefillRequest).where(or_(RefillRequest.id == rid, RefillRequest.order_id == rid))
            if user.role != "ADMIN":
                stmt = stmt.where(RefillRequest.user_id == user.id)
            ref_obj = (await db.execute(stmt)).scalars().first()
            if not ref_obj:
                return {"error": "Incorrect refill ID"}

            st_text = "Completed" if ref_obj.status in ("COMPLETED", "PROCESSING") else ref_obj.status.capitalize()
            return {"status": st_text}

        elif refills_ids:
            r_list = [s.strip() for s in str(refills_ids).split(",") if s.strip()][:100]
            results = []
            for r in r_list:
                try:
                    rid = int(r)
                    stmt = select(RefillRequest).where(or_(RefillRequest.id == rid, RefillRequest.order_id == rid))
                    if user.role != "ADMIN":
                        stmt = stmt.where(RefillRequest.user_id == user.id)
                    ref_obj = (await db.execute(stmt)).scalars().first()
                    if ref_obj:
                        st_text = "Completed" if ref_obj.status in ("COMPLETED", "PROCESSING") else ref_obj.status.capitalize()
                        results.append({"refill": r, "status": st_text})
                    else:
                        results.append({"refill": r, "error": "Incorrect refill ID"})
                except Exception:
                    results.append({"refill": r, "error": "Incorrect refill ID"})
            return results
        else:
            return {"error": "Missing refill or refills parameter"}

    # 6. Action: cancel
    if action == "cancel":
        orders_ids = data.get("orders") or data.get("order")
        if not orders_ids:
            return {"error": "Missing orders parameter"}

        id_list = [s.strip() for s in str(orders_ids).split(",") if s.strip()][:100]
        service = OrderService(db)
        results = []
        for sid in id_list:
            try:
                oid = int(sid)
                await service.cancel_order_by_user(user, oid)
                results.append({"order": oid, "cancel": 1})
            except Exception:
                results.append({"order": sid, "cancel": {"error": "Incorrect order ID"}})
        return results

    # 7. Action: balance
    if action == "balance":
        return {
            "balance": str(round(user.balance, 2)),
            "currency": "VND"
        }

    clean_action = re.sub(r'[^a-zA-Z0-9_-]', '', action)[:32]
    return {"error": f"Incorrect request: Unknown action '{clean_action}'"}
