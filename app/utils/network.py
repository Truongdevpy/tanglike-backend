import asyncio
import logging
from typing import Callable, Any, Tuple, Type
import httpx

logger = logging.getLogger(__name__)

DEFAULT_RETRY_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    httpx.RequestError,
    httpx.TimeoutException,
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout
)

async def async_retry(
    func: Callable[[], Any],
    max_retries: int = 3,
    initial_delay: float = 0.5,
    backoff_factor: float = 2.0,
    retry_exceptions: Tuple[Type[Exception], ...] = DEFAULT_RETRY_EXCEPTIONS
) -> Any:
    """
    Executes an async callable with exponential backoff on transient network or 5xx server errors.
    Does NOT retry client errors (4xx) to avoid redundant spam.
    """
    delay = initial_delay
    last_exc: Exception | None = None

    for attempt in range(1, max_retries + 1):
        try:
            return await func()
        except retry_exceptions as exc:
            last_exc = exc
            if attempt == max_retries:
                logger.error(f"Network call failed after {max_retries} attempts: {exc}")
                raise
            logger.warning(f"Transient error on attempt {attempt}/{max_retries}: {exc}. Retrying in {delay:.2f}s...")
            await asyncio.sleep(delay)
            delay *= backoff_factor
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code >= 500:
                last_exc = exc
                if attempt == max_retries:
                    logger.error(f"Upstream server 5xx error after {max_retries} attempts: {exc}")
                    raise
                logger.warning(f"Upstream {exc.response.status_code} error on attempt {attempt}/{max_retries}. Retrying in {delay:.2f}s...")
                await asyncio.sleep(delay)
                delay *= backoff_factor
            else:
                raise

    if last_exc:
        raise last_exc
