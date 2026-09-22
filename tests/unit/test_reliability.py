"""Retry, circuit breaking and the cost guard."""

from __future__ import annotations

import asyncio

import pytest

from pr_sentinel.reliability.budget import BudgetExceeded, BudgetGuard
from pr_sentinel.reliability.circuit import CircuitBreaker, CircuitOpen, State
from pr_sentinel.reliability.retry import RetryExhausted, RetryPolicy, with_retry


class Boom(Exception):
    pass


class Fatal(Exception):
    pass


async def test_a_transient_failure_is_retried_then_succeeds():
    calls = 0

    async def flaky():
        nonlocal calls
        calls += 1
        if calls < 3:
            raise Boom("not yet")
        return "ok"

    result = await with_retry(flaky, RetryPolicy(attempts=5, base_delay=0), name="t")
    assert result == "ok" and calls == 3


async def test_exhausting_attempts_raises_with_the_original_cause():
    async def always():
        raise Boom("down")

    with pytest.raises(RetryExhausted) as exc:
        await with_retry(always, RetryPolicy(attempts=3, base_delay=0), name="t")
    assert exc.value.attempts == 3
    assert isinstance(exc.value.last, Boom)


async def test_non_retryable_errors_are_not_retried():
    calls = 0

    async def fatal():
        nonlocal calls
        calls += 1
        raise Fatal("400 bad request")

    with pytest.raises(Fatal):
        await with_retry(fatal, RetryPolicy(attempts=5, base_delay=0, retry_on=(Boom,)), name="t")
    assert calls == 1


async def test_give_up_on_beats_retry_on():
    calls = 0

    async def refused():
        nonlocal calls
        calls += 1
        raise Fatal("refusal")

    policy = RetryPolicy(attempts=5, base_delay=0, retry_on=(Exception,), give_up_on=(Fatal,))
    with pytest.raises(Fatal):
        await with_retry(refused, policy, name="t")
    assert calls == 1


async def test_a_server_supplied_delay_is_preferred_over_our_curve():
    slept: list[float] = []
    real_sleep = asyncio.sleep

    async def fake_sleep(d):
        slept.append(d)
        await real_sleep(0)

    calls = 0

    async def rate_limited():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Boom("429")
        return "ok"

    import pr_sentinel.reliability.retry as retry_mod

    retry_mod.asyncio.sleep = fake_sleep  # type: ignore[assignment]
    try:
        await with_retry(
            rate_limited, RetryPolicy(attempts=3, base_delay=99), name="t", retry_after=lambda exc: 0.25
        )
    finally:
        retry_mod.asyncio.sleep = real_sleep  # type: ignore[assignment]
    assert slept == [0.25]


async def test_cancellation_is_never_swallowed_by_retry():
    async def cancelled():
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await with_retry(cancelled, RetryPolicy(attempts=3, base_delay=0), name="t")


async def test_the_breaker_opens_after_repeated_failures_and_stops_calling():
    cb = CircuitBreaker("dep", failure_threshold=3, recovery_time=60)
    calls = 0

    async def failing():
        nonlocal calls
        calls += 1
        raise Boom("down")

    for _ in range(3):
        with pytest.raises(Boom):
            await cb.call(failing)
    assert cb.state is State.OPEN

    with pytest.raises(CircuitOpen):
        await cb.call(failing)
    assert calls == 3  # the fourth call never reached the dependency


async def test_the_breaker_probes_after_the_recovery_window_and_closes_on_success():
    cb = CircuitBreaker("dep", failure_threshold=1, recovery_time=0.01, success_threshold=1)

    async def failing():
        raise Boom("down")

    with pytest.raises(Boom):
        await cb.call(failing)
    assert cb.state is State.OPEN

    await asyncio.sleep(0.02)
    assert cb.state is State.HALF_OPEN
    assert await cb.call(lambda: asyncio.sleep(0, result="ok")) == "ok"
    assert cb.state is State.CLOSED


async def test_a_failure_while_probing_reopens_immediately():
    cb = CircuitBreaker("dep", failure_threshold=1, recovery_time=0.01, success_threshold=2)

    async def failing():
        raise Boom("down")

    with pytest.raises(Boom):
        await cb.call(failing)
    await asyncio.sleep(0.02)
    assert cb.state is State.HALF_OPEN
    with pytest.raises(Boom):
        await cb.call(failing)
    assert cb.state is State.OPEN


def test_the_review_cap_stops_a_single_runaway_pull_request(monkeypatch):
    import uuid

    from pr_sentinel.config import get_settings

    guard = BudgetGuard(review_id=uuid.uuid4(), day_spent=0.0)
    cap = get_settings().review_cost_cap_usd
    guard.charge(cap - 0.01)
    guard.check(0.005)
    with pytest.raises(BudgetExceeded) as exc:
        guard.check(0.5)
    assert exc.value.scope == "review"


def test_the_daily_cap_stops_a_webhook_storm():
    import uuid

    from pr_sentinel.config import get_settings

    cap = get_settings().daily_cost_cap_usd
    guard = BudgetGuard(review_id=uuid.uuid4(), day_spent=cap)
    with pytest.raises(BudgetExceeded) as exc:
        guard.check(0.01)
    assert exc.value.scope == "daily"


def test_the_guard_can_be_disabled_for_offline_runs():
    import uuid

    guard = BudgetGuard(review_id=uuid.uuid4(), day_spent=10_000, enabled=False)
    guard.check(10_000)  # does not raise
