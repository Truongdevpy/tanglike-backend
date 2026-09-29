import asyncio
import time
from typing import Dict
from urllib.parse import urlparse

class ProviderRateLimiter:
    """
    Giới hạn tần suất gọi API nhà cung cấp để tránh bị chặn IP (Rate-Limit Protection).
    Mặc định: Tối đa 5 requests/giây cho mỗi host nhà cung cấp, khoảng cách tối thiểu giữa 2 request là 150ms.
    """
    _locks: Dict[str, asyncio.Lock] = {}
    _last_call_time: Dict[str, float] = {}
    _semaphores: Dict[str, asyncio.Semaphore] = {}

    def __init__(self, min_interval_seconds: float = 0.15, max_concurrent: int = 4):
        self.min_interval = min_interval_seconds
        self.max_concurrent = max_concurrent

    def _get_host(self, url: str) -> str:
        try:
            parsed = urlparse(url)
            return parsed.netloc or "default_provider"
        except Exception:
            return "default_provider"

    async def acquire(self, url: str):
        host = self._get_host(url)

        if len(self._locks) > 200:
            now_cleanup = time.monotonic()
            stale = [h for h, t in self._last_call_time.items() if now_cleanup - t > 3600]
            for sh in stale:
                self._locks.pop(sh, None)
                self._semaphores.pop(sh, None)
                self._last_call_time.pop(sh, None)

        if host not in self._locks:
            self._locks[host] = asyncio.Lock()
        if host not in self._semaphores:
            self._semaphores[host] = asyncio.Semaphore(self.max_concurrent)

        # Acquire concurrency slot
        await self._semaphores[host].acquire()

        # Enforce rate delay
        async with self._locks[host]:
            now = time.monotonic()
            last_time = self._last_call_time.get(host, 0.0)
            elapsed = now - last_time
            if elapsed < self.min_interval:
                await asyncio.sleep(self.min_interval - elapsed)
            self._last_call_time[host] = time.monotonic()

    def release(self, url: str):
        host = self._get_host(url)
        if host in self._semaphores:
            try:
                self._semaphores[host].release()
            except ValueError:
                pass

provider_rate_limiter = ProviderRateLimiter()
