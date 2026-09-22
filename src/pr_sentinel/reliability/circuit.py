"""A circuit breaker, per external dependency.

Retries help with a blip. They make a sustained outage worse: every worker keeps
paying full timeout on every call, and the queue backs up behind a dependency
that is not coming back this minute. The breaker turns that into a fast, cheap,
obvious failure.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import TypeVar

from ..logging import get_logger

log = get_logger(__name__)
T = TypeVar("T")


class State(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpen(Exception):
    def __init__(self, name: str, retry_in: float) -> None:
        super().__init__(f"circuit '{name}' is open; retry in {retry_in:.1f}s")
        self.name = name
        self.retry_in = retry_in


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        *,
        failure_threshold: int = 5,
        recovery_time: float = 30.0,
        success_threshold: int = 2,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_time = recovery_time
        self.success_threshold = success_threshold
        self._state = State.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at = 0.0
        self._lock = asyncio.Lock()

    @property
    def state(self) -> State:
        if self._state is State.OPEN and time.monotonic() - self._opened_at >= self.recovery_time:
            self._state = State.HALF_OPEN
            self._successes = 0
            log.info("circuit.half_open", circuit=self.name)
        return self._state

    async def call(self, fn: Callable[[], Awaitable[T]]) -> T:
        if self.state is State.OPEN:
            raise CircuitOpen(self.name, self.recovery_time - (time.monotonic() - self._opened_at))
        try:
            result = await fn()
        except Exception:
            await self._on_failure()
            raise
        await self._on_success()
        return result

    async def _on_success(self) -> None:
        async with self._lock:
            if self._state is State.HALF_OPEN:
                self._successes += 1
                if self._successes >= self.success_threshold:
                    self._state = State.CLOSED
                    self._failures = 0
                    log.info("circuit.closed", circuit=self.name)
            else:
                self._failures = 0

    async def _on_failure(self) -> None:
        async with self._lock:
            self._failures += 1
            # One failure while probing is enough to re-open; the dependency
            # has had its chance.
            if self._state is State.HALF_OPEN or self._failures >= self.failure_threshold:
                self._state = State.OPEN
                self._opened_at = time.monotonic()
                log.warning("circuit.open", circuit=self.name, failures=self._failures)


_registry: dict[str, CircuitBreaker] = {}


def breaker(name: str, **kwargs) -> CircuitBreaker:
    if name not in _registry:
        _registry[name] = CircuitBreaker(name, **kwargs)
    return _registry[name]


def reset_all() -> None:
    _registry.clear()
