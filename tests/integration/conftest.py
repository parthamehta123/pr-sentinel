from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture(scope="session", autouse=True)
def _require_services():
    if os.environ.get("SKIP_INTEGRATION"):
        pytest.skip("SKIP_INTEGRATION set")


@pytest.fixture
async def db():
    """A live connection pool, torn down after every test.

    The pool is a module-level singleton and pytest-asyncio gives each test its
    own event loop, so a pool that survives a test carries connections bound to a
    loop that no longer exists. Closing it per test costs a few milliseconds and
    removes a whole category of confusing "Event loop is closed" skips.
    """
    from pr_sentinel.db import pool

    try:
        await pool.fetchval("SELECT 1")
    except Exception as exc:
        await pool.close_pool()
        pytest.skip(f"postgres unavailable — run `make up migrate` ({exc})")
    try:
        yield pool
    finally:
        await pool.close_pool()
