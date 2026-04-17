from __future__ import annotations

import pytest

from giga_mcp_server.retry import async_retry


class TestAsyncRetry:
    async def test_succeeds_on_first_try(self) -> None:
        call_count = 0

        @async_retry(max_attempts=3, base_delay=0.01)
        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 1

    async def test_retries_on_failure_then_succeeds(self) -> None:
        call_count = 0

        @async_retry(max_attempts=3, base_delay=0.01)
        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("fail")
            return "ok"

        result = await fn()
        assert result == "ok"
        assert call_count == 3

    async def test_raises_after_max_attempts(self) -> None:
        call_count = 0

        @async_retry(max_attempts=3, base_delay=0.01)
        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise ConnectionError("always fails")

        with pytest.raises(ConnectionError, match="always fails"):
            await fn()
        assert call_count == 3

    async def test_only_retries_specified_exceptions(self) -> None:
        call_count = 0

        @async_retry(max_attempts=3, base_delay=0.01, exceptions=(ValueError,))
        async def fn() -> str:
            nonlocal call_count
            call_count += 1
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            await fn()
        assert call_count == 1  # No retry for TypeError

    async def test_preserves_return_value(self) -> None:
        @async_retry(max_attempts=2, base_delay=0.01)
        async def fn() -> dict:
            return {"key": "PROJ-1"}

        result = await fn()
        assert result == {"key": "PROJ-1"}
