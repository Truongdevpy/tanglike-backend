import logging

logger = logging.getLogger(__name__)

from typing import Dict, Any, Optional, List

import secrets

import hashlib

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status, Request

from sqlalchemy import select, func, or_

from sqlalchemy.ext.asyncio import AsyncSession



from app.database.session import get_db

from app.models.all import User, Referral, PasswordResetToken, EmailVerificationToken, SystemSetting

from app.schemas.all import (

    ApiResponse, UserRegister, UserLogin, TokenResponse, UserResponse,

    RefreshTokenRequest, ChangePasswordRequest, UserProfileUpdate, ResetPasswordRequest,

    ResetPasswordConfirmRequest, EmailVerificationConfirmRequest

)

from app.auth.security import (

    hash_password, verify_password, create_access_token,

    create_refresh_token, decode_token, get_current_user, hash_api_key

)

from app.notifications.telegram import TelegramNotifier

from app.notifications.email import EmailNotifier

from app.config.settings import settings



router = APIRouter(prefix="/auth", tags=["Authentication"])





def _token_hash(token: str) -> str:

    return hashlib.sha256(token.encode("utf-8")).hexdigest()





async def _issue_email_verification(db: AsyncSession, user: User) -> Optional[str]:

    """Create one active verification credential and deliver it when configured."""

    if user.email_verified:

        return None

    await db.execute(

        EmailVerificationToken.__table__.update()

        .where(EmailVerificationToken.user_id == user.id, EmailVerificationToken.used_at.is_(None))

        .values(used_at=datetime.utcnow())

    )

    raw_token = secrets.token_urlsafe(48)

    db.add(EmailVerificationToken(

        user_id=user.id,

        token_hash=_token_hash(raw_token),

        expires_at=datetime.utcnow() + timedelta(hours=24),

    ))

    await db.commit()

    if settings.APP_ENV.lower() == "production":

        await EmailNotifier.send_email_verification(user.email, raw_token)

        return None

    return raw_token



@router.post("/register", response_model=ApiResponse[TokenResponse])

async def register(payload: UserRegister, db: AsyncSession = Depends(get_db)):

    registration_setting = await db.scalar(

        select(SystemSetting.value).where(SystemSetting.key == "registration_enabled")

    )

    registration_enabled = (

        registration_setting.strip().lower() in {"1", "true", "yes", "on"}

        if registration_setting is not None

        else settings.REGISTRATION_ENABLED

    )

    if not registration_enabled:

        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="New registrations are currently disabled.")

    if payload.password != payload.confirm_password:

        raise HTTPException(

            status_code=status.HTTP_400_BAD_REQUEST,

            detail="Mật khẩu xác nhận không khớp."

        )

    clean_username = payload.username.strip()
    if payload.email:
        clean_email = str(payload.email).strip().lower()
        from app.utils.security import is_disposable_email
        if is_disposable_email(clean_email):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vui lòng sử dụng địa chỉ email hợp lệ (không chấp nhận email tạm thời/disposable email)."
            )
    else:
        clean_email = f"{clean_username.lower()}@tanglike.vn"

    if not clean_username or len(clean_username) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tên đăng nhập phải có ít nhất 3 ký tự."
        )
    if not payload.password or not payload.password.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu không được để trống hoặc chỉ chứa khoảng trắng."
        )
    if len(payload.password.encode("utf-8")) > 72:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu không được vượt quá 72 bytes."
        )
    if "\x00" in payload.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Mật khẩu chứa ký tự không hợp lệ."
        )

    # Check username
    check_user = await db.execute(
        select(User).where(func.lower(User.username) == clean_username.lower())
    )
    if check_user.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Tên đăng nhập đã tồn tại."
        )

    if payload.email:
        check_email = await db.execute(
            select(User).where(func.lower(User.email) == clean_email)
        )
        if check_email.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Địa chỉ email đã tồn tại."
            )
    else:
        check_email = await db.execute(
            select(User).where(func.lower(User.email) == clean_email)
        )
        if check_email.scalar_one_or_none():
            clean_email = f"{clean_username.lower()}_{secrets.token_hex(3)}@tanglike.vn" 



    # Check referral code if provided

    referrer_id = None

    if payload.referral_code:

        ref_check = await db.execute(

            select(User).where(User.referral_code == payload.referral_code.strip())

        )

        referrer = ref_check.scalar_one_or_none()

        if referrer:

            referrer_id = referrer.id



    # Generate unique user referral code safely bounded within VARCHAR(50)

    unique_ref = f"{clean_username[:36].upper()}{secrets.token_hex(4).upper()}"



    user = User(

        username=clean_username,

        email=clean_email,

        password_hash=hash_password(payload.password),

        full_name=payload.full_name or payload.username,

        phone=payload.phone,

        # Privileged accounts are created only through explicit bootstrap configuration.

        role="USER",

        balance=0.0,

        status="ACTIVE",

        token_version=0,

        referral_code=unique_ref,

        referred_by_id=referrer_id,

        api_key=None,

        created_at=datetime.utcnow(),

        last_login_at=datetime.utcnow()

    )

    db.add(user)

    await db.flush()



    if referrer_id:

        ref_record = Referral(

            referrer_id=referrer_id,

            referred_user_id=user.id,

            commission=0.0,

            status="ACTIVE",

            created_at=datetime.utcnow()

        )

        db.add(ref_record)



    await db.commit()

    await db.refresh(user)



    # Production delivery is conditional on SMTP configuration. Development

    # retains the token only in the explicit resend response for testability.

    if EmailNotifier.configured() or settings.APP_ENV.lower() != "production":

        await _issue_email_verification(db, user)



    # Notify via Telegram

    await TelegramNotifier.notify_new_user(user.username, user.email)



    access_token = create_access_token(data={"sub": str(user.id), "role": user.role, "tv": user.token_version})

    refresh_token = create_refresh_token(data={"sub": str(user.id), "tv": user.token_version})



    return ApiResponse(

        data=TokenResponse(

            access_token=access_token,

            refresh_token=refresh_token,

            user=UserResponse.model_validate(user)

        ),

        message="Đăng ký tài khoản thành công."

    )



@router.post("/login", response_model=ApiResponse[TokenResponse])

async def login(payload: UserLogin, request: Request, db: AsyncSession = Depends(get_db)):

    raw_ident = (payload.username or "").strip()

    if not raw_ident:

        raise HTTPException(

            status_code=status.HTTP_400_BAD_REQUEST,

            detail="Tên đăng nhập hoặc email không được để trống."

        )

    result = await db.execute(

        select(User).where(

            or_(

                func.lower(User.username) == raw_ident.lower(),

                func.lower(User.email) == raw_ident.lower()

            )

        )

    )

    user = result.scalar_one_or_none()

    if not user:

        raise HTTPException(

            status_code=status.HTTP_400_BAD_REQUEST,

            detail=f"Tài khoản '{raw_ident}' chưa tồn tại trên hệ thống. Vui lòng bấm 'Đăng ký tài khoản mới' hoặc kiểm tra lại tên đăng nhập."

        )



    if not verify_password(payload.password, user.password_hash):

        raise HTTPException(

            status_code=status.HTTP_400_BAD_REQUEST,

            detail="Mật khẩu không chính xác. Vui lòng kiểm tra lại phím CapsLock hoặc mật khẩu đã nhập."

        )



    if user.status != "ACTIVE" or getattr(user, "is_deleted", False):

        raise HTTPException(

            status_code=status.HTTP_403_FORBIDDEN,

            detail="Tài khoản của bạn đã bị tạm ngưng hoặc vô hiệu hóa. Vui lòng liên hệ Admin để được hỗ trợ."

        )



    user.last_login_at = datetime.utcnow()

    await db.commit()



    access_token = create_access_token(data={"sub": str(user.id), "role": user.role, "tv": user.token_version})

    refresh_token = create_refresh_token(data={"sub": str(user.id), "tv": user.token_version})



    return ApiResponse(

        data=TokenResponse(

            access_token=access_token,

            refresh_token=refresh_token,

            user=UserResponse.model_validate(user)

        ),

        message="Đăng nhập thành công."

    )



@router.post("/refresh", response_model=ApiResponse[Dict[str, str]])

async def refresh_token(payload: RefreshTokenRequest, db: AsyncSession = Depends(get_db)):

    raw_token = (payload.refresh_token or "").strip()

    if not raw_token:

        raise HTTPException(status_code=401, detail="Token không hợp lệ.")

    decoded = decode_token(raw_token, is_refresh=True)

    user_id = decoded.get("sub")

    if not user_id:

        raise HTTPException(status_code=401, detail="Token không hợp lệ.")

    try:

        uid_int = int(user_id)

        if uid_int <= 0:

            raise ValueError

    except (ValueError, TypeError):

        raise HTTPException(status_code=401, detail="Token không hợp lệ.")



    res = await db.execute(select(User).where(User.id == uid_int))

    user = res.scalar_one_or_none()

    if not user or user.status != "ACTIVE" or getattr(user, "is_deleted", False):

        raise HTTPException(status_code=401, detail="Token không hợp lệ hoặc tài khoản đã bị khóa.")



    if decoded.get("tv") is None or decoded.get("tv") != user.token_version:

        raise HTTPException(status_code=401, detail="Phiên đăng nhập đã bị thu hồi. Vui lòng đăng nhập lại.")

    new_access_token = create_access_token(data={"sub": str(user.id), "role": user.role, "tv": user.token_version})

    new_refresh_token = create_refresh_token(data={"sub": str(user.id), "tv": user.token_version})



    return ApiResponse(

        data={"access_token": new_access_token, "refresh_token": new_refresh_token},

        message="Làm mới phiên đăng nhập thành công."

    )



@router.get("/me", response_model=ApiResponse[UserResponse])

async def get_me(current_user: User = Depends(get_current_user)):

    return ApiResponse(data=UserResponse.model_validate(current_user))



@router.put("/profile", response_model=ApiResponse[UserResponse])

async def update_profile(

    payload: UserProfileUpdate,

    current_user: User = Depends(get_current_user),

    db: AsyncSession = Depends(get_db)

):

    if payload.full_name is not None:

        current_user.full_name = payload.full_name

    if payload.phone is not None:

        current_user.phone = payload.phone

    if payload.avatar is not None:

        clean_av = payload.avatar.strip()

        if clean_av:

            from app.utils.security import is_safe_presentation_url

            if not is_safe_presentation_url(clean_av):

                raise HTTPException(status_code=400, detail="URL ảnh đại diện không hợp lệ hoặc chứa giao thức nguy hiểm.")

        current_user.avatar = clean_av if clean_av else None

    await db.commit()

    await db.refresh(current_user)

    return ApiResponse(data=UserResponse.model_validate(current_user), message="Cập nhật thông tin thành công.")



@router.post("/change-password", response_model=ApiResponse[bool])

async def change_password(

    payload: ChangePasswordRequest,

    current_user: User = Depends(get_current_user),

    db: AsyncSession = Depends(get_db)

):

    if not verify_password(payload.current_password, current_user.password_hash):

        raise HTTPException(status_code=400, detail="Mật khẩu hiện tại không đúng.")



    if not payload.new_password or not payload.new_password.strip():

        raise HTTPException(status_code=400, detail="Mật khẩu mới không được để trống hoặc chỉ chứa khoảng trắng.")

    if len(payload.new_password.encode("utf-8")) > 72:

        raise HTTPException(status_code=400, detail="Mật khẩu mới không được vượt quá 72 bytes.")

    if "\x00" in payload.new_password:

        raise HTTPException(status_code=400, detail="Mật khẩu chứa ký tự không hợp lệ.")



    current_user.password_hash = hash_password(payload.new_password)

    current_user.token_version = (current_user.token_version or 0) + 1

    await db.commit()

    return ApiResponse(data=True, message="Đổi mật khẩu thành công.")



@router.post("/logout", response_model=ApiResponse[bool])

async def logout(

    current_user: User = Depends(get_current_user),

    db: AsyncSession = Depends(get_db)

):

    """Invalidate current session by bumping token version."""

    current_user.token_version = (current_user.token_version or 0) + 1

    await db.commit()

    return ApiResponse(data=True, message="Đăng xuất thành công.")



@router.post("/logout-all", response_model=ApiResponse[bool])

async def logout_all_devices(

    current_user: User = Depends(get_current_user),

    db: AsyncSession = Depends(get_db)

):

    """Invalidate every issued access and refresh token for the current user."""

    current_user.token_version = (current_user.token_version or 0) + 1

    await db.commit()

    return ApiResponse(data=True, message="All sessions have been signed out.")





@router.post("/forgot-password", response_model=ApiResponse[Dict[str, Optional[str]]])

async def forgot_password(payload: ResetPasswordRequest, db: AsyncSession = Depends(get_db)):

    """Issue a one-time credential without disclosing whether an email exists."""

    user = (await db.execute(select(User).where(func.lower(User.email) == str(payload.email).strip().lower()))).scalar_one_or_none()

    response: Dict[str, Optional[str]] = {"reset_token": None}

    if user and user.status == "ACTIVE" and not user.is_deleted:

        if settings.APP_ENV.lower() == "production" and not EmailNotifier.configured():

            logger.error("Password reset requested but SMTP delivery is not configured")

            return ApiResponse(data=response, message="If the email exists, reset instructions have been sent.")

        await db.execute(

            PasswordResetToken.__table__.update()

            .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))

            .values(used_at=datetime.utcnow())

        )

        raw_token = secrets.token_urlsafe(48)

        db.add(PasswordResetToken(

            user_id=user.id,

            token_hash=_token_hash(raw_token),

            expires_at=datetime.utcnow() + timedelta(minutes=30),

        ))

        await db.commit()

        if settings.APP_ENV.lower() == "production":

            await EmailNotifier.send_password_reset(user.email, raw_token)

        # Development callers can exercise this flow without SMTP credentials.

        if settings.APP_ENV.lower() != "production":

            response["reset_token"] = raw_token

    return ApiResponse(data=response, message="If the email exists, reset instructions have been sent.")





@router.post("/reset-password", response_model=ApiResponse[bool])

async def reset_password(payload: ResetPasswordConfirmRequest, db: AsyncSession = Depends(get_db)):

    clean_token = (payload.token or "").strip()

    if not clean_token:

        raise HTTPException(status_code=400, detail="Reset link is invalid or expired.")



    if not payload.new_password or not payload.new_password.strip():

        raise HTTPException(status_code=400, detail="Mật khẩu mới không được để trống hoặc chỉ chứa khoảng trắng.")

    if len(payload.new_password.encode("utf-8")) > 72:

        raise HTTPException(status_code=400, detail="Mật khẩu mới không được vượt quá 72 bytes.")

    if "\x00" in payload.new_password:

        raise HTTPException(status_code=400, detail="Mật khẩu chứa ký tự không hợp lệ.")



    reset = (await db.execute(

        select(PasswordResetToken).where(

            PasswordResetToken.token_hash == _token_hash(clean_token),

            PasswordResetToken.used_at.is_(None),

            PasswordResetToken.expires_at > datetime.utcnow(),

        ).with_for_update()

    )).scalar_one_or_none()

    if not reset:

        raise HTTPException(status_code=400, detail="Reset link is invalid or expired.")

    user = (await db.execute(select(User).where(User.id == reset.user_id).with_for_update())).scalar_one_or_none()

    if not user or user.status != "ACTIVE" or user.is_deleted:

        raise HTTPException(status_code=400, detail="This account cannot reset its password.")

    user.password_hash = hash_password(payload.new_password)

    user.token_version = (user.token_version or 0) + 1

    reset.used_at = datetime.utcnow()

    await db.execute(

        PasswordResetToken.__table__.update()

        .where(PasswordResetToken.user_id == user.id, PasswordResetToken.used_at.is_(None))

        .values(used_at=datetime.utcnow())

    )

    await db.commit()

    return ApiResponse(data=True, message="Password reset successfully. Please sign in again.")





@router.post("/resend-email-verification", response_model=ApiResponse[Dict[str, Optional[str]]])

async def resend_email_verification(

    current_user: User = Depends(get_current_user),

    db: AsyncSession = Depends(get_db),

):

    if current_user.email_verified:

        return ApiResponse(data={"verification_token": None}, message="Email đã được xác minh.")

    if settings.APP_ENV.lower() == "production" and not EmailNotifier.configured():

        raise HTTPException(status_code=503, detail="Email delivery is not configured.")

    token = await _issue_email_verification(db, current_user)

    return ApiResponse(

        data={"verification_token": token if settings.APP_ENV.lower() != "production" else None},

        message="Đã gửi hướng dẫn xác minh email.",

    )





@router.post("/verify-email", response_model=ApiResponse[bool])

async def verify_email(payload: EmailVerificationConfirmRequest, db: AsyncSession = Depends(get_db)):

    verification = (await db.execute(

        select(EmailVerificationToken).where(

            EmailVerificationToken.token_hash == _token_hash(payload.token),

            EmailVerificationToken.used_at.is_(None),

            EmailVerificationToken.expires_at > datetime.utcnow(),

        ).with_for_update()

    )).scalar_one_or_none()

    if not verification:

        raise HTTPException(status_code=400, detail="Liên kết xác minh không hợp lệ hoặc đã hết hạn.")

    user = (await db.execute(select(User).where(User.id == verification.user_id).with_for_update())).scalar_one_or_none()

    if not user or user.is_deleted:

        raise HTTPException(status_code=400, detail="Tài khoản không còn khả dụng.")

    user.email_verified = True

    verification.used_at = datetime.utcnow()

    await db.commit()

    return ApiResponse(data=True, message="Email đã được xác minh thành công.")





@router.post("/generate-api-key", response_model=ApiResponse[str])

async def generate_api_key(

    current_user: User = Depends(get_current_user),

    db: AsyncSession = Depends(get_db)

):

    new_key = f"tang_{secrets.token_urlsafe(28)}"

    current_user.api_key = None

    current_user.api_key_hash = hash_api_key(new_key)

    current_user.api_key_prefix = new_key[:12]

    await db.commit()

    return ApiResponse(data=new_key, message="Tạo API Key mới thành công.")





@router.delete("/api-key", response_model=ApiResponse[bool])

async def revoke_api_key(

    current_user: User = Depends(get_current_user),

    db: AsyncSession = Depends(get_db),

):

    current_user.api_key = None

    current_user.api_key_hash = None

    current_user.api_key_prefix = None

    await db.commit()

    return ApiResponse(data=True, message="API Key đã được thu hồi.")

from app.routers.users import export_gdpr_data, deactivate_account

router.add_api_route("/export-data", export_gdpr_data, methods=["GET"])

router.add_api_route("/me/export-data", export_gdpr_data, methods=["GET"])

router.add_api_route("/deactivate", deactivate_account, methods=["POST"])

router.add_api_route("/me/deactivate", deactivate_account, methods=["POST"])

