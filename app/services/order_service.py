from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Dict, Any, Optional
import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException, status

from app.models.all import Order, Service, User, Transaction, Coupon, RefillRequest, Provider, Category
from app.providers.manager import ProviderManager
from app.notifications.telegram import TelegramNotifier
from app.utils.security import validate_target_link

logger = logging.getLogger(__name__)

class OrderService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create_single_order(
        self,
        user: User,
        service_id: int,
        link: str,
        quantity: int,
        coupon_code: Optional[str] = None,
        is_dripfeed: bool = False,
        dripfeed_runs: Optional[int] = None,
        dripfeed_interval: Optional[int] = None,
        comments: Optional[str] = None,
        reaction: Optional[str] = None,
        speed: Optional[str] = None,
        custom_data: Optional[Dict[str, Any]] = None
    ) -> Order:
        # Validate target link
        if not validate_target_link(link):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Đường liên kết không hợp lệ hoặc chứa ký tự nguy hiểm."
            )

        # 1. Validate service and category
        result = await self.db.execute(
            select(Service)
            .join(Category, Service.category_id == Category.id)
            .where(
                Service.id == service_id,
                Service.status == "ACTIVE",
                Service.is_deleted == False,
                Category.status == "ACTIVE",
                Category.is_deleted == False,
            )
        )
        service = result.scalar_one_or_none()
        if not service:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Dịch vụ không tồn tại hoặc tạm ngưng."
            )

        # Reject an unknown coupon before reporting other form errors. This gives
        # the caller a stable, actionable response without ever applying it.
        if coupon_code and coupon_code.strip():
            cp_res = await self.db.execute(
                select(Coupon).where(
                    Coupon.code == coupon_code.strip().upper(),
                    Coupon.status == "ACTIVE",
                )
            )
            if not cp_res.scalar_one_or_none():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mã giảm giá không tồn tại hoặc đã bị khóa.")

        # 2. Validate quantity
        if quantity < service.min_quantity or quantity > service.max_quantity:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Số lượng phải từ {service.min_quantity:,} đến {service.max_quantity:,}."
            )

        # 3. Calculate price
        # Check dealer/reseller tier pricing
        is_dealer = getattr(user, "role", "").upper() in ["RESELLER", "DEALER"]
        effective_rate = service.dealer_price if (is_dealer and getattr(service, "dealer_price", 0) > 0) else service.price
        base_price = (Decimal(quantity) / Decimal("1000")) * Decimal(str(effective_rate))
        discount_amount = Decimal("0")

        if quantity <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Số lượng phải lớn hơn 0.")

        if coupon_code and coupon_code.strip():
            cp_res = await self.db.execute(
                select(Coupon).where(
                    Coupon.code == coupon_code.strip().upper(),
                    Coupon.status == "ACTIVE",
                ).with_for_update()
            )
            coupon = cp_res.scalar_one_or_none()
            if not coupon:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mã giảm giá không tồn tại hoặc đã bị khóa.")
            if coupon.expires_at and coupon.expires_at <= datetime.utcnow():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mã giảm giá đã hết hạn sử dụng.")
            if coupon.used_count >= coupon.usage_limit:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Mã giảm giá đã hết lượt sử dụng.")
            if coupon.min_amount > 0 and base_price < Decimal(str(coupon.min_amount)):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Đơn hàng tối thiểu {int(coupon.min_amount):,}đ mới được áp dụng mã này.")

            from app.models.all import AuditLog
            from sqlalchemy import func
            existing_usage = await self.db.execute(
                select(func.count(AuditLog.id)).where(
                    AuditLog.user_id == user.id,
                    AuditLog.action == "USE_COUPON",
                    AuditLog.target_id == str(coupon.id)
                )
            )
            if (existing_usage.scalar() or 0) >= 1:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Mỗi tài khoản chỉ được sử dụng mã giảm giá này một lần."
                )
            if coupon.type == "PERCENT":
                discount_amount = (base_price * Decimal(str(coupon.value))) / Decimal("100")
                if coupon.max_discount > 0:
                    discount_amount = min(discount_amount, Decimal(str(coupon.max_discount)))
            else:
                discount_amount = min(Decimal(str(coupon.value)), base_price)
            coupon.used_count += 1

        total_price = max(Decimal("0"), (base_price - discount_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        # Audit warning if order total is unexpectedly low compared to standard retail rate
        standard_retail = (Decimal(quantity) / Decimal("1000")) * Decimal(str(service.price))
        if standard_retail > 0 and total_price < standard_retail * Decimal("0.4") and not is_dealer:
            logger.warning(
                f"SECURITY AUDIT: Unusually low price detected for order: user={user.username}, "
                f"service_id={service.id}, qty={quantity}, total={total_price}, standard={standard_retail}"
            )

                # 4. Check user balance with row-level lock (prevents double-spending race conditions)
        user_stmt = select(User).where(User.id == user.id).with_for_update()
        user_res = await self.db.execute(user_stmt)
        locked_user = user_res.scalar_one_or_none()
        if not locked_user or locked_user.status != "ACTIVE" or getattr(locked_user, "is_deleted", False):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Tài khoản không hợp lệ hoặc đã bị khóa."
            )

        if locked_user.balance < total_price:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Số dư không đủ. Bạn cần {int(total_price):,}đ nhưng số dư hiện tại là {int(locked_user.balance):,}đ."
            )

        # 5. Record the requested order while the wallet remains locked. The
        # debit happens only after the provider confirms it accepted the order.
        order = Order(
            user_id=user.id,
            service_id=service.id,
            provider_id=service.provider_id,
            link=link.strip(),
            quantity=quantity,
            start_count=0,
            remains=quantity,
            price=total_price,
            status="PROCESSING",
            is_dripfeed=is_dripfeed,
            dripfeed_runs=dripfeed_runs,
            dripfeed_interval=dripfeed_interval,
            created_at=datetime.utcnow()
        )
        self.db.add(order)
        await self.db.flush()

        # 6. Call provider adapter
        provider_obj = None
        if service.provider_id:
            p_res = await self.db.execute(select(Provider).where(Provider.id == service.provider_id))
            provider_obj = p_res.scalar_one_or_none()

        adapter = ProviderManager.get_provider(provider_obj)
        try:
            extra_kwargs: Dict[str, Any] = {}
            if comments:
                extra_kwargs["comments"] = comments
            if reaction:
                extra_kwargs["reaction"] = reaction
            if speed:
                extra_kwargs["speed"] = speed
            if custom_data:
                extra_kwargs.update(custom_data)

            external_res = await adapter.create_order(
                service_id=service.external_service_id or str(service.id),
                link=order.link,
                quantity=order.quantity,
                runs=dripfeed_runs,
                interval=dripfeed_interval,
                **extra_kwargs
            )
            order.external_order_id = str(external_res.get("order_id", ""))
            if not order.external_order_id:
                raise ValueError("Provider did not return an order id")
            order.status = "PROCESSING"

            balance_before = locked_user.balance
            balance_after = balance_before - total_price
            locked_user.balance = balance_after
            user.balance = balance_after
            self.db.add(Transaction(
                user_id=user.id,
                type="ORDER",
                amount=-total_price,
                balance_before=balance_before,
                balance_after=balance_after,
                reference=f"ORDER-{order.id}",
                description=f"Thanh toán đơn hàng #{order.id}: {service.name} (SL: {quantity:,})",
                status="SUCCESS",
                created_at=datetime.utcnow(),
            ))

            if coupon_code and coupon_code.strip() and coupon:
                self.db.add(AuditLog(
                    user_id=user.id,
                    username=user.username,
                    action="USE_COUPON",
                    target_type="COUPON",
                    target_id=str(coupon.id),
                    details=f"Áp dụng mã {coupon.code} cho đơn #{order.id}"
                ))
        except Exception as e:
            # A failed initial submission is not charged. Keep an immutable
            # failed order for support/audit, but do not queue a paid retry.
            logger.warning(f"Provider initial order dispatch failed for #{order.id}: {e}")
            order.status = "FAILED"
            order.error_message = f"Provider submission failed: {str(e)[:400]}"
            if coupon_code and coupon_code.strip():
                coupon.used_count = max(0, coupon.used_count - 1)
            await TelegramNotifier.notify_provider_error(
                provider_name=provider_obj.name if provider_obj else "Default",
                service_name=service.name,
                error=str(e)
            )

        await self.db.commit()
        await self.db.refresh(order)

        if order.status == "FAILED":
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Nhà cung cấp chưa thể nhận đơn. Số dư của bạn không bị trừ; vui lòng thử lại sau.",
            )

        # 7. Notify admin only when the provider accepted a paid order.
        if order.status == "PROCESSING":
            await TelegramNotifier.notify_new_order(
                order_id=order.id,
                service_name=service.name,
                quantity=order.quantity,
                amount=order.price,
                username=user.username,
            )

        return order

    async def create_mass_orders(self, user: User, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        results = []
        success_count = 0
        failed_count = 0

        for item in items:
            s_id = item.get("service_id")
            link = item.get("link")
            qty = item.get("quantity")
            try:
                order = await self.create_single_order(user, s_id, link, qty)
                results.append({
                    "service_id": s_id,
                    "link": link,
                    "quantity": qty,
                    "order_id": order.id,
                    "status": "success",
                    "price": order.price
                })
                success_count += 1
            except Exception as e:
                results.append({
                    "service_id": s_id,
                    "link": link,
                    "quantity": qty,
                    "status": "error",
                    "message": getattr(e, "detail", str(e))
                })
                failed_count += 1

        return {
            "total": len(items),
            "success": success_count,
            "failed": failed_count,
            "orders": results
        }

    async def request_refill(self, user: User, order_id: int) -> Dict[str, Any]:
        res = await self.db.execute(
            select(Order).where(Order.id == order_id, Order.user_id == user.id).with_for_update()
        )
        order = res.scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")

        if order.status not in {"COMPLETED", "PARTIAL"}:
            raise HTTPException(status_code=400, detail="Refill is available only for completed or partial orders.")
        if not order.external_order_id:
            raise HTTPException(status_code=400, detail="Order has no provider order id.")

        service_res = await self.db.execute(select(Service).where(Service.id == order.service_id))
        service = service_res.scalar_one_or_none()
        if not service or not service.refill_enabled:
            raise HTTPException(status_code=400, detail="This service does not support refill.")

        existing_res = await self.db.execute(
            select(RefillRequest.id).where(
                RefillRequest.order_id == order.id,
                RefillRequest.status.in_(["PENDING", "PROCESSING"]),
            )
        )
        if existing_res.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="A refill request is already in progress for this order.")

        refill = RefillRequest(
            order_id=order.id,
            user_id=user.id,
            status="PROCESSING",
            created_at=datetime.utcnow()
        )
        self.db.add(refill)

        # Provider acceptance is not completion; the worker polls the refill.
        provider_obj = None
        if order.provider_id:
            p_res = await self.db.execute(select(Provider).where(Provider.id == order.provider_id))
            provider_obj = p_res.scalar_one_or_none()
        adapter = ProviderManager.get_provider(provider_obj)
        try:
            p_ref = await adapter.refill_order(order.external_order_id)
            external_id = p_ref.get("refill_id")
            if not external_id:
                raise ValueError("Provider did not return a refill id")
            refill.external_refill_id = str(external_id)
            refill.status = "PROCESSING"
        except Exception as e:
            logger.warning(f"Provider refill submission warning: {e}")
            refill.status = "PENDING"

        await self.db.commit()
        return {"status": "success", "message": "Yêu cầu bảo hành đã được gửi thành công."}

    async def cancel_order_by_user(self, user: User, order_id: int) -> Dict[str, Any]:
        res = await self.db.execute(
            select(Order).where(Order.id == order_id, Order.user_id == user.id).with_for_update()
        )
        order = res.scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")

        if order.status not in ["PENDING", "PROCESSING"]:
            raise HTTPException(status_code=400, detail="Chỉ có thể hủy đơn hàng đang chờ hoặc đang xử lý.")

        prior_refund = await self.db.execute(
            select(Transaction.id).where(
                Transaction.user_id == user.id,
                Transaction.type == "REFUND",
                Transaction.reference.in_([
                    f"CANCEL-ORDER-{order.id}",
                    f"REFUND-ORDER-{order.id}",
                    f"ADMIN-CANCEL-{order.id}",
                    f"AUTO-REFUND-CANCEL-{order.id}",
                    f"AUTO-REFUND-PARTIAL-{order.id}",
                ]),
            )
        )
        if prior_refund.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Order has already been refunded.")

        # Lock the wallet independently from the request-scoped user object.
        # This serializes refunds with deposits, manual adjustments, and orders.
        user_res = await self.db.execute(
            select(User).where(User.id == order.user_id).with_for_update()
        )
        locked_user = user_res.scalar_one_or_none()
        if not locked_user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại.")

        provider_obj = None
        if order.provider_id:
            p_res = await self.db.execute(select(Provider).where(Provider.id == order.provider_id))
            provider_obj = p_res.scalar_one_or_none()

        adapter = ProviderManager.get_provider(provider_obj)
        if order.external_order_id:
            try:
                await adapter.cancel_order(order.external_order_id)
            except Exception as ex:
                logger.warning(f"Provider cancel call warning: {ex}")

        order.status = "CANCELED"
        # Refund user balance
        refund_amount = order.price
        balance_before = locked_user.balance
        balance_after = balance_before + refund_amount
        locked_user.balance = balance_after

        tx = Transaction(
            user_id=locked_user.id,
            type="REFUND",
            amount=refund_amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reference=f"CANCEL-ORDER-{order.id}",
            description=f"Hoàn tiền tự động khi hủy đơn #{order.id}",
            status="SUCCESS",
            created_at=datetime.utcnow()
        )
        self.db.add(tx)
        await self.db.commit()

        return {
            "status": "success",
            "order_id": order.id,
            "refunded_amount": refund_amount,
            "message": f"Đã hủy đơn #{order.id} và hoàn {int(refund_amount):,}đ vào tài khoản."
        }

    async def refund_order_by_admin(self, order_id: int, refund_amount: Optional[float] = None, reason: str = "") -> Dict[str, Any]:
        res = await self.db.execute(select(Order).where(Order.id == order_id).with_for_update())
        order = res.scalar_one_or_none()
        if not order:
            raise HTTPException(status_code=404, detail="Không tìm thấy đơn hàng.")

        if order.status in {"CANCELED", "REFUNDED"}:
            raise HTTPException(status_code=409, detail="Order has already been canceled or refunded.")

        prior_refund = await self.db.execute(
            select(Transaction.id).where(
                Transaction.user_id == order.user_id,
                Transaction.type == "REFUND",
                Transaction.reference.in_([
                    f"CANCEL-ORDER-{order.id}",
                    f"REFUND-ORDER-{order.id}",
                    f"ADMIN-CANCEL-{order.id}",
                    f"AUTO-REFUND-CANCEL-{order.id}",
                    f"AUTO-REFUND-PARTIAL-{order.id}",
                ]),
            )
        )
        if prior_refund.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail="Order has already been refunded.")

        amount = Decimal(str(refund_amount)) if refund_amount is not None else order.price
        if amount != order.price:
            raise HTTPException(status_code=400, detail="Refund amount must equal the full order charge.")
        if amount < 0:
            raise HTTPException(status_code=400, detail="Số tiền hoàn không thể âm.")

        user_res = await self.db.execute(
            select(User).where(User.id == order.user_id).with_for_update()
        )
        user = user_res.scalar_one_or_none()
        if not user:
            raise HTTPException(status_code=404, detail="Người dùng không tồn tại.")

        balance_before = user.balance
        balance_after = balance_before + amount
        user.balance = balance_after

        order.status = "REFUNDED"

        tx = Transaction(
            user_id=user.id,
            type="REFUND",
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reference=f"REFUND-ORDER-{order.id}",
            description=f"Hoàn tiền đơn hàng #{order.id}: {reason or 'Quản trị viên hoàn tiền'}",
            status="SUCCESS",
            created_at=datetime.utcnow()
        )
        self.db.add(tx)
        await self.db.commit()

        return {
            "status": "success",
            "order_id": order.id,
            "refunded_amount": amount,
            "new_balance": balance_after
        }
