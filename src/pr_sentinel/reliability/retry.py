"""Retry with exponential backoff and full jitter.

Full jitter rather than fixed backoff: when a dependency comes back after an
outage, synchronised retries from every worker are how a recovering service gets
knocked over a second time.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from ..logging import get_logger

log = get_logger(__name__)
T = TypeVar("T")


class RetryExhausted(Exception):
    def __init__(self, attempts: int, last: BaseException) -> None:
        super().__init__(f"gave up after {attempts} attempts: {type(last).__name__}: {last}")
        self.attempts = attempts
        self.last = last


@dataclass(frozen=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 20.0
    retry_on: Sequence[type[BaseException]] = (Exception,)
    give_up_on: Sequence[type[BaseException]] = ()

    def delay_for(self, attempt: int) -> float:
        ceiling = min(self.max_delay, self.base_delay * (2**attempt))
        return random.uniform(0, ceiling)  # noqa: S311 - jitter, not crypto


async def with_retry(
    fn: Callable[[], Awaitable[T]],
    policy: RetryPolicy | None = None,
    *,
    name: str = "call",
    retry_after: Callable[[BaseException], float | None] | None = None,
) -> T:
    policy = policy or RetryPolicy()
    last: BaseException | None = None

    for attempt in range(policy.attempts):
        try:
            return await fn()
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            if isinstance(exc, tuple(policy.give_up_on)):
                raise
            if not isinstance(exc, tuple(policy.retry_on)):
                raise
            last = exc
            if attempt == policy.attempts - 1:
                break
            # A server that told us when to come back knows better than our curve.
            hinted = retry_after(exc) if retry_after else None
            delay = hinted if hinted is not None else policy.delay_for(attempt)
            log.warning(
                "retry",
                call=name,
                attempt=attempt + 1,
                of=policy.attempts,
                sleep_s=round(delay, 2),
                error=f"{type(exc).__name__}: {exc}",
            )
            await asyncio.sleep(delay)

    assert last is not None
    raise RetryExhausted(policy.attempts, last)
