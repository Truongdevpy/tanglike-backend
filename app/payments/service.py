import math
import uuid
from datetime import datetime
import json
from decimal import Decimal
import logging
from typing import Optional, Dict, Any
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.all import Payment, User, Transaction, Notification, SystemSetting
from app.config.settings import settings
from app.payments.helpers import VietQRHelper

logger = logging.getLogger(__name__)

class PaymentService:
    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def get_default_banking_config() -> dict:
        return {
            "enabled": False,
            "api_type": "thueapi",
            "bank_name": settings.BANK_NAME or "MB Bank",
            "bank_code": "MBBANK",
            "bank_bin": "970422",
            "account_number": settings.BANK_ACCOUNT_NO or "",
            "account_name": settings.BANK_ACCOUNT_HOLDER or "",
            "token": settings.MB_BANK_API_TOKEN or "",
            "internal_mb_username": "",
            "internal_mb_password": "",
            "content_prefix": settings.DEPOSIT_CONTENT_PREFIX or "NAP",
            "min_deposit": int(settings.MIN_DEPOSIT_AMOUNT or 10000),
            "vietqr_template": "compact2",
            "cron_secret": "",
        }

    async def get_banking_config(self) -> dict:
        config = self.get_default_banking_config()
        try:
            res = await self.db.execute(select(SystemSetting).where(SystemSetting.key == "banking_config"))
            row = res.scalar_one_or_none()
            if row and row.value:
                stored = json.loads(row.value)
                if isinstance(stored, dict):
                    config.update(stored)
        except Exception as exc:
            logger.warning("Error reading banking_config: %s", exc)
        return config

    async def create_deposit_request(self, user: User, amount: float, method: str = "VIETQR") -> Dict[str, Any]:
        amount = Decimal(str(amount))
        cfg = await self.get_banking_config()
        min_dep = Decimal(str(cfg.get("min_deposit") or settings.MIN_DEPOSIT_AMOUNT or 10000))
        if amount < min_dep:
            raise ValueError(f"Số tiền nạp tối thiểu là {int(min_dep):,}đ")

        prefix = (cfg.get("content_prefix") or settings.DEPOSIT_CONTENT_PREFIX or "NAP").strip().upper()
        # Unique transaction memo: NAP <USERNAME> <TIMESTAMP_HEX>
        code_suffix = hex(int(datetime.utcnow().timestamp()))[2:].upper()
        rnd = uuid.uuid4().hex[:4].upper()
        transaction_code = f"{prefix} {user.username.upper()} {code_suffix}{rnd}"

        payment = Payment(
            user_id=user.id,
            amount=amount,
            payment_method=method,
            transaction_code=transaction_code,
            status="PENDING",
            created_at=datetime.utcnow()
        )
        self.db.add(payment)
        await self.db.commit()
        await self.db.refresh(payment)

        bank_name = (cfg.get("bank_name") or settings.BANK_NAME or "MB Bank").strip()
        bank_code = (cfg.get("bank_code") or "MBBANK").strip()
        account_no = (cfg.get("account_number") or settings.BANK_ACCOUNT_NO or "").strip()
        account_holder = (cfg.get("account_name") or settings.BANK_ACCOUNT_HOLDER or "").strip()
        template = (cfg.get("vietqr_template") or "compact2").strip()

        qr_url = VietQRHelper.generate_qr_url(
            bank_name=bank_name,
            bank_code=bank_code,
            account_no=account_no,
            amount=float(amount),
            memo=transaction_code,
            template=template,
            account_holder=account_holder
        )

        return {
            "id": payment.id,
            "payment_method": payment.payment_method,
            "amount": payment.amount,
            "transaction_code": payment.transaction_code,
            "bank_name": bank_name,
            "bank_account_no": account_no,
            "bank_account_holder": account_holder,
            "qr_url": qr_url,
            "status": payment.status,
            "created_at": payment.created_at
        }

    async def process_webhook(
        self,
        code: str,
        amount: float,
        gateway_reference: Optional[str] = None,
        raw_data: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Idempotent payment webhook processor.
        """
        try:
            val_amt = float(amount)
            if not math.isfinite(val_amt) or val_amt <= 0:
                raise ValueError("Số tiền nạp phải là số dương hợp lệ.")
        except (ValueError, TypeError):
            raise ValueError("Số tiền nạp không hợp lệ.")
        amount = Decimal(str(amount))
        MAX_DEPOSIT_LIMIT = Decimal("1000000000")  # 1 billion VND limit
        if amount > MAX_DEPOSIT_LIMIT:
            raise ValueError("Số tiền nạp vượt quá giới hạn giao dịch tối đa (tối đa 1,000,000,000đ).")
        if settings.APP_ENV.lower() == "production" and not gateway_reference:
            raise ValueError("Thiếu mã giao dịch duy nhất từ ngân hàng/payment gateway.")

        # A replay from the bank must never credit a second payment request.
        if gateway_reference:
            duplicate = await self.db.execute(
                select(Payment).where(Payment.gateway_reference == gateway_reference)
            )
            previous = duplicate.scalar_one_or_none()
            if previous:
                return {"status": "already_processed", "message": "Giao dịch ngân hàng đã được xử lý", "payment_id": previous.id}

        # Search payment matching transaction_code
        clean_code = code.strip()
        result = await self.db.execute(
            select(Payment).where(Payment.transaction_code == clean_code).with_for_update()
        )
        payment = result.scalar_one_or_none()

        if not payment:
            # Fallback search if memo contains transaction code (ignoring spaces)
            all_pending = await self.db.execute(select(Payment).where(Payment.status == "PENDING"))
            clean_compact = "".join(c for c in clean_code.upper() if c.isalnum())
            candidates = []
            for p in all_pending.scalars().all():
                p_compact = "".join(c for c in p.transaction_code.upper() if c.isalnum())
                if len(p_compact) >= 6 and p_compact in clean_compact:
                    candidates.append((p_compact == clean_compact, p.amount == amount, len(p_compact), p))
            if candidates:
                candidates.sort(key=lambda c: (c[0], c[1], c[2]), reverse=True)
                payment = candidates[0][3]

        if not payment:
            # Direct transfer support: extract username or user_id from prefix (e.g. NAP DEMO or NAPDEMO)
            import re
            cfg = await self.get_banking_config()
            prefix = (cfg.get("content_prefix") or settings.DEPOSIT_CONTENT_PREFIX or "NAP").strip().upper()
            clean_upper = clean_code.upper()
            matched_user = None

            # Pattern: prefix followed by optional whitespace and username/id
            pattern = r'(?:^|[^A-Z0-9])' + re.escape(prefix) + r'\s*([A-Za-z0-9_]{3,32})'
            match = re.search(pattern, clean_upper)
            if match:
                identifier = match.group(1).lower()
                u_res = await self.db.execute(
                    select(User).where(
                        func.lower(User.username) == identifier,
                        User.status == "ACTIVE",
                        User.is_deleted == False
                    )
                )
                matched_user = u_res.scalar_one_or_none()
                if not matched_user and identifier.isdigit():
                    u_id_res = await self.db.execute(
                        select(User).where(
                            User.id == int(identifier),
                            User.status == "ACTIVE",
                            User.is_deleted == False
                        )
                    )
                    matched_user = u_id_res.scalar_one_or_none()

            if matched_user:
                cfg = await self.get_banking_config()
                min_dep = Decimal(str(cfg.get("min_deposit") or settings.MIN_DEPOSIT_AMOUNT or 10000))
                if amount < min_dep:
                    raise ValueError(f"Số tiền nạp tối thiểu là {int(min_dep):,}đ")
                code_suffix = hex(int(datetime.utcnow().timestamp()))[2:].upper()
                rnd = uuid.uuid4().hex[:6].upper()
                payment = Payment(
                    user_id=matched_user.id,
                    amount=amount,
                    payment_method="BANK_TRANSFER",
                    transaction_code=f"{prefix} {matched_user.username.upper()} {code_suffix}{rnd}",
                    status="PENDING",
                    created_at=datetime.utcnow()
                )
                self.db.add(payment)
                await self.db.flush()

        if not payment:
            raise ValueError(f"Không tìm thấy yêu cầu nạp tiền cho mã: {code}")

        # Idempotency check: Don't process twice
        if payment.status == "COMPLETED":
            return {"status": "already_processed", "message": "Giao dịch đã được xử lý trước đó", "payment_id": payment.id}

        # Check amount tolerance (allow exact or greater)
        if amount < payment.amount:
            raise ValueError(f"Số tiền nạp ({amount}) không khớp với đơn ({payment.amount})")

        # Atomic user balance credit and ledger transaction
        user_res = await self.db.execute(select(User).where(User.id == payment.user_id).with_for_update())
        user = user_res.scalar_one_or_none()
        if not user or user.status != "ACTIVE" or user.is_deleted:
            raise ValueError("Người dùng không tồn tại hoặc tài khoản đã bị khóa/xóa")

        balance_before = user.balance
        balance_after = balance_before + amount
        user.balance = balance_after

        payment.status = "COMPLETED"
        payment.gateway_reference = gateway_reference
        payment.completed_at = datetime.utcnow()
        if raw_data:
            payment.raw_payload = json.dumps(raw_data)

        # Create audit transaction record
        tx = Transaction(
            user_id=user.id,
            type="DEPOSIT",
            amount=amount,
            balance_before=balance_before,
            balance_after=balance_after,
            reference=f"PAY-{gateway_reference or payment.id}",
            description=f"Nạp tiền tự động qua {payment.payment_method} ({payment.transaction_code})",
            status="SUCCESS",
            created_at=datetime.utcnow()
        )
        self.db.add(tx)

        # Create user notification
        notif = Notification(
            user_id=user.id,
            title="Nạp tiền thành công",
            message=f"Tài khoản của bạn đã được cộng {int(amount):,}đ. Số dư hiện tại: {int(balance_after):,}đ",
            type="SUCCESS",
            created_at=datetime.utcnow()
        )
        self.db.add(notif)

        payment_id = payment.id


        try:


            await self.db.commit()


        except IntegrityError:


            # Concurrent deliveries are also stopped by the database constraint.


            await self.db.rollback()


            return {"status": "already_processed", "message": "Giao dịch ngân hàng đã được xử lý", "payment_id": payment_id}

        return {
            "status": "success",
            "id": payment.id,
            "payment_method": payment.payment_method,
            "user_id": user.id,
            "username": user.username,
            "amount": amount,
            "balance_after": balance_after,
            "transaction_code": payment.transaction_code
        }
