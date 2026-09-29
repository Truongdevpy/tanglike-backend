"""Enforce maintenance mode while preserving administrator recovery access."""

import time

from sqlalchemy import select
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.auth.security import decode_token
from app.database.session import AsyncSessionLocal
from app.models.all import SystemSetting, User


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    _public_paths = {"/health", "/docs", "/redoc", "/openapi.json"}

    def __init__(self, app):
        super().__init__(app)
        self._enabled = False
        self._checked_at = 0.0

    async def _is_enabled(self) -> bool:
        now = time.monotonic()
        if now - self._checked_at < 5:
            return self._enabled
        try:
            async with AsyncSessionLocal() as db:
                setting = (await db.execute(
                    select(SystemSetting.value).where(SystemSetting.key == "maintenance_mode")
                )).scalar_one_or_none()
            self._enabled = str(setting or "false").strip().lower() in {"1", "true", "yes", "on"}
        except Exception:
            # A database outage is handled by normal request failures, not a false maintenance page.
            self._enabled = False
        self._checked_at = now
        return self._enabled

    async def _is_active_admin(self, request: Request) -> bool:
        authorization = request.headers.get("authorization", "")
        if not authorization.startswith("Bearer "):
            return False
        try:
            payload = decode_token(authorization[7:], is_refresh=False)
            user_id = int(payload.get("sub", 0))
            async with AsyncSessionLocal() as db:
                user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
            return bool(user and user.role == "ADMIN" and user.status == "ACTIVE" and not user.is_deleted and payload.get("tv") is not None and payload.get("tv") == user.token_version)
        except Exception:
            return False

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in self._public_paths or path.startswith("/api/v1/auth/") or not await self._is_enabled():
            return await call_next(request)
        if await self._is_active_admin(request):
            return await call_next(request)
        return JSONResponse(
            status_code=503,
            content={"success": False, "error": {"code": "MAINTENANCE_MODE", "message": "The system is under maintenance. Please try again later."}},
            headers={"Retry-After": "300"},
        )
