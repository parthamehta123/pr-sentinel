"""The webhook endpoint.

Shape of the handler, in order, and the order is the design:

    verify signature -> claim idempotency key -> parse -> enqueue -> 202

GitHub gives a webhook consumer a small number of seconds to acknowledge before
it records a failed delivery and retries. A full review takes minutes. So this
endpoint does the smallest possible amount of work that is still safe, and hands
everything else to the queue. That constraint, not taste, is why there is a queue
at all — see ADR-0001.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, Request, Response, status
from fastapi.responses import JSONResponse

from ..config import get_settings
from ..db.repositories import reviews as review_repo
from ..domain.models import WebhookJob
from ..logging import get_logger
from ..queue.dispatch import enqueue_review, seen_delivery
from .security import verify_signature

log = get_logger(__name__)
router = APIRouter()

# `synchronize` fires on a force-push / new commit; `reopened` on a revived PR.
REVIEWABLE_ACTIONS = {"opened", "synchronize", "reopened", "ready_for_review"}


@router.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@router.post("/webhooks/github", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    response: Response,
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> JSONResponse:
    settings = get_settings()
    body = await request.body()

    if not verify_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        # Deliberately uninformative and always 401: a forger learns nothing about
        # whether the secret, the algorithm or the encoding was the problem.
        log.warning("webhook.rejected", reason="bad_signature", delivery=x_github_delivery)
        return JSONResponse({"detail": "invalid signature"}, status_code=401)

    if not x_github_delivery:
        return JSONResponse({"detail": "missing delivery id"}, status_code=400)

    if x_github_event == "ping":
        return JSONResponse({"detail": "pong"}, status_code=200)

    if x_github_event != "pull_request":
        return JSONResponse({"detail": "ignored", "event": x_github_event}, status_code=202)

    try:
        payload = await request.json()
    except Exception:
        log.warning("webhook.bad_json", delivery=x_github_delivery)
        return JSONResponse({"detail": "malformed json body"}, status_code=400)

    if not isinstance(payload, dict):
        return JSONResponse({"detail": "malformed json body"}, status_code=400)

    action = payload.get("action", "")
    if action not in REVIEWABLE_ACTIONS:
        return JSONResponse({"detail": "ignored", "action": action}, status_code=202)

    try:
        job = _build_job(x_github_delivery, x_github_event, action, payload)
    except (KeyError, TypeError, ValueError) as exc:
        log.warning("webhook.unusable_payload", delivery=x_github_delivery, error=str(exc))
        return JSONResponse({"detail": "malformed pull_request payload"}, status_code=400)

    if payload.get("pull_request", {}).get("draft") and action != "ready_for_review":
        return JSONResponse({"detail": "ignored", "reason": "draft"}, status_code=202)

    # Two-layer dedupe: Redis for speed, Postgres for durability. Either one
    # saying "already seen" is enough to stop here.
    if await seen_delivery(job.delivery_id):
        log.info("webhook.duplicate", delivery=job.delivery_id, layer="redis")
        return JSONResponse({"detail": "duplicate", "delivery": job.delivery_id}, status_code=200)

    if not await review_repo.record_delivery(job):
        log.info("webhook.duplicate", delivery=job.delivery_id, layer="postgres")
        return JSONResponse({"detail": "duplicate", "delivery": job.delivery_id}, status_code=200)

    await enqueue_review(job)
    log.info("webhook.accepted", delivery=job.delivery_id, repo=job.repo_full_name, pr=job.pr_number)
    return JSONResponse(
        {"detail": "accepted", "delivery": job.delivery_id, "pr": job.pr_number},
        status_code=202,
    )


def _build_job(delivery: str, event: str, action: str, payload: dict) -> WebhookJob:
    pr = payload["pull_request"]
    repo = payload["repository"]
    return WebhookJob(
        delivery_id=delivery,
        event=event or "pull_request",
        action=action,
        repo_full_name=repo["full_name"],
        repo_github_id=int(repo["id"]),
        is_private=bool(repo.get("private", True)),
        pr_number=int(pr["number"]),
        head_sha=pr["head"]["sha"],
        base_sha=pr["base"]["sha"],
        title=pr.get("title") or "",
        author=(pr.get("user") or {}).get("login") or "",
    )
