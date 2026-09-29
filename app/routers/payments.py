import json
import math
import secrets
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Header, Request, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.models.all import Payment, User
from app.schemas.all import ApiResponse, PaymentCreate, PaymentResponse, WebhookPayload
from app.auth.security import get_current_user, get_optional_current_user
from app.payments.service import PaymentService
from app.payments.helpers import WebhookVerifier
from app.notifications.telegram import TelegramNotifier
from app.config.settings import settings

router = APIRouter(prefix="/payments", tags=["Payments & Auto Deposit"])

@router.post("/deposit", response_model=ApiResponse[PaymentResponse])
async def create_deposit(
    payload: PaymentCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    service = PaymentService(db)
    try:
        data = await service.create_deposit_request(
            user=current_user,
            amount=payload.amount,
            method=payload.payment_method
        )
        return ApiResponse(data=PaymentResponse(**data), message="Tạo yêu cầu nạp tiền thành công.")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

@router.get("/bank-info", response_model=ApiResponse[dict])
async def get_public_bank_info(db: AsyncSession = Depends(get_db)):
    """Return public bank deposit instructions and account details."""
    cfg = await PaymentService(db).get_banking_config()
    data = {
        "enabled": bool(cfg.get("enabled")),
        "bank_name": cfg.get("bank_name") or settings.BANK_NAME or "MB Bank",
        "bank_code": cfg.get("bank_code") or "MBBANK",
        "bank_bin": cfg.get("bank_bin") or "970422",
        "account_number": cfg.get("account_number") or settings.BANK_ACCOUNT_NO or "",
        "account_name": cfg.get("account_name") or settings.BANK_ACCOUNT_HOLDER or "",
        "content_prefix": cfg.get("content_prefix") or settings.DEPOSIT_CONTENT_PREFIX or "NAP",
        "min_deposit": int(cfg.get("min_deposit") or settings.MIN_DEPOSIT_AMOUNT or 10000),
        "vietqr_template": cfg.get("vietqr_template") or "compact2",
    }
    return ApiResponse(data=data)

@router.get("/history", response_model=ApiResponse[List[PaymentResponse]])
async def list_payment_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    cfg = await PaymentService(db).get_banking_config()
    b_name = cfg.get("bank_name") or settings.BANK_NAME or "MB Bank"
    b_no = cfg.get("account_number") or settings.BANK_ACCOUNT_NO or ""
    b_holder = cfg.get("account_name") or settings.BANK_ACCOUNT_HOLDER or ""

    stmt = select(Payment).where(Payment.user_id == current_user.id).order_by(desc(Payment.created_at)).limit(50)
    res = await db.execute(stmt)
    payments = res.scalars().all()

    output = []
    for p in payments:
        resp = PaymentResponse(
            id=p.id,
            amount=p.amount,
            payment_method=p.payment_method,
            transaction_code=p.transaction_code,
            status=p.status,
            bank_name=b_name,
            bank_account_no=b_no,
            bank_account_holder=b_holder,
            created_at=p.created_at
        )
        output.append(resp)

    return ApiResponse(data=output)

@router.get("/status/{transaction_code}", response_model=ApiResponse[dict])
async def check_payment_status(
    transaction_code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Check the status of a specific deposit transaction.
    Returns status: PENDING / COMPLETED, credited amount, current balance, etc.
    """
    clean_code = transaction_code.strip()
    
    stmt = select(Payment).where(
        Payment.user_id == current_user.id,
        (Payment.transaction_code == clean_code) | (Payment.transaction_code.ilike(f"%{clean_code}%"))
    ).order_by(desc(Payment.id)).limit(1)
    
    res = await db.execute(stmt)
    payment = res.scalar_one_or_none()
    
    if not payment and clean_code.isdigit():
        stmt2 = select(Payment).where(
            Payment.user_id == current_user.id,
            Payment.id == int(clean_code)
        )
        res2 = await db.execute(stmt2)
        payment = res2.scalar_one_or_none()

    if not payment:
        raise HTTPException(status_code=404, detail="Không tìm thấy yêu cầu nạp tiền.")

    user_res = await db.execute(select(User).where(User.id == current_user.id))
    fresh_user = user_res.scalar_one_or_none() or current_user
        
    return ApiResponse(
        data={
            "id": payment.id,
            "transaction_code": payment.transaction_code,
            "status": payment.status,
            "is_completed": payment.status == "COMPLETED",
            "amount": float(payment.amount),
            "completed_at": payment.completed_at.isoformat() if payment.completed_at else None,
            "balance": float(fresh_user.balance),
        },
        message="Trạng thái giao dịch"
    )

@router.post("/webhook")
async def payment_webhook(
    request: Request,
    authorization: Optional[str] = Header(None),
    x_secret: Optional[str] = Header(None, alias="X-Secret-Key"),
    x_signature: Optional[str] = Header(None, alias="X-Signature"),
    x_sepay_signature: Optional[str] = Header(None, alias="X-Sepay-Signature"),
    x_hub_signature: Optional[str] = Header(None, alias="X-Hub-Signature-256"),
    x_webhook_signature: Optional[str] = Header(None, alias="X-Webhook-Signature"),
    x_timestamp: Optional[str] = Header(None, alias="X-Webhook-Timestamp"),
    x_timestamp_alt: Optional[str] = Header(None, alias="X-Timestamp"),
    x_nonce: Optional[str] = Header(None, alias="X-Webhook-Nonce"),
    x_event_id: Optional[str] = Header(None, alias="X-Event-Id"),
    x_provider: Optional[str] = Header(None, alias="X-Provider"),
    db: AsyncSession = Depends(get_db)
):
    expected_secret = settings.PAYMENT_WEBHOOK_SECRET
    if not expected_secret:
        raise HTTPException(status_code=401, detail="Webhook secret chưa được cấu hình trên hệ thống.")

    raw_body = await request.body()
    received_sig = x_signature or x_sepay_signature or x_hub_signature or x_webhook_signature

    if received_sig:
        if not WebhookVerifier.verify_hmac_signature(raw_body, received_sig, expected_secret):
            raise HTTPException(status_code=401, detail="Chữ ký webhook (signature) không hợp lệ hoặc dữ liệu payload đã bị thay đổi.")
    else:
        token = x_secret or (authorization.replace("Bearer ", "").replace("Apikey ", "").strip() if authorization else None)
        if not token or not WebhookVerifier.verify_secret_token(token, expected_secret):
            raise HTTPException(status_code=401, detail="Webhook signature/secret không hợp lệ.")

    try:
        body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        if not isinstance(body, dict):
            raise ValueError()
    except Exception:
        raise HTTPException(status_code=400, detail="Dữ liệu payload webhook không hợp lệ (malformed JSON).")

    # Check timestamp replay
    raw_ts = x_timestamp or x_timestamp_alt or body.get("timestamp") or body.get("createdAt")
    if raw_ts is not None:
        valid_ts, ts_err = WebhookVerifier.verify_timestamp(raw_ts, max_age_seconds=300)
        if not valid_ts:
            raise HTTPException(status_code=400, detail=ts_err or "Webhook timestamp đã quá cũ hoặc lệch giờ (replay protection).")

    # Check nonce replay
    raw_nonce = x_nonce or body.get("nonce") or body.get("request_id")
    if raw_nonce is not None:
        valid_nonce, nonce_err = WebhookVerifier.verify_nonce(raw_nonce)
        if not valid_nonce:
            raise HTTPException(status_code=400, detail=nonce_err or "Webhook nonce đã được sử dụng hoặc không hợp lệ (replay protection).")

    # Check provider identity if provided
    raw_provider = x_provider or body.get("provider") or body.get("gateway")
    if raw_provider:
        clean_provider = str(raw_provider).lower().strip()
        ALLOWED_PROVIDERS = {"mbbank", "vietqr", "sepay", "thueapi", "bank", "bank_transfer", "generic"}
        if clean_provider not in ALLOWED_PROVIDERS:
            raise HTTPException(
                status_code=400,
                detail=f"Nhà cung cấp webhook '{raw_provider}' không được hỗ trợ."
            )

    # Check event type
    raw_event = body.get("event") or body.get("event_type") or body.get("type")
    if raw_event:
        clean_event = str(raw_event).lower().strip()
        ALLOWED_PAYMENT_EVENTS = {
            "payment.success", "payment.received", "transaction.incoming",
            "deposit.success", "deposit", "receive", "transfer.success",
            "transfer.incoming", "paid", "success"
        }
        if clean_event not in ALLOWED_PAYMENT_EVENTS:
            raise HTTPException(
                status_code=400,
                detail=f"Sự kiện webhook '{raw_event}' không được hỗ trợ hoặc không phải giao dịch nạp tiền thành công."
            )

    # Check payment status
    raw_st = str(body.get("status") or body.get("payment_status") or body.get("transfer_status") or body.get("state") or "").upper().strip()
    if raw_st in {"FAILED", "CANCELLED", "CANCELED", "ERROR", "REJECTED", "EXPIRED", "DECLINED"}:
        raise HTTPException(status_code=400, detail=f"Giao dịch không ở trạng thái thành công (status: {raw_st}).")
    if body.get("error") is True or body.get("success") is False:
        raise HTTPException(status_code=400, detail="Giao dịch bị đánh dấu thất bại từ payment gateway.")

    code = body.get("content") or body.get("transaction_code") or body.get("code") or body.get("description", "")
    try:
        amount = float(body.get("transferAmount") or body.get("amount") or 0)
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Số tiền nạp không hợp lệ.")

    gateway_reference = str(
        body.get("transaction_id")
        or body.get("transactionId")
        or body.get("reference")
        or body.get("id")
        or body.get("event_id")
        or body.get("eventId")
        or x_event_id
        or ""
    ).strip() or None

    if not code or amount <= 0 or not math.isfinite(amount):
        raise HTTPException(status_code=400, detail="Thiếu thông tin mã giao dịch hoặc số tiền không hợp lệ.")

    service = PaymentService(db)
    try:
        result = await service.process_webhook(
            code=code, amount=amount, gateway_reference=gateway_reference, raw_data=body
        )
        if result.get("status") == "success":
            await TelegramNotifier.notify_deposit(
                username=result["username"],
                amount=result["amount"],
                transaction_code=result["transaction_code"]
            )
        return {"success": True, "message": "Webhook processed successfully", "data": result}
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))

@router.post("/simulate-webhook")
async def simulate_bank_deposit(
    transaction_code: str = Query(..., description="Mã giao dịch nạp tiền"),
    amount: float = Query(..., description="Số tiền chuyển khoản"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if settings.APP_ENV == "production":
        raise HTTPException(
            status_code=403,
            detail="Chức năng mô phỏng nạp tiền bị vô hiệu hóa trên môi trường production."
        )

    if amount <= 0:
        raise HTTPException(status_code=400, detail="Số tiền nạp phải lớn hơn 0.")

    # In dev/testing, regular users can only simulate their own pending payment
    if current_user.role != "ADMIN":
        chk_res = await db.execute(select(Payment).where(Payment.transaction_code == transaction_code.strip()))
        chk_payment = chk_res.scalar_one_or_none()
        if not chk_payment or chk_payment.user_id != current_user.id:
            raise HTTPException(
                status_code=403,
                detail="Bạn chỉ có thể thử nghiệm mô phỏng giao dịch do chính bạn tạo ra."
            )

    service = PaymentService(db)
    try:
        result = await service.process_webhook(
            code=transaction_code,
            amount=amount,
            gateway_reference=f"SIM-{transaction_code}",
            raw_data={"simulated": True, "by_user": current_user.username}
        )
        if result.get("status") == "success":
            await TelegramNotifier.notify_deposit(
                username=result["username"],
                amount=result["amount"],
                transaction_code=result["transaction_code"]
            )
        return ApiResponse(data=result, message="Mô phỏng nạp tiền thành công! Số dư đã được cộng tức thì.")
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))


@router.get("/cron/bank-sync")
@router.post("/cron/bank-sync")
@router.get("/bank-cron.php")
@router.post("/bank-cron.php")
async def bank_cron_sync(
    secret: Optional[str] = Query(None, description="Secret token cho Cron"),
    x_secret: Optional[str] = Header(None, alias="X-Bank-Cron-Secret"),
    optional_user: Optional[User] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Public HTTP endpoint for external cron jobs (crontab, UptimeRobot, cron-job.org).
    Validates optional or configured cron_secret before running bank sync.
    """
    from app.payments.bank_sync import BankSyncService
    from fastapi.responses import JSONResponse

    cfg = await PaymentService(db).get_banking_config()
    configured_secret = (cfg.get("cron_secret") or "").strip()
    provided_secret = (secret or x_secret or "").strip()

    is_admin = bool(optional_user and optional_user.role == "ADMIN")

    if configured_secret and not is_admin:
        if not provided_secret or not secrets.compare_digest(provided_secret, configured_secret):
            return JSONResponse(
                status_code=403,
                content={
                    "ok": False,
                    "success": False,
                    "message": "Secret Cron không chính xác hoặc chưa được cấu hình. Vui lòng kiểm tra mã Secret trong Cài đặt Ngân hàng hoặc đăng nhập tài khoản Quản trị viên.",
                }
            )

    try:
        result = await BankSyncService(db).sync_mbbank()
        processed_cnt = int(result.get("processed", result.get("seen", 0)))
        credited_cnt = int(result.get("credited", 0))
        ignored_cnt = int(result.get("ignored", 0))
        failed_cnt = int(result.get("failed", 0))
        duplicates_cnt = int(result.get("duplicates", 0))
        msg = str(result.get("message") or "Đã đồng bộ giao dịch ngân hàng.")
        is_ok = bool(result.get("ok", True))

        content = {
            "ok": is_ok,
            "message": msg,
            "processed": processed_cnt,
            "credited": credited_cnt,
            "ignored": ignored_cnt,
            "failed": failed_cnt,
            "duplicates": duplicates_cnt,
            "success": is_ok,
            "data": result,
        }
        return JSONResponse(status_code=200, content=content)
    except Exception as exc:
        return JSONResponse(
            status_code=200,
            content={
                "ok": False,
                "success": False,
                "message": f"Không thể lấy giao dịch từ ngân hàng: {str(exc)[:200]}",
                "processed": 0,
                "credited": 0,
                "error": str(exc)[:200],
            }
        )

@router.post("/admin/bank-test")
async def admin_bank_test_endpoint(
    payload: Optional[Dict[str, Any]] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if current_user.role != "ADMIN":
        raise HTTPException(status_code=403, detail="Chỉ Quản trị viên mới có quyền kiểm tra ngân hàng.")
    from app.payments.bank_sync import BankSyncService
    service = BankSyncService(db)
    result = await service.test_fetch_transactions(custom_config=payload or {})
    return ApiResponse(data=result, message=result.get("message", "Đã kiểm tra lịch sử giao dịch ngân hàng."))
