"""Bank polling orchestration with durable idempotency and retry state."""

import asyncio
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from typing import Any, Tuple, List

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.all import BankTransaction
from app.payments.mbbank import MBBankTransactionSource, BankTransactionPayload
from app.payments.service import PaymentService

logger = logging.getLogger(__name__)


class BankSyncService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _fetch_internal_transactions(self, config: dict) -> Tuple[List[BankTransactionPayload], str | None]:
        username = (config.get("internal_mb_username") or "").strip()
        password = (config.get("internal_mb_password") or "").strip()
        account_no = (config.get("account_number") or "").strip()

        if not username or not password or not account_no:
            return [], "Chưa nhập đầy đủ Tên đăng nhập, Mật khẩu và Số tài khoản MBBank nội bộ."

        script_candidates = [
            os.path.join(os.path.dirname(__file__), "mbbank_cli", "cli_get_transactions.py"),
            os.getenv("MBBANK_SYNC_SCRIPT", ""),
        ]
        chosen_script = None
        for path in script_candidates:
            if path and os.path.exists(path):
                chosen_script = path
                break

        if not chosen_script:
            return [], "Chưa tìm thấy script đồng bộ nội bộ mbbank_sync/cli_get_transactions.py."

        import shutil
        if not shutil.which("node"):
            return [], (
                "Máy chủ Cloud (Render) đang chạy môi trường Python không có sẵn NodeJS để giải mã WASM của MBBank App. "
                "Vui lòng đổi Loại API sang 'ThueAPI / Webhook' để nạp tiền tự động ổn định 24/7!"
            )

        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tf:
            cfg_path = tf.name
            json.dump({
                "username": username,
                "password": password,
                "account_no": account_no,
            }, tf)

        try:
            cwd = os.path.dirname(chosen_script)
            proc = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, chosen_script, "--config-file", cfg_path],
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60
            )
            raw_out = proc.stdout.strip()
            if proc.returncode != 0 or not raw_out:
                err_text = (proc.stderr or "").strip()
                if not err_text:
                    err_text = "Kh?ng c? d? li?u tr? v? t? script"
                else:
                    lines = [ln.strip() for ln in err_text.splitlines() if ln.strip()]
                    err_text = lines[-1] if lines else err_text[-250:]
                return [], f"L?i ch?y script MBBank n?i b?: {err_text}"
            data = json.loads(raw_out)
        except Exception as exc:
            return [], f"Không thể thực thi script MBBank nội bộ: {str(exc)[:200]}"
        finally:
            try:
                os.remove(cfg_path)
            except OSError:
                pass

        if data.get("status") == "error":
            raw_err = data.get("message") or "Lỗi đăng nhập hoặc truy vấn MBBank nội bộ"
            if "GW21" in raw_err:
                clean_err = f"Đã tự động giải mã Captcha bằng AI model (ONNX) thành công. MBBank phản hồi: Tên đăng nhập hoặc mật khẩu MBBank chưa chính xác (Lỗi GW21: Customer is invalid). Vui lòng kiểm tra lại mật khẩu tài khoản {username}."
            elif "GW266" in raw_err:
                clean_err = "MBBank yêu cầu xác thực OTP trên App điện thoại cho thiết bị mới (Lỗi GW266). Vui lòng mở App MBBank để xác thực."
            elif "GW283" in raw_err:
                clean_err = "MBBank từ chối mã Captcha. Hệ thống sẽ tự động dùng model AI để giải lại ở chu kỳ tiếp theo."
            else:
                clean_err = raw_err
            return [], clean_err

        parsed: List[BankTransactionPayload] = []
        for tx in data.get("transactions", []):
            if tx.get("type") == "OUT":
                continue
            tx_id = str(tx.get("transactionID") or tx.get("refNo") or "").strip()
            amount = float(tx.get("amount") or 0)
            content = str(tx.get("description") or "").strip()
            if tx_id and amount > 0:
                parsed.append(BankTransactionPayload(transaction_id=tx_id, amount=amount, content=content, occurred_at=None, raw=tx))
        return parsed, None

    async def sync_mbbank(self) -> dict[str, Any]:
        config = await PaymentService(self.db).get_banking_config()
        # Enabled check (allow fallback to env if explicitly configured)
        is_enabled = bool(config.get("enabled"))
        has_env_credentials = bool(settings.MB_BANK_API_URL and settings.MB_BANK_API_TOKEN)

        if not is_enabled and not has_env_credentials:
            return {
                "seen": 0,
                "processed": 0,
                "ignored": 0,
                "failed": 0,
                "message": "Banking tự động đang tắt. Hãy bật trong Cấu hình Banking để tự động đồng bộ."
            }

        api_type = config.get("api_type", "thueapi")
        incoming_txs: List[BankTransactionPayload] = []
        error_msg: str | None = None

        if api_type == "internal":
            incoming_txs, error_msg = await self._fetch_internal_transactions(config)
        else:
            token = config.get("token") or settings.MB_BANK_API_TOKEN or ""
            api_url = settings.MB_BANK_API_URL or ""
            source = MBBankTransactionSource(api_url=api_url, api_token=token)
            try:
                incoming_txs = await source.fetch_transactions()
            except Exception as exc:
                error_msg = str(exc)

        if error_msg and not incoming_txs:
            return {
                "ok": False,
                "seen": 0,
                "processed": 0,
                "credited": 0,
                "ignored": 0,
                "failed": 0,
                "duplicates": 0,
                "error": error_msg,
                "message": f"Không thể lấy giao dịch từ ngân hàng: {error_msg}",
            }

        result = {
            "seen": 0,
            "processed": 0,
            "credited": 0,
            "ignored": 0,
            "failed": 0,
            "duplicates": 0,
        }
        prefix = (config.get("content_prefix") or settings.DEPOSIT_CONTENT_PREFIX or "NAP").strip().upper()
        bank_code = (config.get("bank_code") or "MBBANK").strip()

        for incoming in incoming_txs:
            result["seen"] += 1
            existing = await self.db.execute(
                select(BankTransaction).where(BankTransaction.bank_transaction_id == incoming.transaction_id)
            )
            bank_tx = existing.scalar_one_or_none()
            if bank_tx and bank_tx.status in {"PROCESSED", "IGNORED"}:
                result["duplicates"] += 1
                continue
            if not bank_tx:
                bank_tx = BankTransaction(
                    provider=bank_code,
                    bank_transaction_id=incoming.transaction_id,
                    amount=incoming.amount,
                    content=incoming.content,
                    occurred_at=incoming.occurred_at,
                    raw_payload=json.dumps(incoming.raw, ensure_ascii=False),
                    status="PENDING",
                )
                self.db.add(bank_tx)
                try:
                    await self.db.flush()
                except IntegrityError:
                    await self.db.rollback()
                    result["duplicates"] += 1
                    continue

            # Ignore unrelated incoming money while retaining an auditable record.
            clean_content = "".join(c for c in incoming.content.upper() if c.isalnum())
            clean_prefix = "".join(c for c in prefix if c.isalnum())
            if clean_prefix and clean_prefix not in clean_content:
                bank_tx.status = "IGNORED"
                bank_tx.error_message = "Không chứa tiền tố nạp tiền hợp lệ"
                await self.db.commit()
                result["ignored"] += 1
                continue

            try:
                payment_result = await PaymentService(self.db).process_webhook(
                    code=incoming.content,
                    amount=incoming.amount,
                    gateway_reference=incoming.transaction_id,
                    raw_data=incoming.raw,
                )
                existing_tx = await self.db.execute(
                    select(BankTransaction).where(BankTransaction.bank_transaction_id == incoming.transaction_id)
                )
                current_bank_tx = existing_tx.scalar_one_or_none() or bank_tx
                status_val = payment_result.get("status")
                is_success = status_val in {"success", "already_processed"}
                current_bank_tx.status = "PROCESSED" if is_success else "FAILED"
                current_bank_tx.processed_at = datetime.utcnow()
                current_bank_tx.error_message = None
                await self.db.commit()
                if status_val == "success":
                    result["credited"] += 1
                elif status_val == "already_processed":
                    result["duplicates"] += 1
            except Exception as exc:
                await self.db.rollback()
                existing_tx = await self.db.execute(
                    select(BankTransaction).where(BankTransaction.bank_transaction_id == incoming.transaction_id)
                )
                current_bank_tx = existing_tx.scalar_one_or_none()
                if not current_bank_tx:
                    current_bank_tx = BankTransaction(
                        provider=bank_code,
                        bank_transaction_id=incoming.transaction_id,
                        amount=incoming.amount,
                        content=incoming.content,
                        occurred_at=incoming.occurred_at,
                        raw_payload=json.dumps(incoming.raw, ensure_ascii=False),
                        status="FAILED",
                        error_message=str(exc)[:500],
                    )
                    self.db.add(current_bank_tx)
                else:
                    current_bank_tx.status = "FAILED"
                    current_bank_tx.error_message = str(exc)[:500]
                await self.db.commit()
                result["failed"] += 1
                logger.warning("Bank transaction %s failed: %s", incoming.transaction_id, exc)

        result["processed"] = result["seen"]
        result["ok"] = True
        result["message"] = "Đã đồng bộ giao dịch ngân hàng."
        return result

    async def test_fetch_transactions(self, custom_config: dict | None = None) -> dict[str, Any]:
        """Fetch bank transactions on-demand for diagnostic/testing purposes without crediting money."""
        config = await PaymentService(self.db).get_banking_config()
        if custom_config and isinstance(custom_config, dict):
            for k, v in custom_config.items():
                if v is not None and v != "":
                    config[k] = v

        api_type = str(config.get("api_type") or "internal").lower()
        bank_name = str(config.get("bank_name") or "MB Bank")
        bank_code = str(config.get("bank_code") or "MBBANK")
        account_number = str(config.get("account_number") or "")
        account_name = str(config.get("account_name") or "")
        username = str(config.get("internal_mb_username") or "")
        content_prefix = str(config.get("content_prefix") or "NAP").strip().upper()

        incoming_txs: List[BankTransactionPayload] = []
        error_msg: str | None = None
        source_name = "internal_script" if api_type == "internal" else "thueapi"

        if api_type == "internal":
            incoming_txs, error_msg = await self._fetch_internal_transactions(config)
        else:
            token = config.get("token") or settings.MB_BANK_API_TOKEN or ""
            api_url = settings.MB_BANK_API_URL or ""
            source = MBBankTransactionSource(api_url=api_url, api_token=token)
            try:
                incoming_txs = await source.fetch_transactions()
            except Exception as exc:
                error_msg = str(exc)

        parsed_txs = []
        clean_prefix = "".join(c for c in content_prefix if c.isalnum())
        for tx in incoming_txs:
            clean_content = "".join(c for c in tx.content.upper() if c.isalnum())
            is_valid_prefix = bool(clean_prefix and clean_prefix in clean_content)
            parsed_txs.append({
                "transaction_id": tx.transaction_id,
                "amount": tx.amount,
                "content": tx.content,
                "occurred_at": tx.occurred_at.isoformat() if tx.occurred_at else None,
                "is_valid_prefix": is_valid_prefix,
                "raw": tx.raw,
            })

        success = len(parsed_txs) > 0 or (error_msg is None)
        if error_msg:
            message = f"Không thể lấy giao dịch từ ngân hàng: {error_msg}"
        elif len(parsed_txs) > 0:
            message = f"Kết nối thành công! Đã lấy được {len(parsed_txs)} giao dịch từ tài khoản {account_number}."
        else:
            message = f"Kết nối thành công với tài khoản {account_number}! Hiện chưa có giao dịch nhận tiền mới trong lịch sử gần đây."

        return {
            "success": success,
            "api_type": api_type,
            "source": source_name,
            "bank_name": bank_name,
            "bank_code": bank_code,
            "account_number": account_number,
            "account_name": account_name,
            "username": username,
            "content_prefix": content_prefix,
            "total_found": len(parsed_txs),
            "transactions": parsed_txs,
            "error": error_msg,
            "message": message,
            "tested_at": datetime.utcnow().isoformat(),
        }
