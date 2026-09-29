from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette import status

# Maximum allowed payload size: 10 Megabytes
MAX_PAYLOAD_SIZE_BYTES = 10 * 1024 * 1024

class PayloadLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
                if length > MAX_PAYLOAD_SIZE_BYTES:
                    return JSONResponse(
                        status_code=413,
                        content={
                            "success": False,
                            "error": {
                                "code": "PAYLOAD_TOO_LARGE",
                                "message": f"Dung lượng request vượt quá giới hạn tối đa cho phép ({MAX_PAYLOAD_SIZE_BYTES // (1024*1024)}MB)."
                            }
                        }
                    )
            except ValueError:
                pass
        return await call_next(request)
