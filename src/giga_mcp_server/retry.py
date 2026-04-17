from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


def async_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator that retries an async function with exponential backoff.

    Args:
        max_attempts: Maximum number of attempts (including the first).
        base_delay: Initial delay in seconds between retries.
        max_delay: Maximum delay cap in seconds.
        exceptions: Tuple of exception types to retry on.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: BaseException | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await fn(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        logger.error(
                            "retry_exhausted",
                            extra={
                                "function": fn.__name__,
                                "attempts": max_attempts,
                                "error": str(exc),
                            },
                        )
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    logger.warning(
                        "retry_attempt",
                        extra={
                            "function": fn.__name__,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "delay": delay,
                            "error": str(exc),
                        },
                    )
                    await asyncio.sleep(delay)
            raise last_exc  # Unreachable, but satisfies type checker

        return wrapper  # type: ignore[return-value]

    return decorator
