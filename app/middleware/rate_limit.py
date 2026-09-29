import time
import hashlib
from collections import defaultdict
from typing import Dict, List, Tuple
from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from app.config.settings import settings


class ApiKeyRateLimiter:
    """Per-key limiter for protocols which carry the key in the request body."""

    def __init__(self) -> None:
        self.requests: Dict[str, List[float]] = defaultdict(list)

    def check(self, raw_key: str) -> None:
        fingerprint = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16]
        now = time.time()
        timestamps = [
            timestamp for timestamp in self.requests[fingerprint]
            if now - timestamp < 60.0
        ]
        if len(timestamps) >= settings.USER_API_RATE_LIMIT_PER_MINUTE:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="API rate limit exceeded. Please retry in one minute.",
                headers={"Retry-After": "60"},
            )
        timestamps.append(now)
        self.requests[fingerprint] = timestamps


class FailedApiKeyTracker:
    """Tracks failed API key authentication attempts by IP to thwart brute force key enumeration."""

    def __init__(self) -> None:
        self.failures: Dict[str, List[float]] = defaultdict(list)

    def is_blocked(self, identifier: str, max_failures: int = 30, window: float = 60.0, is_test: bool = False, test_flag: bool = False) -> bool:
        if is_test and not test_flag:
            return False
        now = time.time()
        self.failures[identifier] = [t for t in self.failures[identifier] if now - t < window]
        return len(self.failures[identifier]) >= max_failures

    def record_failure(self, identifier: str) -> None:
        now = time.time()
        self.failures[identifier].append(now)


failed_api_key_tracker = FailedApiKeyTracker()

# SMM v2 sends its key as form/json data, after middleware has run. Keep this
# process-local limiter at router level so separate customers do not share an IP
# bucket. Deployments with multiple workers should use the same limiter via Redis.
smm_v2_api_key_limiter = ApiKeyRateLimiter()

class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    In-memory sliding window rate limiter.
    Limits:
      - /api/v1/auth/login: 15 req/min
      - /api/v1/auth/register: 5 req/min
      - General endpoints: 180 req/min
    """
    def __init__(self, app):
        super().__init__(app)
        self.requests: Dict[str, List[float]] = defaultdict(list)

    def _get_client_ip(self, request: Request) -> str:
        # Only a known reverse proxy can supply a forwarded client address.
        peer_ip = request.client.host if request.client else "127.0.0.1"
        is_trusted = (
            peer_ip in settings.TRUSTED_PROXY_IPS
            or peer_ip in ("127.0.0.1", "::1", "testclient")
        )
        if is_trusted:
            cf_ip = request.headers.get("CF-Connecting-IP")
            if cf_ip:
                return cf_ip.strip()
            x_real_ip = request.headers.get("X-Real-IP")
            if x_real_ip:
                return x_real_ip.strip()
            forwarded = request.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return peer_ip

    async def dispatch(self, request: Request, call_next):
        # Exclude documentation, health check, and static files
        path = request.url.path
        if path in ["/health", "/docs", "/redoc", "/openapi.json"] or path.startswith("/static"):
            return await call_next(request)

        # Allow tests to bypass rate limiting via header only in test environment
        if settings.APP_ENV.lower() == "test" and request.headers.get("X-Bypass-Rate-Limit") == "true":
            return await call_next(request)

        ip = self._get_client_ip(request)
        now = time.time()
        window = 60.0  # 1 minute

        # Determine threshold
        if "/auth/login" in path:
            limit = 15
            bucket_key = f"login_{ip}"
        elif "/auth/register" in path:
            limit = 5
            bucket_key = f"register_{ip}"
        elif "/auth/forgot-password" in path:
            limit = 5
            bucket_key = f"forgot_{ip}"
        elif "/payments/deposit" in path:
            limit = 20
            bucket_key = f"deposit_{ip}"
        elif path.startswith("/api/v1/user-api/"):
            # Rate-limit API clients by an irreversible key fingerprint, not IP,
            # so a shared NAT cannot exhaust every customer's quota.
            raw_key = request.headers.get("X-API-KEY", "")
            fingerprint = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:16] if raw_key else ip
            limit = settings.USER_API_RATE_LIMIT_PER_MINUTE
            bucket_key = f"user_api_{fingerprint}"

            # Secondary IP safety limit prevents a single IP from brute-forcing random keys
            ip_limit = 200
            if ip in ("testclient", "127.0.0.1") and not request.headers.get("X-Test-Rate-Limit"):
                ip_limit = 1000 if settings.APP_ENV.lower() == "test" else 200
            ip_timestamps = [t for t in self.requests[f"user_api_ip_{ip}"] if now - t < window]
            if len(ip_timestamps) >= ip_limit:
                retry_after = int(window - (now - ip_timestamps[0])) + 1
                return JSONResponse(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    content={
                        "success": False,
                        "error": {
                            "code": "RATE_LIMIT_EXCEEDED",
                            "message": f"Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau {retry_after} giây."
                        }
                    },
                    headers={"Retry-After": str(retry_after)}
                )
            self.requests[f"user_api_ip_{ip}"].append(now)
        elif path.endswith("/orders") and request.method == "POST":
            limit = 60
            bucket_key = f"orders_{ip}"
        else:
            limit = 200
            bucket_key = f"general_{ip}"

        # If client IP is local testclient and not testing rate limit, allow higher headroom
        if ip in ("testclient", "127.0.0.1") and not request.headers.get("X-Test-Rate-Limit"):
            limit = max(limit, 1000) if settings.APP_ENV.lower() == "test" else max(limit, 200)

        # Clean old timestamps
        timestamps = [t for t in self.requests[bucket_key] if now - t < window]
        if len(timestamps) >= limit:
            retry_after = int(window - (now - timestamps[0])) + 1
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={
                    "success": False,
                    "error": {
                        "code": "RATE_LIMIT_EXCEEDED",
                        "message": f"Bạn đã gửi quá nhiều yêu cầu. Vui lòng thử lại sau {retry_after} giây."
                    }
                },
                headers={"Retry-After": str(retry_after)}
            )

        timestamps.append(now)
        self.requests[bucket_key] = timestamps

        return await call_next(request)
