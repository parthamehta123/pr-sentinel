"""ARQ worker. One job type: review a pull request.

Separate process from ingress on purpose. Ingress must answer in milliseconds
under any load; a review takes minutes and is allowed to. Scaling them together
would mean sizing the webhook endpoint for LLM latency.
"""

from __future__ import annotations

from typing import Any, ClassVar

from ..config import get_settings
from ..db import pool
from ..domain.models import WebhookJob
from ..logging import configure_logging, get_logger
from ..orchestration.pipeline import review_pull_request
from .dispatch import redis_settings

log = get_logger(__name__)


async def run_review(ctx: dict, payload: dict) -> dict[str, Any]:
    job = WebhookJob.model_validate(payload)
    log.info("worker.start", delivery=job.delivery_id, repo=job.repo_full_name, pr=job.pr_number)
    outcome = await review_pull_request(job)
    log.info(
        "worker.done",
        delivery=job.delivery_id,
        decision=str(outcome.decision),
        findings=len(outcome.findings),
        confidence=round(outcome.overall_confidence, 3),
        cost_usd=round(outcome.total_cost_usd, 4),
    )
    return {
        "review_id": str(outcome.review_id),
        "decision": str(outcome.decision),
        "findings": len(outcome.findings),
        "confidence": outcome.overall_confidence,
        "cost_usd": outcome.total_cost_usd,
    }


async def startup(ctx: dict) -> None:
    settings = get_settings()
    configure_logging(settings.log_level, json_logs=settings.app_env != "dev")
    await pool.get_pool()
    log.info("worker.ready")


async def shutdown(ctx: dict) -> None:
    await pool.close_pool()


class WorkerSettings:
    # arq reads these as class-level configuration; the list is part of its contract.
    functions: ClassVar[list] = [run_review]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = redis_settings()
    max_jobs = 4
    job_timeout = 900
    # A review that already died twice will die again; the third attempt just
    # burns tokens. Failures land in the HITL queue instead.
    max_tries = 3
    keep_result = 3600
