import logging
import html
from typing import Optional
from datetime import datetime
import httpx
from app.config.settings import settings

logger = logging.getLogger(__name__)

class TelegramNotifier:
    @classmethod
    async def get_telegram_config(cls) -> tuple[Optional[str], Optional[str]]:
        bot_token = settings.TELEGRAM_BOT_TOKEN
        chat_id = settings.TELEGRAM_ADMIN_CHAT_ID
        try:
            from app.database.session import AsyncSessionLocal
            from app.models.all import SystemSetting
            from sqlalchemy import select
            async with AsyncSessionLocal() as db:
                res = await db.execute(
                    select(SystemSetting).where(
                        SystemSetting.key.in_(["telegram_bot_token", "telegram_admin_chat_id", "telegram_chat_id"])
                    )
                )
                rows = res.scalars().all()
                for r in rows:
                    if r.key == "telegram_bot_token" and r.value and str(r.value).strip():
                        bot_token = str(r.value).strip()
                    elif r.key in ("telegram_admin_chat_id", "telegram_chat_id") and r.value and str(r.value).strip():
                        chat_id = str(r.value).strip()
        except Exception as e:
            logger.debug(f"Could not load telegram settings from db: {e}")
        return bot_token, chat_id

    @classmethod
    async def send_message(cls, text: str) -> bool:
        bot_token, chat_id = await cls.get_telegram_config()
        if not bot_token or not chat_id:
            logger.info(f"[TELEGRAM NOTIFICATION (Simulated / No Credentials)]: {text}")
            return True

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML"
        }
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.post(url, json=payload)
                return res.status_code == 200
        except Exception as e:
            err_str = str(e)
            if bot_token and bot_token in err_str:
                err_str = err_str.replace(bot_token, "••••••••••••")
            logger.warning(f"Failed to send Telegram notification: {err_str}")
            return False

    @classmethod
    async def notify_new_user(cls, username: str, email: str):
        username = html.escape(str(username))
        email = html.escape(str(email))
        text = (
            f"👤 <b>USER MỚI ĐĂNG KÝ</b>\n\n"
            f"<b>Username:</b> {username}\n"
            f"<b>Email:</b> {email}\n"
            f"<b>Thời gian:</b> Vừa xong"
        )
        await cls.send_message(text)

    @classmethod
    async def notify_new_order(cls, order_id: int, service_name: str, quantity: int, amount: float, username: str):
        service_name = html.escape(str(service_name))
        username = html.escape(str(username))
        text = (
            f"📦 <b>ĐƠN HÀNG MỚI</b>\n\n"
            f"<b>Mã đơn:</b> #{order_id}\n"
            f"<b>Khách hàng:</b> {username}\n"
            f"<b>Dịch vụ:</b> {service_name}\n"
            f"<b>Số lượng:</b> {quantity:,}\n"
            f"<b>Số tiền:</b> {int(amount):,}đ"
        )
        await cls.send_message(text)

    @classmethod
    async def notify_deposit(cls, username: str, amount: float, transaction_code: str):
        username = html.escape(str(username))
        transaction_code = html.escape(str(transaction_code))
        text = (
            f"💰 <b>NẠP TIỀN THÀNH CÔNG</b>\n\n"
            f"<b>Khách hàng:</b> {username}\n"
            f"<b>Số tiền:</b> {int(amount):,}đ\n"
            f"<b>Mã GD:</b> {transaction_code}"
        )
        await cls.send_message(text)

    @classmethod
    async def notify_provider_error(cls, provider_name: str, service_name: str, error: str):
        provider_name = html.escape(str(provider_name))
        service_name = html.escape(str(service_name))
        error = html.escape(str(error))
        text = (
            f"🚨 <b>CẢNH BÁO LỖI PROVIDER</b>\n\n"
            f"<b>Nhà cung cấp:</b> {provider_name}\n"
            f"<b>Dịch vụ:</b> {service_name}\n"
            f"<b>Chi tiết:</b> {error}"
        )
        await cls.send_message(text)

    @classmethod
    async def notify_new_ticket(cls, username: str, subject: str):
        username = html.escape(str(username))
        subject = html.escape(str(subject))
        text = (
            f"🎧 <b>TICKET HỖ TRỢ MỚI</b>\n\n"
            f"<b>Khách hàng:</b> {username}\n"
            f"<b>Chủ đề:</b> {subject}"
        )
        await cls.send_message(text)

    @classmethod
    async def notify_live_chat(cls, sender: str, message: str, email: Optional[str] = None, balance: Optional[float] = None):
        sender = html.escape(str(sender))
        message = html.escape(str(message))
        if email:
            email = html.escape(str(email))
        user_info = f"<b>Người gửi:</b> {sender}"
        if email:
            user_info += f"\n<b>Email:</b> {email}"
        if balance is not None:
            user_info += f"\n<b>Số dư:</b> {int(balance):,}đ"

        text = (
            f"💬 <b>TIN NHẮN CHAT TỪ WEBSITE</b>\n\n"
            f"{user_info}\n"
            f"<b>Thời gian:</b> {datetime.utcnow().strftime('%H:%M:%S - %d/%m/%Y')}\n\n"
            f"<b>Nội dung tin nhắn:</b>\n"
            f"{message}"
        )
        return await cls.send_message(text)
