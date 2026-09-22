"""Redis side of ingress: the fast dedupe check and the enqueue."""

from __future__ import annotations

import asyncio
from typing import Any

from arq import create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis

from ..config import get_settings
from ..domain.models import WebhookJob
from ..logging import get_logger

log = get_logger(__name__)

_arq: Any = None
_redis: Redis | None = None
_lock = asyncio.Lock()

# A GitHub redelivery arrives within minutes; a week of keys is far more than
# enough and bounds the memory this costs.
DEDUPE_TTL_SECONDS = 7 * 24 * 3600


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


async def get_redis() -> Redis:
    global _redis
    if _redis is None:
        async with _lock:
            if _redis is None:
                _redis = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _redis


async def get_arq() -> Any:
    global _arq
    if _arq is None:
        async with _lock:
            if _arq is None:
                _arq = await create_pool(redis_settings())
    return _arq


async def seen_delivery(delivery_id: str) -> bool:
    """SET NX is the whole mechanism: the first caller claims the key and wins."""
    redis = await get_redis()
    claimed = await redis.set(f"sentinel:delivery:{delivery_id}", "1", nx=True, ex=DEDUPE_TTL_SECONDS)
    return not claimed


async def enqueue_review(job: WebhookJob) -> str | None:
    arq = await get_arq()
    # _job_id makes the enqueue itself idempotent: arq drops a second job with an
    # id it has already seen, so a Redis-layer race cannot produce two reviews.
    result = await arq.enqueue_job(
        "run_review", job.model_dump(mode="json"), _job_id=f"review:{job.delivery_id}"
    )
    return result.job_id if result else None


async def queue_depth() -> int:
    redis = await get_redis()
    return int(await redis.zcard("arq:queue") or 0)


async def close_redis() -> None:
    global _redis, _arq
    if _redis is not None:
        await _redis.aclose()
        _redis = None
    if _arq is not None:
        await _arq.aclose()
        _arq = None
