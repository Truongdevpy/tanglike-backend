import hashlib
import hmac
import secrets
import time
import urllib.parse
from datetime import datetime
from typing import Optional, Dict, Any, Tuple
from app.config.settings import settings


class VietQRHelper:
    @staticmethod
    def generate_qr_url(
        bank_name: str = "MB Bank",
        account_no: str = "",
        amount: float = 0,
        memo: str = "",
        template: str = "compact2",
        account_holder: str = "",
        bank_code: str = ""
    ) -> str:
        # VietQR standard quicklink format
        code = "".join(c for c in (bank_code or bank_name or "MBBANK") if c.isalnum() or c == "_")
        amt_int = int(amount) if amount > 0 else 0
        clean_memo = urllib.parse.quote(memo.strip(), safe="")
        clean_account = "".join(c for c in account_no if c.isalnum())
        clean_template = "".join(c for c in (template or "compact2") if c.isalnum() or c == "_")
        url = f"https://img.vietqr.io/image/{code}-{clean_account}-{clean_template}.png?amount={amt_int}&addInfo={clean_memo}"
        if account_holder and account_holder.strip():
            url += f"&accountName={urllib.parse.quote(account_holder.strip(), safe='')}"
        return url


class NonceTracker:
    def __init__(self, ttl_seconds: int = 600):
        self.seen: Dict[str, float] = {}
        self.ttl = ttl_seconds

    def check_and_record(self, nonce: str) -> bool:
        if not nonce or not isinstance(nonce, str):
            return False
        clean = nonce.strip()
        if len(clean) < 4 or len(clean) > 128:
            return False
        if any(ord(c) < 32 or ord(c) > 126 for c in clean):
            return False
        now = time.time()
        self.seen = {k: exp for k, exp in self.seen.items() if exp > now}
        if clean in self.seen:
            return False
        self.seen[clean] = now + self.ttl
        return True


nonce_tracker = NonceTracker(ttl_seconds=600)


class WebhookVerifier:
    @staticmethod
    def verify_hmac_signature(raw_bytes: bytes, received_signature: Optional[str], secret: str) -> bool:
        if not secret or not received_signature:
            return False
        clean_sig = received_signature.strip()
        if clean_sig.startswith("sha256="):
            clean_sig = clean_sig[7:].strip()
        expected = hmac.new(
            secret.encode("utf-8"),
            raw_bytes,
            hashlib.sha256
        ).hexdigest()
        return secrets.compare_digest(clean_sig.lower(), expected.lower())

    @staticmethod
    def verify_sepay_signature(payload_str: str, received_signature: str, secret: str) -> bool:
        return WebhookVerifier.verify_hmac_signature(
            payload_str.encode("utf-8"), received_signature, secret
        )

    @staticmethod
    def verify_secret_token(received_token: Optional[str], expected_secret: Optional[str] = None) -> bool:
        sec = expected_secret or settings.PAYMENT_WEBHOOK_SECRET
        if not sec or not received_token:
            return False
        return secrets.compare_digest(str(received_token).strip(), str(sec).strip())

    @staticmethod
    def verify_generic_secret(received_secret: Optional[str]) -> bool:
        return WebhookVerifier.verify_secret_token(received_secret)

    @staticmethod
    def verify_timestamp(timestamp_val: Any, max_age_seconds: int = 300) -> Tuple[bool, Optional[str]]:
        if timestamp_val is None:
            return True, None
        try:
            now = time.time()
            if isinstance(timestamp_val, (int, float)):
                ts = float(timestamp_val)
                if ts > 1e11:  # milliseconds
                    ts /= 1000.0
            elif isinstance(timestamp_val, str):
                clean_ts = timestamp_val.strip()
                if clean_ts.replace(".", "", 1).isdigit():
                    ts = float(clean_ts)
                    if ts > 1e11:
                        ts /= 1000.0
                else:
                    dt = datetime.fromisoformat(clean_ts.replace("Z", "+00:00"))
                    ts = dt.timestamp()
            else:
                return False, "Định dạng timestamp không hợp lệ"

            diff = abs(now - ts)
            if diff > max_age_seconds:
                return False, f"Timestamp lệch quá giới hạn cho phép ({int(diff)}s > {max_age_seconds}s)"
            return True, None
        except Exception as e:
            return False, f"Lỗi phân tích timestamp: {e}"

    @staticmethod
    def verify_nonce(nonce: Optional[str]) -> Tuple[bool, Optional[str]]:
        if not nonce:
            return True, None
        if not nonce_tracker.check_and_record(nonce):
            return False, "Webhook nonce đã được sử dụng hoặc không hợp lệ (replay protection)."
        return True, None