"""Ingress service. Stateless, horizontally scalable, does almost nothing."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from ..config import get_settings
from ..db import pool
from ..logging import configure_logging, get_logger
from ..queue.dispatch import close_redis
from .routes import router

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.app_env != "dev")
    if settings.github_webhook_secret == "change-me" and settings.app_env != "dev":  # noqa: S105
        raise RuntimeError("GITHUB_WEBHOOK_SECRET is still the placeholder; refusing to start")
    await pool.get_pool()
    log.info("ingress.ready", env=settings.app_env)
    yield
    await close_redis()
    await pool.close_pool()


app = FastAPI(
    title="pr-sentinel ingress",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
)
app.include_router(router)
