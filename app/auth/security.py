from datetime import datetime, timedelta
import hashlib
from typing import Optional, Dict, Any
import bcrypt
import jwt
from fastapi import Depends, HTTPException, status, Header, Request
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.settings import settings
from app.database.session import get_db
from app.models.all import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login", auto_error=False)

def hash_api_key(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()

def hash_password(password: str) -> str:
    if not password or not password.strip():
        raise ValueError("Password cannot be empty or whitespace only.")
    pwd_bytes = password.encode('utf-8')
    if len(pwd_bytes) > 72:
        raise ValueError("Password cannot exceed 72 bytes.")
    if b'\x00' in pwd_bytes:
        raise ValueError("Password cannot contain null bytes.")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(pwd_bytes, salt).decode('utf-8')

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not plain_password or not hashed_password:
        return False
    try:
        pwd_bytes = plain_password.encode('utf-8')
        if len(pwd_bytes) > 72 or b'\x00' in pwd_bytes:
            return False
        return bcrypt.checkpw(pwd_bytes, hashed_password.encode('utf-8'))
    except Exception:
        return False

def create_access_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.utcnow()
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"iat": now, "exp": expire, "type": "access"})
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.ALGORITHM)

def create_refresh_token(data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.utcnow()
    expire = now + (expires_delta or timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS))
    to_encode.update({"iat": now, "exp": expire, "type": "refresh"})
    return jwt.encode(to_encode, settings.JWT_REFRESH_SECRET, algorithm=settings.ALGORITHM)

def decode_token(token: str, is_refresh: bool = False) -> Dict[str, Any]:
    if not token or not isinstance(token, str) or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token xác thực không hợp lệ."
        )
    token = token.strip()
    secret = settings.JWT_REFRESH_SECRET if is_refresh else settings.JWT_SECRET
    expected_type = "refresh" if is_refresh else "access"
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cấu hình bảo mật hệ thống chưa hoàn tất."
        )
    try:
        try:
            unverified = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
            token_type = unverified.get("type")
            if token_type and token_type != expected_type:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Loại token không hợp lệ (yêu cầu {expected_type} token)."
                )
        except HTTPException:
            raise
        except Exception:
            pass

        payload = jwt.decode(
            token,
            secret,
            algorithms=[settings.ALGORITHM],
            options={
                "verify_signature": True,
                "verify_exp": True,
                "require": ["exp", "sub"]
            }
        )
        if payload.get("type") != expected_type:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Loại token không hợp lệ (yêu cầu {expected_type} token)."
            )
        return payload
    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên đăng nhập đã hết hạn, vui lòng đăng nhập lại."
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token xác thực không hợp lệ."
        )


async def get_current_user(
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> User:
    if not token or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Yêu cầu xác thực tài khoản."
        )
    payload = decode_token(token, is_refresh=False)
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dữ liệu xác thực không hợp lệ."
        )
    try:
        uid_int = int(user_id)
        if uid_int <= 0:
            raise ValueError
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dữ liệu xác thực không hợp lệ."
        )

    result = await db.execute(select(User).where(User.id == uid_int))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Không tìm thấy tài khoản người dùng."
        )
    if user.status != "ACTIVE" or getattr(user, "is_deleted", False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tài khoản của bạn đã bị khóa hoặc đã ngừng hoạt động."
        )
    if payload.get("tv") is None or payload.get("tv") != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Phiên đăng nhập đã bị thu hồi. Vui lòng đăng nhập lại."
        )
    return user

async def get_optional_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    resolved_token = token
    if not resolved_token or not resolved_token.strip():
        resolved_token = (
            request.cookies.get("tanglike_token")
            or request.cookies.get("access_token")
            or request.query_params.get("token")
        )
    if not resolved_token or not resolved_token.strip():
        return None
    try:
        payload = decode_token(resolved_token, is_refresh=False)
        user_id = payload.get("sub")
        if not user_id:
            return None
        try:
            uid_int = int(user_id)
            if uid_int <= 0:
                return None
        except (ValueError, TypeError):
            return None
        result = await db.execute(select(User).where(User.id == uid_int))
        user = result.scalar_one_or_none()
        if not user or user.status != "ACTIVE" or getattr(user, "is_deleted", False):
            return None
        if payload.get("tv") is None or payload.get("tv") != user.token_version:
            return None
        return user
    except Exception:
        return None

def require_role(roles: list[str]):
    async def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Bạn không có quyền thực hiện thao tác này."
            )
        return current_user
    return role_checker

async def get_api_key_user(
    request: Request,
    x_api_key: Optional[str] = Header(None, alias="X-API-KEY"),
    db: AsyncSession = Depends(get_db)
) -> User:
    from app.middleware.rate_limit import failed_api_key_tracker
    client_ip = request.client.host if request.client else "127.0.0.1"
    is_test = settings.APP_ENV.lower() == "test"
    test_flag = bool(request.headers.get("X-Test-Rate-Limit"))
    if failed_api_key_tracker.is_blocked(client_ip, is_test=is_test, test_flag=test_flag):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed API key attempts. Please retry later.",
            headers={"Retry-After": "60"}
        )

    if not x_api_key or not x_api_key.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Thiếu X-API-KEY trong header request."
        )
    clean_key = x_api_key.strip()
    if len(clean_key) > 128 or len(clean_key) < 16:
        failed_api_key_tracker.record_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key không hợp lệ hoặc tài khoản đã bị khóa."
        )

    result = await db.execute(select(User).where(User.api_key_hash == hash_api_key(clean_key)))
    user = result.scalar_one_or_none()
    if not user or user.status != "ACTIVE" or getattr(user, "is_deleted", False):
        failed_api_key_tracker.record_failure(client_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key không hợp lệ hoặc tài khoản đã bị khóa."
        )
    return user
