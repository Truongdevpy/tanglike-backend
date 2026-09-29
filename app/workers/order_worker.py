import asyncio
import logging
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta
from sqlalchemy import case, delete, select
from app.database.session import AsyncSessionLocal
from app.models.all import AuditLog, EmailVerificationToken, JobRun, Order, PasswordResetToken, PriceSyncLog, Provider, RefillRequest, Service, User, Transaction
from app.providers.manager import ProviderManager
from app.payments.bank_sync import BankSyncService
from app.config.settings import settings
from app.notifications.telegram import TelegramNotifier

logger = logging.getLogger(__name__)

class OrderWorker:
    def __init__(self, check_interval: int = 15):
        self.check_interval = check_interval
        self.is_running = False
        self._last_bank_sync: datetime | None = None
        self._last_provider_sync: datetime | None = None
        self._last_maintenance: datetime | None = None

    async def _run_monitored(self, job_name: str, operation):
        """Run worker work without losing execution state to transient logs."""
        started_at = datetime.utcnow()
        run_status = "SUCCESS"
        details = None
        try:
            await operation()
        except Exception as exc:
            run_status = "FAILED"
            details = str(exc)[:500]
            logger.exception("Worker job %s failed", job_name)
        finally:
            completed_at = datetime.utcnow()
            async with AsyncSessionLocal() as db:
                db.add(JobRun(
                    job_name=job_name,
                    status=run_status,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=max(0, int((completed_at - started_at).total_seconds() * 1000)),
                    details=details,
                ))
                await db.commit()
                if run_status == "FAILED":
                    recent = (await db.execute(
                        select(JobRun.status)
                        .where(JobRun.job_name == job_name)
                        .order_by(JobRun.started_at.desc())
                        .limit(4)
                    )).scalars().all()
                    # Alert once at the threshold; later failures remain visible
                    # in job history without repeatedly flooding administrators.
                    if (
                        len(recent) >= 3
                        and all(item == "FAILED" for item in recent[:3])
                        and (len(recent) == 3 or recent[3] != "FAILED")
                    ):
                        await TelegramNotifier.send_message(
                            f"<b>WORKER FAILURE ALERT</b>\n\n"
                            f"Job: {job_name}\n"
                            "This job has failed 3 consecutive times. Please inspect Cron & Worker."
                        )

    async def sync_bank_transactions(self):
        interval = max(15, settings.MB_BANK_POLL_INTERVAL_SECONDS)
        if self._last_bank_sync and (datetime.utcnow() - self._last_bank_sync).total_seconds() < interval:
            return
        self._last_bank_sync = datetime.utcnow()
        async with AsyncSessionLocal() as db:
            try:
                result = await BankSyncService(db).sync_mbbank()
                if result.get("seen", 0) > 0 or result.get("processed", 0) > 0:
                    logger.info("MB Bank auto sync completed: %s", result)
            except Exception as exc:
                logger.exception("MB Bank auto sync failed: %s", exc)
                raise

    async def sync_provider_services(self):
        """Refresh active provider mappings based on admin configured auto-sync interval."""
        async with AsyncSessionLocal() as db:
            from app.models.all import SystemSetting
            st = await db.scalar(select(SystemSetting.value).where(SystemSetting.key == "provider_auto_sync_interval"))
            interval_hours = int(st) if st and st.isdigit() else 2
            if interval_hours <= 0:
                return  # Auto sync disabled
            interval_seconds = max(300, interval_hours * 3600)
            now = datetime.utcnow()
            if self._last_provider_sync and (now - self._last_provider_sync).total_seconds() < interval_seconds:
                return
            self._last_provider_sync = now
            from app.routers.admin_providers import admin_sync_provider_mappings
            providers = (await db.execute(
                select(Provider.id).where(Provider.status == "ACTIVE")
            )).scalars().all()
            for provider_id in providers:
                await admin_sync_provider_mappings(
                    id=provider_id,
                    alert_threshold_percent=15.0,
                    db=db,
                )

    async def cleanup_expired_records(self):
        """Purge disposable credentials and operational history past retention."""
        from sqlalchemy import and_
        now = datetime.utcnow()
        if self._last_maintenance and (now - self._last_maintenance) < timedelta(hours=24):
            return
        self._last_maintenance = now
        async with AsyncSessionLocal() as db:
            # Dynamically read retention days from SystemSetting
            from app.models.all import SystemSetting
            st_res = await db.execute(select(SystemSetting).where(SystemSetting.key.in_([
                "cleanup_auto_enabled",
                "retention_audit_logs_days",
                "retention_job_runs_days",
                "retention_price_sync_days",
                "retention_reset_tokens_days",
                "retention_read_notifications_days",
                "retention_stale_payments_days",
            ])))
            st_dict = {s.key: s.value for s in st_res.scalars().all()}

            if st_dict.get("cleanup_auto_enabled", "true").lower() == "false":
                logger.info("Auto-cleanup is disabled via SystemSetting.")
                return

            audit_days = int(st_dict.get("retention_audit_logs_days", settings.AUDIT_LOG_RETENTION_DAYS))
            job_days = int(st_dict.get("retention_job_runs_days", settings.JOB_RUN_RETENTION_DAYS))
            price_days = int(st_dict.get("retention_price_sync_days", settings.PRICE_SYNC_LOG_RETENTION_DAYS))
            token_days = int(st_dict.get("retention_reset_tokens_days", settings.PASSWORD_RESET_TOKEN_RETENTION_DAYS))
            notif_days = int(st_dict.get("retention_read_notifications_days", 30))
            pay_days = int(st_dict.get("retention_stale_payments_days", 14))

            reset_cutoff = now - timedelta(days=max(1, token_days))
            job_cutoff = now - timedelta(days=max(1, job_days))
            audit_cutoff = now - timedelta(days=max(1, audit_days))
            price_sync_cutoff = now - timedelta(days=max(1, price_days))
            notif_cutoff = now - timedelta(days=max(1, notif_days))
            pay_cutoff = now - timedelta(days=max(1, pay_days))

            reset_result = await db.execute(delete(PasswordResetToken).where(
                PasswordResetToken.expires_at < reset_cutoff
            ))
            verification_result = await db.execute(delete(EmailVerificationToken).where(
                EmailVerificationToken.expires_at < reset_cutoff
            ))
            job_result = await db.execute(delete(JobRun).where(JobRun.completed_at < job_cutoff))
            audit_result = await db.execute(delete(AuditLog).where(AuditLog.created_at < audit_cutoff))
            price_sync_result = await db.execute(delete(PriceSyncLog).where(PriceSyncLog.created_at < price_sync_cutoff))
            notif_result = await db.execute(delete(Notification).where(
                and_(Notification.is_read.is_(True), Notification.created_at < notif_cutoff)
            ))
            pay_result = await db.execute(delete(Payment).where(
                and_(Payment.status == "PENDING", Payment.created_at < pay_cutoff)
            ))

            total_del = (
                reset_result.rowcount + verification_result.rowcount + job_result.rowcount +
                audit_result.rowcount + price_sync_result.rowcount + notif_result.rowcount + pay_result.rowcount
            )
            summary_msg = f"Đã dọn dẹp {total_del} bản ghi: {audit_result.rowcount} kiểm toán, {job_result.rowcount} jobs, {notif_result.rowcount} thông báo, {reset_result.rowcount + verification_result.rowcount} tokens, {pay_result.rowcount} giao dịch treo"

            # Save status
            for k, v in [("cleanup_last_run", now.isoformat()), ("cleanup_last_summary", summary_msg)]:
                s_obj = (await db.execute(select(SystemSetting).where(SystemSetting.key == k))).scalar_one_or_none()
                if s_obj:
                    s_obj.value = v
                    s_obj.updated_at = now
                else:
                    db.add(SystemSetting(key=k, value=v, description="Nhật ký dọn dẹp rác tự động", updated_at=now))

            await db.commit()
            logger.info(
                "Worker cleanup completed: %s", summary_msg
            )

    async def sync_pending_orders(self):
        async with AsyncSessionLocal() as db:
            try:
                # 1. Retry unsubmitted orders (orders created when network was temporarily unavailable)
                now = datetime.utcnow()
                unsubmitted_res = await db.execute(
                    select(Order.id).where(
                        Order.status == "PENDING",
                        Order.external_order_id.is_(None),
                        (Order.next_retry_at.is_(None)) | (Order.next_retry_at <= now),
                    ).limit(10)
                )
                unsubmitted_ids = unsubmitted_res.scalars().all()
                for unsub_id in unsubmitted_ids:
                    try:
                        u_ord = (await db.execute(
                            select(Order).where(Order.id == unsub_id).with_for_update()
                        )).scalar_one_or_none()
                        if not u_ord:
                            continue
                        p_res = await db.execute(select(Provider).where(Provider.id == u_ord.provider_id))
                        p_obj = p_res.scalar_one_or_none()
                        s_res = await db.execute(select(Service).where(Service.id == u_ord.service_id))
                        s_obj = s_res.scalar_one_or_none()

                        if s_obj:
                            adapter = ProviderManager.get_provider(p_obj)
                            ext_res = await adapter.create_order(
                                service_id=s_obj.external_service_id or str(s_obj.id),
                                link=u_ord.link,
                                quantity=u_ord.quantity,
                                runs=u_ord.dripfeed_runs,
                                interval=u_ord.dripfeed_interval
                            )
                            if ext_res.get("order_id"):
                                u_ord.external_order_id = str(ext_res["order_id"])
                                u_ord.status = "PROCESSING"
                                u_ord.next_retry_at = None
                                await db.commit()
                                logger.info(f"Retried and submitted order #{u_ord.id} -> External #{u_ord.external_order_id}")
                    except Exception as ex:
                        u_ord.retry_count += 1
                        if u_ord.retry_count >= settings.PROVIDER_MAX_RETRY_ATTEMPTS:
                            u_ord.status = "FAILED"
                            u_ord.next_retry_at = None
                            u_ord.error_message = f"Provider submission failed after {u_ord.retry_count} attempts: {str(ex)[:400]}"
                        else:
                            u_ord.next_retry_at = datetime.utcnow() + timedelta(seconds=30 * (2 ** (u_ord.retry_count - 1)))
                            u_ord.error_message = f"Provider retry {u_ord.retry_count}/{settings.PROVIDER_MAX_RETRY_ATTEMPTS}: {str(ex)[:400]}"
                        await db.commit()
                        logger.error(f"Error retrying order #{u_ord.id}: {ex}")

                # 2. Sync status of active orders
                stmt = select(Order.id).where(
                    Order.status.in_(["PENDING", "PROCESSING"]),
                    Order.external_order_id.isnot(None)
                ).limit(50)
                res = await db.execute(stmt)
                order_ids = res.scalars().all()

                for order_id in order_ids:
                    try:
                        order = (await db.execute(
                            select(Order).where(Order.id == order_id).with_for_update()
                        )).scalar_one_or_none()
                        if not order:
                            continue
                        provider_obj = None
                        if order.provider_id:
                            p_res = await db.execute(select(Provider).where(Provider.id == order.provider_id))
                            provider_obj = p_res.scalar_one_or_none()

                        adapter = ProviderManager.get_provider(provider_obj)
                        status_info = await adapter.get_order_status(order.external_order_id)

                        if status_info.status != order.status:
                            old_status = order.status
                            order.status = status_info.status
                            order.start_count = status_info.start_count
                            order.remains = status_info.remains

                            if status_info.status == "COMPLETED":
                                order.completed_at = datetime.utcnow()
                                order.remains = 0
                            elif status_info.status == "CANCELED":
                                # Auto refund full amount to user
                                u_res = await db.execute(select(User).where(User.id == order.user_id).with_for_update())
                                user = u_res.scalar_one_or_none()
                                previous_refund = await db.execute(
                                    select(Transaction.id).where(
                                        Transaction.type == "REFUND",
                                        Transaction.reference.in_([
                                            f"CANCEL-ORDER-{order.id}",
                                            f"REFUND-ORDER-{order.id}",
                                            f"ADMIN-CANCEL-{order.id}",
                                            f"AUTO-REFUND-CANCEL-{order.id}",
                                            f"AUTO-REFUND-PARTIAL-{order.id}",
                                        ])
                                    )
                                )
                                if user and previous_refund.scalar_one_or_none() is None:
                                    refund_amt = order.price
                                    b_before = user.balance
                                    user.balance += refund_amt
                                    tx = Transaction(
                                        user_id=user.id,
                                        type="REFUND",
                                        amount=refund_amt,
                                        balance_before=b_before,
                                        balance_after=user.balance,
                                        reference=f"AUTO-REFUND-CANCEL-{order.id}",
                                        description=f"Hoàn tiền tự động đơn #{order.id} do nhà cung cấp hủy",
                                        status="SUCCESS",
                                        created_at=datetime.utcnow()
                                    )
                                    db.add(tx)
                                    logger.info(f"Auto-refunded {refund_amt:,.0f}đ to user #{user.id} for canceled order #{order.id}")

                            elif status_info.status == "PARTIAL" and order.quantity > 0:
                                # Auto refund partial amount based on remaining unfulfilled units
                                u_res = await db.execute(select(User).where(User.id == order.user_id).with_for_update())
                                user = u_res.scalar_one_or_none()
                                previous_refund = await db.execute(
                                    select(Transaction.id).where(
                                        Transaction.type == "REFUND",
                                        Transaction.reference.in_([
                                            f"CANCEL-ORDER-{order.id}",
                                            f"REFUND-ORDER-{order.id}",
                                            f"ADMIN-CANCEL-{order.id}",
                                            f"AUTO-REFUND-CANCEL-{order.id}",
                                            f"AUTO-REFUND-PARTIAL-{order.id}",
                                        ])
                                    )
                                )
                                if user and status_info.remains > 0 and previous_refund.scalar_one_or_none() is None:
                                    partial_ratio = min(Decimal("1"), max(Decimal("0"), Decimal(status_info.remains) / Decimal(order.quantity)))
                                    refund_amt = (order.price * partial_ratio).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                                    if refund_amt > 0:
                                        b_before = user.balance
                                        user.balance += refund_amt
                                        tx = Transaction(
                                            user_id=user.id,
                                            type="REFUND",
                                            amount=refund_amt,
                                            balance_before=b_before,
                                            balance_after=user.balance,
                                            reference=f"AUTO-REFUND-PARTIAL-{order.id}",
                                            description=f"Hoàn tiền một phần ({status_info.remains:,}/{order.quantity:,}) đơn #{order.id}",
                                            status="SUCCESS",
                                            created_at=datetime.utcnow()
                                        )
                                        db.add(tx)
                                        logger.info(f"Auto-refunded partial {refund_amt:,.0f}đ to user #{user.id} for partial order #{order.id}")

                            await db.commit()
                            logger.info(f"Order #{order.id} (External #{order.external_order_id}) status updated from {old_status} to {order.status}")
                    except Exception as err:
                        logger.error(f"Error syncing order #{order_id}: {err}")
            except Exception as e:
                logger.error(f"Worker iteration error: {e}")

    async def sync_refill_requests(self):
        """Retry accepted refill submissions and poll them to a terminal state."""
        async with AsyncSessionLocal() as db:
            refill_ids = (await db.execute(
                select(RefillRequest.id)
                .where(RefillRequest.status.in_(["PENDING", "PROCESSING"]))
                .order_by(
                    case((RefillRequest.status == "PENDING", 0), else_=1),
                    RefillRequest.updated_at.asc(),
                    RefillRequest.id.asc(),
                )
                .limit(50)
            )).scalars().all()
            for refill_id in refill_ids:
                try:
                    refill = (await db.execute(
                        select(RefillRequest).where(RefillRequest.id == refill_id).with_for_update()
                    )).scalar_one_or_none()
                    if not refill:
                        continue
                    order = (await db.execute(
                        select(Order).where(Order.id == refill.order_id)
                    )).scalar_one_or_none()
                    if not order or not order.external_order_id:
                        refill.status = "REJECTED"
                    else:
                        provider = None
                        if order.provider_id:
                            provider = (await db.execute(
                                select(Provider).where(Provider.id == order.provider_id)
                            )).scalar_one_or_none()
                        adapter = ProviderManager.get_provider(provider)
                        if refill.status == "PENDING":
                            accepted = await adapter.refill_order(order.external_order_id)
                            ext_refill_id = accepted.get("refill_id")
                            if ext_refill_id:
                                refill.external_refill_id = str(ext_refill_id)
                                refill.status = "PROCESSING"
                            elif accepted.get("error") or str(accepted.get("status", "")).lower() in {"error", "rejected"}:
                                refill.status = "REJECTED"
                        elif refill.external_refill_id and hasattr(adapter, "get_refill_status"):
                            result = await adapter.get_refill_status(refill.external_refill_id)
                            provider_status = str(result.get("status", "PROCESSING")).upper()
                            if provider_status in {"COMPLETED", "COMPLETE", "SUCCESS"}:
                                refill.status = "COMPLETED"
                            elif provider_status in {"REJECTED", "CANCELED", "CANCELLED", "FAILED", "ERROR"}:
                                refill.status = "REJECTED"
                    await db.commit()
                except Exception as exc:
                    await db.rollback()
                    logger.warning("Refill request #%s sync failed: %s", refill_id, exc)

    async def run_loop(self):
        self.is_running = True
        logger.info("Order background worker started.")
        while self.is_running:
            await self._run_monitored("bank_sync", self.sync_bank_transactions)
            await self._run_monitored("provider_service_sync", self.sync_provider_services)
            await self._run_monitored("order_sync", self.sync_pending_orders)
            await self._run_monitored("refill_sync", self.sync_refill_requests)
            await self._run_monitored("maintenance_cleanup", self.cleanup_expired_records)
            await asyncio.sleep(self.check_interval)

    def stop(self):
        self.is_running = False

order_worker = OrderWorker()
