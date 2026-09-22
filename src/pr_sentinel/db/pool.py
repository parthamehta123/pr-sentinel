"""asyncpg pool lifecycle. Raw SQL by choice — see ADR-0003."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import asyncpg

from ..config import get_settings
from ..logging import get_logger

log = get_logger(__name__)

_pool: asyncpg.Pool | None = None
_lock = asyncio.Lock()


async def _init_connection(conn: asyncpg.Connection) -> None:
    # jsonb in and out as dicts, so no call site has to remember to json.dumps.
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    await conn.set_type_codec("json", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None:
        return _pool
    async with _lock:
        if _pool is None:
            s = get_settings()
            _pool = await asyncpg.create_pool(
                dsn=s.database_url,
                min_size=s.db_pool_min,
                max_size=s.db_pool_max,
                command_timeout=s.db_timeout_s,
                init=_init_connection,
            )
            log.info("db.pool.open", min=s.db_pool_min, max=s.db_pool_max)
    assert _pool is not None
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("db.pool.closed")


async def fetch(query: str, *args: Any) -> list[asyncpg.Record]:
    pool = await get_pool()
    return await pool.fetch(query, *args)


async def fetchrow(query: str, *args: Any) -> asyncpg.Record | None:
    pool = await get_pool()
    return await pool.fetchrow(query, *args)


async def fetchval(query: str, *args: Any) -> Any:
    pool = await get_pool()
    return await pool.fetchval(query, *args)


async def execute(query: str, *args: Any) -> str:
    pool = await get_pool()
    return await pool.execute(query, *args)
