"""Outbound email delivery with SMTP credentials sourced only from environment."""

import asyncio
import logging
import smtplib
from email.message import EmailMessage
from urllib.parse import quote

from app.config.settings import settings

logger = logging.getLogger(__name__)


class EmailNotifier:
    @staticmethod
    def configured() -> bool:
        return bool(settings.SMTP_HOST and settings.SMTP_FROM_EMAIL and settings.PUBLIC_APP_URL)

    @classmethod
    async def send_password_reset(cls, recipient: str, token: str) -> bool:
        if not cls.configured():
            logger.warning("Password reset email delivery is not configured")
            return False
        url = f"{settings.PUBLIC_APP_URL.rstrip('/')}/reset-password?token={quote(token)}"
        message = EmailMessage()
        message["Subject"] = "Đặt lại mật khẩu TangLike"
        message["From"] = settings.SMTP_FROM_EMAIL
        message["To"] = recipient
        message.set_content(
            f"Mở liên kết sau để đặt lại mật khẩu (có hiệu lực trong 30 phút):\n{url}\n\n"
            "Nếu bạn không yêu cầu thao tác này, hãy bỏ qua email này."
        )
        try:
            await asyncio.to_thread(cls._send, message)
            return True
        except Exception:
            logger.exception("Unable to deliver password reset email")
            return False

    @classmethod
    async def send_email_verification(cls, recipient: str, token: str) -> bool:
        if not cls.configured():
            logger.warning("Email verification delivery is not configured")
            return False
        url = f"{settings.PUBLIC_APP_URL.rstrip('/')}/verify-email?token={quote(token)}"
        message = EmailMessage()
        message["Subject"] = "Xác minh email TangLike"
        message["From"] = settings.SMTP_FROM_EMAIL
        message["To"] = recipient
        message.set_content(
            f"Mở liên kết sau để xác minh email (có hiệu lực trong 24 giờ):\n{url}\n\n"
            "Nếu bạn không tạo tài khoản này, hãy bỏ qua email này."
        )
        try:
            await asyncio.to_thread(cls._send, message)
            return True
        except Exception:
            logger.exception("Unable to deliver email verification")
            return False

    @staticmethod
    def _send(message: EmailMessage) -> None:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as client:
            if settings.SMTP_STARTTLS:
                client.starttls()
            if settings.SMTP_USERNAME:
                client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            client.send_message(message)
